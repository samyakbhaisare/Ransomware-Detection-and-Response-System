from __future__ import annotations

import csv
import json
from copy import deepcopy
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
import uuid

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .config import ConfigConflictError
from .dashboard_data import DashboardData
from .logging import initialize_logging
from .monitoring import DirectoryMonitor
from .runtime import RuntimeManager, RuntimeStatus
from .storage import DatabaseManager
from .public_redaction import redact


runtime_manager = RuntimeManager()


@asynccontextmanager
async def lifespan(application: FastAPI):
    runtime = get_runtime()
    runtime.start()
    yield
    try:
        get_runtime().stop()
    except RuntimeError:
        get_runtime().force_stop()


app = FastAPI(
    title="RDRS API",
    version="1.0.0",
    description=(
        "Ransomware Detection and Response System "
        "defensive monitoring API."
    ),
    lifespan=lifespan,
)

DASHBOARD_FILE = (
    Path(__file__).resolve().parent
    / "dashboard"
    / "index.html"
)


class SettingsUpdate(BaseModel):
    name: str | None = None
    environment: str | None = None
    shutdown_timeout: float | None = None
    log_level: str | None = None
    persistence_enabled: bool | None = None


def get_runtime() -> RuntimeManager:
    """Return the live runtime singleton.

    This deliberately resolves through ``RuntimeManager()`` rather than
    returning the module-level ``runtime_manager`` object. ``RuntimeManager`` is
    a singleton, so ``RuntimeManager()`` returns the current instance -- whereas
    the module-level reference can go stale after
    ``RuntimeManager.reset_singleton()``. A stale reference still "works", but
    its pending configuration path may point at a different config file, so a
    request could read or *write* the wrong configuration.
    """
    return RuntimeManager()


def get_database() -> DatabaseManager:
    """Return the runtime database, resolving settings on a cold process.

    The engine is initialized once and reused for subsequent requests so that
    per-request connections are not leaked (previously this produced repeated
    ``ResourceWarning: unclosed database``).
    """
    runtime = get_runtime()

    if runtime._database is None:
        runtime._apply_runtime_settings(
            config_path=runtime._pending_config_path,
            data_dir=runtime._pending_data_dir,
            logs_dir=runtime._pending_logs_dir,
            db_path=runtime._pending_db_path,
        )

    if runtime._database is None:
        raise RuntimeError(
            "Database is not available."
        )

    if runtime._database.engine is None:
        runtime._database.initialize()

    return runtime._database


# NOTE: a per-request `close_if_stopped()` helper used to live here. It
# disposed the shared SQLite engine from read endpoints, which raced with
# concurrent reads and produced HTTP 500 (`Database not initialized`) while
# monitoring was stopped. Read endpoints must never close the shared
# database; the FastAPI lifespan owns startup/shutdown cleanup instead.


#: Host names a request may originate from. The dashboard is served by this
#: same process, so a legitimate browser request is always same-origin.
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


def require_local_origin(request: Request) -> None:
    """Reject state-changing requests that carry a foreign ``Origin``.

    RDRS listens on loopback, but a page on any website can still make the
    operator's browser send a cross-origin POST to ``http://127.0.0.1:8000``
    (a "localhost CSRF"). The browser attaches ``Origin`` to such a request, so
    refusing a non-local origin closes it.

    Deliberately permissive where it is safe to be:

    * a request with **no** ``Origin`` header is allowed -- that is a non-browser
      client (curl, the CLI, the test client), which a hostile web page cannot
      forge;
    * any loopback host is accepted, so the dashboard keeps working whether it
      was opened as ``localhost`` or ``127.0.0.1``.

    Only mutating endpoints call this; every ``GET`` is unaffected.
    """
    origin = request.headers.get("origin")

    if not origin:
        return

    from urllib.parse import urlparse

    try:
        host = urlparse(origin).hostname
    except ValueError:
        host = None

    if host is not None and host.lower() in _LOCAL_HOSTS:
        return

    raise HTTPException(
        status_code=403,
        detail=(
            "Cross-origin requests are not allowed on this endpoint."
        ),
    )


def report_directory(runtime: RuntimeManager) -> Path:
    """Resolve the directory generated reports are written to.

    The data directory is taken from the resolved configuration when
    available, then from the pending runtime settings. A ``None`` value can
    never reach ``Path()``: when neither is set the documented default
    ``data`` directory is used, so ``POST /reports/generate`` cannot fail
    with a ``TypeError`` on a cold process.
    """
    config = getattr(runtime, "_config", None)

    data_dir: Any = None

    if config is not None:
        data_dir = getattr(
            config,
            "resolved_data_dir",
            getattr(config, "data_dir", None),
        )

    if data_dir is None:
        data_dir = getattr(
            runtime,
            "_pending_data_dir",
            None,
        )

    if data_dir is None:
        data_dir = Path("data")

    data_root = Path(data_dir).resolve()
    reports_dir = (data_root / "reports").resolve()

    if not _is_path_within(reports_dir, data_root):
        raise RuntimeError(
            "The reports directory must remain inside the configured data directory."
        )

    reports_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    return reports_dir


def _is_path_within(path: Path, directory: Path) -> bool:
    """Return whether a resolved path is contained beneath a resolved root."""
    try:
        return Path(path).resolve().is_relative_to(Path(directory).resolve())
    except (OSError, RuntimeError):
        return False


def utc_timestamp() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def _first_not_none(*values: Any) -> Any:
    """Return the first argument that is not None (0 and [] are kept)."""
    for value in values:
        if value is not None:
            return value

    return None


def _decode_list_cursor(
    value: str | None,
) -> tuple[datetime, int] | None:
    """Decode the additive timestamp/id cursor used by paged list routes."""
    if value is None:
        return None

    try:
        timestamp_text, id_text = value.rsplit("|", 1)
        timestamp_text = (
            timestamp_text[:-1] + "+00:00"
            if timestamp_text.endswith("Z")
            else timestamp_text
        )
        timestamp = datetime.fromisoformat(timestamp_text)
        record_id = int(id_text)
        if record_id < 1:
            raise ValueError("cursor id must be positive")
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return timestamp.astimezone(timezone.utc), record_id
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail="Invalid pagination cursor.",
        ) from exc


def _paginate_rows(
    response: Response,
    rows: list[dict[str, Any]],
    *,
    limit: int,
    timestamp_key: str,
    total_count: int | None = None,
) -> list[dict[str, Any]]:
    """Keep list bodies backward-compatible while exposing keyset metadata."""
    page = rows[:limit]
    has_more = len(rows) > limit
    response.headers["X-Has-More"] = "true" if has_more else "false"

    if total_count is not None:
        response.headers["X-Total-Count"] = str(total_count)

    if has_more and page:
        last = page[-1]
        timestamp = last.get(timestamp_key)
        record_id = last.get("id")
        if timestamp is not None and record_id is not None:
            response.headers["X-Next-Cursor"] = (
                f"{timestamp}|{int(record_id)}"
            )

    return page


#: Placeholder values written into the dedicated incident columns by the
#: schema migration for rows that predate them. A row carrying one of these
#: tells us nothing, so the report is allowed to fall back to the evidence
#: payload (and to derive the level from the score) instead.
_SCORE_PLACEHOLDER = 0.0

_LEVEL_PLACEHOLDERS = frozenset(
    {
        "",
        "NORMAL",
        "UNKNOWN",
    }
)


def _resolve_score(
    incident: dict[str, Any],
    evidence: dict[str, Any],
) -> float:
    """Resolve the incident's threat score from the columns or the evidence.

    The dedicated column wins whenever it carries a real value. A migrated
    legacy row carries the ``0.0`` placeholder, which is *not* a real score,
    so the evidence payload is consulted before the placeholder is accepted.
    """
    column_value = incident.get("threat_score")

    if column_value is not None and float(
        column_value
    ) != _SCORE_PLACEHOLDER:
        return float(column_value)

    evidence_value = _first_not_none(
        evidence.get("score"),
        incident.get("score"),
    )

    if evidence_value is None:
        return float(column_value or _SCORE_PLACEHOLDER)

    return float(evidence_value)


def _resolve_level(
    incident: dict[str, Any],
    evidence: dict[str, Any],
    score: float,
) -> str:
    """Resolve the incident's threat level, deriving it when it is unknown.

    Legacy rows default to ``"NORMAL"`` regardless of their real score, which
    would silently downgrade a critical incident in the generated report. The
    level is therefore derived from the resolved score whenever the column
    only holds a placeholder (or the evidence payload disagrees with it).
    """
    column_value = incident.get("threat_level")

    normalised_column = (
        str(column_value).strip().upper()
        if column_value is not None
        else ""
    )

    evidence_value = _first_not_none(
        evidence.get("level"),
        incident.get("level"),
    )

    normalised_evidence = (
        str(evidence_value).strip().upper()
        if evidence_value is not None
        else ""
    )

    if (
        normalised_column in _LEVEL_PLACEHOLDERS
        and normalised_evidence
        not in _LEVEL_PLACEHOLDERS
    ):
        return str(evidence_value)

    if normalised_column not in _LEVEL_PLACEHOLDERS:
        return str(column_value)

    if normalised_evidence not in _LEVEL_PLACEHOLDERS:
        return str(evidence_value)

    # Nothing usable was recorded - derive the level from the real score.
    if score >= 70:
        return "Critical"

    if score >= 40:
        return "Warning"

    return "Normal"


def build_incident_report(
    database: DatabaseManager,
    incident: dict[str, Any],
) -> dict[str, Any]:
    """Build a Phase 8 incident report from real persisted data.

    Score, threat level, suspect process and affected files are read from the
    incident's dedicated columns first (with a tolerant fallback to the
    evidence payload for rows written by older versions). The timeline is
    strictly scoped to events attributed to this incident so that unrelated
    activity from other incidents never leaks into the report.
    """
    incident_id = _first_not_none(
        incident.get("id"),
        incident.get("incident_id"),
    )

    evidence = incident.get("evidence") or {}

    if not isinstance(evidence, dict):
        evidence = {}

    # --- incident-scoped timeline -------------------------------------------
    incident_events: list[dict[str, Any]] = []

    if incident_id is not None:
        event_cursor: tuple[datetime, int] | None = None
        while True:
            event_page = database.list_events_for_incident(
                incident_id,
                limit=1000,
                after=event_cursor,
            )
            incident_events.extend(event_page)

            if len(event_page) < 1000:
                break

            last_event = event_page[-1]
            timestamp = last_event.get("created_at")
            event_id = last_event.get("id")
            if timestamp is None or event_id is None:
                break

            timestamp_text = str(timestamp)
            if timestamp_text.endswith("Z"):
                timestamp_text = timestamp_text[:-1] + "+00:00"
            event_time = datetime.fromisoformat(timestamp_text)
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)
            event_cursor = (event_time, int(event_id))

    # --- score / level ------------------------------------------------------
    score = _resolve_score(incident, evidence)
    level = _resolve_level(incident, evidence, score)

    # --- affected files -----------------------------------------------------
    # The dedicated column wins whenever it holds a real list. A migrated
    # legacy row carries the ``[]`` placeholder, so the evidence payload is
    # consulted before that placeholder is accepted as "no files".
    affected_files = incident.get("affected_files")

    if not affected_files:
        affected_files = _first_not_none(
            evidence.get("affected_files"),
            affected_files,
        ) or []

    if isinstance(affected_files, str):
        try:
            decoded = json.loads(affected_files)
        except json.JSONDecodeError:
            decoded = [affected_files]

        affected_files = decoded

    if not isinstance(affected_files, list):
        affected_files = [affected_files]

    # --- suspect process ----------------------------------------------------
    # Same placeholder rule: ``{}`` means "nothing was recorded here".
    suspect_process = incident.get("suspect_process")

    if not suspect_process:
        suspect_process = _first_not_none(
            evidence.get("suspect_process"),
            suspect_process,
        )

    if not suspect_process:
        suspect_process = None

    suspect_process_attribution = evidence.get(
        "suspect_process_attribution"
    )

    # --- alerts / scores referenced by this incident ------------------------
    alerts = database.list_alerts(limit=1000)
    scores = database.list_scores(limit=1000)

    incident_alert_id = incident.get("alert_id")
    incident_score_id = incident.get("score_id")

    if incident_alert_id is not None:
        alerts = [
            alert
            for alert in alerts
            if alert.get("id") == incident_alert_id
        ]

    if incident_score_id is not None:
        scores = [
            score_row
            for score_row in scores
            if score_row.get("id") == incident_score_id
        ]

    timeline = []

    for event in incident_events:
        timeline.append(
            {
                "timestamp": (
                    event.get("timestamp")
                    or event.get("created_at")
                ),
                "action": (
                    event.get("action")
                    or event.get("event_type")
                ),
                "path": event.get("path"),
                "metadata": event.get(
                    "metadata",
                    {},
                ),
            }
        )

    # --- recommendations derived from the actual signals --------------------
    recommendations = _build_recommendations(
        score=float(score),
        signals=evidence.get("signals") or {},
        suspect_process=suspect_process,
        suspect_process_attribution=suspect_process_attribution,
        affected_files=affected_files,
        simulation_mode=evidence.get(
            "simulation_mode",
            True,
        ),
    )

    return {
        "report_version": "1.0",
        "generated_at": utc_timestamp(),
        "incident": {
            "id": incident_id,
            "created_at": incident.get(
                "created_at"
            ),
            "status": incident.get("status"),
            "severity": incident.get(
                "severity"
            ),
            "score": float(score),
            "level": level,
            "description": incident.get(
                "description"
            ),
        },
        "timeline": timeline,
        "threat_score": float(score),
        "threat_level": level,
        "affected_files": affected_files,
        "affected_file_count": len(
            affected_files
        ),
        "suspect_process": suspect_process,
        "suspect_process_attribution": (
            suspect_process_attribution
        ),
        "evidence": evidence,
        "alerts": alerts,
        "scores": scores,
        "recommendations": recommendations,
    }


def _build_recommendations(
    *,
    score: float,
    signals: dict[str, Any],
    suspect_process: Any,
    suspect_process_attribution: str | None = None,
    affected_files: list[Any],
    simulation_mode: bool,
) -> list[str]:
    """Derive report recommendations from the incident's real signals."""
    fired = sorted(
        name
        for name, value in (signals or {}).items()
        if value
    )

    recommendations: list[str] = []

    if "rapid_encryption" in fired:
        recommendations.append(
            "Mass file modification consistent with rapid "
            "encryption was observed - isolate the affected "
            "host from the network before restoring data."
        )

    if "mass_rename" in fired:
        recommendations.append(
            "A mass rename/extension-change burst was observed - "
            "compare the affected files against the last known "
            "good backup."
        )

    if "high_entropy" in fired:
        recommendations.append(
            "Files were rewritten with high-entropy content "
            "(consistent with encryption) - preserve the "
            "quarantined evidence before any recovery attempt."
        )

    if "cpu_spike" in fired:
        recommendations.append(
            "An abnormal CPU spike accompanied the burst - "
            "correlate the suspect process with endpoint "
            "telemetry."
        )

    if "unknown_program" in fired:
        recommendations.append(
            "An unexpected program was associated with the "
            "activity - verify its executable path and signature "
            "before re-enabling it."
        )

    if suspect_process:
        name = (
            suspect_process.get("name")
            if isinstance(suspect_process, dict)
            else suspect_process
        )
        recommendations.append(
            f"Review the recorded process observation: {name}. "
            + (
                f"Attribution: {suspect_process_attribution}."
                if suspect_process_attribution
                else "Its causal relationship to file activity is not recorded."
            )
        )

    recommendations.append(
        f"Review the {len(affected_files)} affected file(s) and "
        "confirm they match the quarantined copies."
    )

    recommendations.append(
        "Preserve the generated incident report and the audit "
        "trail as evidence."
    )

    if simulation_mode:
        recommendations.append(
            "Automated response ran in simulation mode - no "
            "processes were terminated and no originals were "
            "modified."
        )
    else:
        recommendations.append(
            "Automated response was NOT in simulation mode - "
            "confirm every action taken on the host."
        )

    recommendations.append(
        f"Highest recorded threat score for this incident: "
        f"{int(round(score))}/100."
    )

    return recommendations


def write_json_report(
    report: dict[str, Any],
    destination: Path,
) -> None:
    with destination.open(
        "x",
        encoding="utf-8",
    ) as handle:
        json.dump(
            report,
            handle,
            indent=2,
            ensure_ascii=False,
            default=str,
        )


def write_csv_report(
    report: dict[str, Any],
    destination: Path,
) -> None:
    rows: list[dict[str, Any]] = []

    incident = report["incident"]

    rows.append(
        {
            "record_type": "incident",
            "timestamp": incident.get(
                "created_at"
            ),
            "incident_id": incident.get(
                "id"
            ),
            "action": "",
            "path": "",
            "score": incident.get(
                "score"
            ),
            "level": incident.get(
                "level"
            ),
            "details": incident.get(
                "description"
            ),
        }
    )

    for item in report["timeline"]:
        rows.append(
            {
                "record_type": "event",
                "timestamp": item.get(
                    "timestamp"
                ),
                "incident_id": incident.get(
                    "id"
                ),
                "action": item.get(
                    "action"
                ),
                "path": item.get(
                    "path"
                ),
                "score": report[
                    "threat_score"
                ],
                "level": incident.get(
                    "level"
                ),
                "details": json.dumps(
                    item.get(
                        "metadata",
                        {},
                    ),
                    ensure_ascii=False,
                    default=str,
                ),
            }
        )

    for file_path in report[
        "affected_files"
    ]:
        rows.append(
            {
                "record_type": "affected_file",
                "timestamp": "",
                "incident_id": incident.get(
                    "id"
                ),
                "action": "",
                "path": file_path,
                "score": report[
                    "threat_score"
                ],
                "level": incident.get(
                    "level"
                ),
                "details": "",
            }
        )

    suspect_process = report.get("suspect_process")
    if suspect_process:
        process_data = (
            suspect_process
            if isinstance(suspect_process, dict)
            else {"name": str(suspect_process)}
        )
        rows.append(
            {
                "record_type": "suspect_process",
                "timestamp": incident.get("created_at"),
                "incident_id": incident.get("id"),
                "action": "",
                "path": process_data.get("executable_path", ""),
                "score": report["threat_score"],
                "level": incident.get("level"),
                "details": json.dumps(
                    {
                        "process": process_data,
                        "attribution": report.get(
                            "suspect_process_attribution"
                        ),
                    },
                    ensure_ascii=False,
                    default=str,
                ),
            }
        )

    for recommendation in report[
        "recommendations"
    ]:
        rows.append(
            {
                "record_type": "recommendation",
                "timestamp": "",
                "incident_id": incident.get(
                    "id"
                ),
                "action": "",
                "path": "",
                "score": report[
                    "threat_score"
                ],
                "level": incident.get(
                    "level"
                ),
                "details": recommendation,
            }
        )

    fieldnames = [
        "record_type",
        "timestamp",
        "incident_id",
        "action",
        "path",
        "score",
        "level",
        "details",
    ]

    with destination.open(
        "x",
        newline="",
        encoding="utf-8",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
        )
        writer.writeheader()
        writer.writerows(rows)


#: How many generated report files to keep per incident. Each generation writes
#: a new timestamped file, so without a cap the reports directory grows without
#: bound. The newest file of each format is always retained.
REPORT_RETENTION_PER_INCIDENT = 20


class RetentionResult(dict):
    """Outcome of one retention pass: ``removed`` / ``blocked`` / ``kept``.

    A plain ``dict`` subclass so callers and tests can read it like a mapping
    without the API surface of a full class.
    """

    @property
    def removed(self) -> list[Path]:
        return self["removed"]

    @property
    def blocked(self) -> list[Path]:
        return self["blocked"]


def prune_reports(
    directory: Path,
    incident_id: int,
    keep: int = REPORT_RETENTION_PER_INCIDENT,
) -> RetentionResult:
    """Delete the oldest generated reports for one incident.

    Safety rules, so evidence is never destroyed by housekeeping:

    * only files matching ``incident_<id>_<stamp>.<ext>`` for this incident are
      considered -- nothing else in the directory is touched;
    * the ``keep`` most recent files are always retained;
    * the newest ``.json`` and the newest ``.csv`` are retained even if they
      fall outside that window;
    * **any** failure to delete is recorded and skipped. Cleanup must never
      break report generation, and it must never raise. The original code
      caught only ``OSError``, so a delete guard raising anything else escaped
      and turned ``POST /reports/generate`` into a 500.

    Returns a :class:`RetentionResult` with the paths removed and the paths that
    could not be removed.
    """
    result = RetentionResult(removed=[], blocked=[], kept=[], considered=0)

    if keep < 2:
        keep = 2

    try:
        candidates = sorted(
            (
                path
                for path in directory.glob(f"incident_{incident_id}_*")
                if not path.is_symlink()
                if path.is_file()
                and path.suffix in {".json", ".csv"}
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except Exception:
        # A directory that cannot be listed is not a reason to fail the
        # generation that just succeeded.
        return result

    result["considered"] = len(candidates)

    if len(candidates) <= keep:
        result["kept"] = [path.name for path in candidates]
        return result

    protected: set[Path] = set()

    for suffix in (".json", ".csv"):
        newest = next(
            (path for path in candidates if path.suffix == suffix),
            None,
        )

        if newest is not None:
            protected.add(newest)

    retained = set(candidates[:keep]) | protected

    for path in candidates:
        if path in retained:
            result["kept"].append(path.name)
            continue

        try:
            path.unlink()
        except BaseException:
            # Deliberately broad: a deletion guard, a permission error or a
            # filesystem quirk must not propagate into report generation.
            result["blocked"].append(path.name)
            continue

        # Verify the filesystem actually changed rather than trusting that
        # unlink() returned without raising.
        if path.exists():
            result["blocked"].append(path.name)
        else:
            result["removed"].append(path)

    return result

    return removed


def _report_media_type(format: str) -> str:
    return (
        "application/json"
        if format == "json"
        else "text/csv"
    )


@app.get(
    "/",
    include_in_schema=False,
)
def dashboard() -> FileResponse:
    if not DASHBOARD_FILE.is_file():
        raise HTTPException(
            status_code=500,
            detail="Dashboard file is missing.",
        )

    # The dashboard is a single self-contained page whose JavaScript changes
    # whenever the UI is fixed. Without an explicit Cache-Control the browser
    # falls back to heuristic caching and can keep serving the previous page
    # from its disk cache, so a fix appears not to have taken effect even after
    # a normal refresh. `no-store` guarantees the current page is always used;
    # the payload is ~100 KB and served from localhost, so the cost is nil.
    return FileResponse(
        DASHBOARD_FILE,
        headers={
            "Cache-Control": "no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/health")
def health() -> dict[str, Any]:
    return get_runtime().health_status()


@app.middleware("http")
async def redact_public_reads(request: Request, call_next):
    if request.headers.get("x-rdrs-public") == "1" and request.method not in {"GET", "HEAD"}:
        return Response('{"detail":"Public access is read-only"}', status_code=403, media_type="application/json")
    response = await call_next(request)
    if request.headers.get("x-rdrs-public") != "1":
        return response
    if request.url.path in {"/", "/health", "/public-summary"}:
        return response
    if request.url.path.startswith("/reports/") and response.status_code == 200:
        return response
    if response.headers.get("content-type", "").startswith("application/json"):
        body = b"".join([chunk async for chunk in response.body_iterator])
        payload = redact(json.loads(body)) if response.status_code == 200 else {"detail": "Read request unavailable"}
        headers = {k: v for k, v in response.headers.items() if k.lower() not in {"content-length", "content-type"}}
        headers["Cache-Control"] = "no-store"
        return Response(json.dumps(payload), status_code=response.status_code, headers=headers, media_type="application/json")
    return response


@app.get("/status")
def status() -> dict[str, Any]:
    return get_runtime().status()


@app.get("/public-summary")
def public_summary(request: Request) -> dict[str, Any]:
    """Allowlisted aggregates only; never serialize historical row details."""
    from sqlalchemy import text

    runtime = get_runtime()
    database = get_database()
    groups = {}
    allowed = {
        "events": ("action", {"created", "modified", "deleted", "moved", "renamed"}),
        "alerts": ("severity", {"NORMAL", "WARNING", "CRITICAL"}),
        "incidents": ("severity", {"NORMAL", "WARNING", "CRITICAL"}),
    }
    with database.engine.connect() as connection:
        for table, (column, labels) in allowed.items():
            totals = {}
            for label, count in connection.execute(
                text(f"SELECT {column}, count(*) FROM {table} GROUP BY {column}")
            ):
                label = label if label in labels else "Other"
                totals[label] = totals.get(label, 0) + count
            groups[table] = totals
        latest_capture = connection.execute(
            text("SELECT captured_at FROM processes ORDER BY captured_at DESC, id DESC LIMIT 1")
        ).scalar()
        latest_score = connection.execute(
            text("SELECT score, level, created_at FROM scores ORDER BY created_at DESC, id DESC LIMIT 1")
        ).first()
        # Chart inputs contain only numerical observations and timestamps.
        cpu = connection.execute(text(
            "SELECT cpu_percent FROM processes ORDER BY captured_at DESC, id DESC LIMIT 60"
        )).scalars().all()
        entropy = []
        activity = {}
        for timestamp, metadata in connection.execute(text(
            "SELECT created_at, metadata_json FROM events ORDER BY created_at DESC, id DESC LIMIT 50"
        )):
            second = str(timestamp)[:19]
            activity[second] = activity.get(second, 0) + 1
            try:
                value = json.loads(metadata).get("entropy")
                if isinstance(value, (int, float)) and 0 <= value <= 8:
                    entropy.append(float(value))
            except (ValueError, TypeError, AttributeError):
                pass
    reports = report_directory(runtime)
    return {
        # The gateway forces this header. It cannot grant access to private APIs.
        "access": "public" if request.headers.get("x-rdrs-public") == "1" else "private",
        "health": runtime.health_status(),
        "summary": DashboardData(database).summary(),
        "groups": groups,
        "latest_capture": str(latest_capture) if latest_capture is not None else None,
        "score": {
            "score": float(latest_score[0]),
            "level": latest_score[1] if latest_score[1] in {"NORMAL", "WARNING", "CRITICAL"} else "Restricted",
            "created_at": str(latest_score[2]).replace(" ", "T") + "Z",
        } if latest_score else None,
        "telemetry": {"cpu": cpu, "entropy": list(reversed(entropy))},
        "activity": [{"time": stamp.replace(" ", "T") + "Z", "count": count} for stamp, count in sorted(activity.items())],
        "report_count": sum(
            1 for path in reports.glob("incident_*.*")
            if path.suffix in {".json", ".csv"} and path.is_file() and not path.is_symlink()
        ),
    }


@app.post("/monitor/start")
def start_monitoring(
    request: Request,
) -> dict[str, Any]:
    require_local_origin(request)
    runtime = get_runtime()

    try:
        return runtime.start()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc


@app.post("/monitor/stop")
def stop_monitoring(
    request: Request,
) -> dict[str, Any]:
    require_local_origin(request)
    runtime = get_runtime()

    try:
        return runtime.stop()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc


@app.get("/events")
def events(
    response: Response,
    limit: int = Query(
        100,
        ge=1,
        le=1000,
    ),
    cursor: str | None = None,
    include_total: bool = False,
) -> list[dict[str, Any]]:
    database = get_database()
    decoded_cursor = _decode_list_cursor(cursor)
    rows = database.list_events(
        limit=limit + 1,
        cursor=decoded_cursor,
    )

    return _paginate_rows(
        response,
        rows,
        limit=limit,
        timestamp_key="created_at",
        total_count=(
            database.count_records("events")
            if include_total
            else None
        ),
    )


@app.get("/processes")
def processes(
    response: Response,
    limit: int = Query(
        100,
        ge=1,
        le=1000,
    ),
    cursor: str | None = None,
    include_total: bool = False,
) -> list[dict[str, Any]]:
    database = get_database()
    decoded_cursor = _decode_list_cursor(cursor)
    rows = DashboardData(database).recent_processes(
        limit=limit + 1,
        cursor=decoded_cursor,
    )

    return _paginate_rows(
        response,
        rows,
        limit=limit,
        timestamp_key="captured_at",
        total_count=(
            database.count_records("processes")
            if include_total
            else None
        ),
    )


@app.get("/alerts")
def alerts(
    response: Response,
    limit: int = Query(
        100,
        ge=1,
        le=1000,
    ),
    cursor: str | None = None,
    include_total: bool = False,
) -> list[dict[str, Any]]:
    database = get_database()
    decoded_cursor = _decode_list_cursor(cursor)
    rows = database.list_alerts(
        limit=limit + 1,
        cursor=decoded_cursor,
    )

    return _paginate_rows(
        response,
        rows,
        limit=limit,
        timestamp_key="created_at",
        total_count=(
            database.count_records("alerts")
            if include_total
            else None
        ),
    )


@app.get("/incidents")
def incidents(
    response: Response,
    limit: int = Query(
        100,
        ge=1,
        le=1000,
    ),
    cursor: str | None = None,
    include_total: bool = False,
) -> list[dict[str, Any]]:
    database = get_database()
    decoded_cursor = _decode_list_cursor(cursor)
    rows = database.list_incidents(
        limit=limit + 1,
        cursor=decoded_cursor,
    )

    return _paginate_rows(
        response,
        rows,
        limit=limit,
        timestamp_key="created_at",
        total_count=(
            database.count_records("incidents")
            if include_total
            else None
        ),
    )


@app.get("/dashboard-data")
def dashboard_data() -> dict[str, Any]:
    database = get_database()

    return DashboardData(
        database
    ).complete()


@app.get("/scan")
def scan_information() -> dict[str, str]:
    return {
        "message": (
            "Use POST /scan with a filesystem path."
        )
    }


@app.post("/scan")
def scan(
    request: Request,
    path: str,
) -> dict[str, Any]:
    require_local_origin(request)
    scan_path = Path(path)

    if not scan_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Scan path does not exist.",
        )

    if not scan_path.is_dir():
        raise HTTPException(
            status_code=400,
            detail="Scan path must be a directory.",
        )

    monitor = DirectoryMonitor(
        scan_path
    )

    files = monitor.scan()

    return {
        "path": str(scan_path),
        "files": [
            str(file_path)
            for file_path in files
        ],
        "count": len(files),
    }


@app.get("/reports")
def reports(
    response: Response,
    limit: int = Query(
        100,
        ge=1,
        le=1000,
    ),
    cursor: str | None = None,
    include_total: bool = False,
) -> dict[str, Any]:
    database = get_database()
    decoded_cursor = _decode_list_cursor(cursor)

    incident_rows = (
        database.list_incidents(
            limit=limit + 1,
            cursor=decoded_cursor,
        )
    )
    incidents_data = _paginate_rows(
        response,
        incident_rows,
        limit=limit,
        timestamp_key="created_at",
        total_count=(
            database.count_records("incidents")
            if include_total
            else None
        ),
    )

    return {
        "count": len(incidents_data),
        "incidents": incidents_data,
    }


@app.post("/reports/generate")
def generate_report(
    request: Request,
    incident_id: int,
    format: str = Query(
        "json",
        pattern="^(json|csv)$",
    ),
) -> dict[str, Any]:
    require_local_origin(request)
    runtime = get_runtime()
    database = get_database()

    incident = database.get_incident(incident_id)

    if incident is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Incident {incident_id} "
                "was not found."
            ),
        )

    report = build_incident_report(
        database,
        incident,
    )

    destination_dir = report_directory(
        runtime
    )

    timestamp = datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )

    filename = (
        f"incident_{incident_id}_{timestamp}_{uuid.uuid4().hex}"
    )

    if format == "json":
        destination = (
            destination_dir
            / f"{filename}.json"
        )
        write_json_report(
            report,
            destination,
        )
    else:
        destination = (
            destination_dir
            / f"{filename}.csv"
        )
        write_csv_report(
            report,
            destination,
        )

    prune_reports(
        destination_dir,
        incident_id,
    )

    return {
        "generated": True,
        "format": format,
        "incident_id": incident_id,
        "path": str(destination),
        "report": report,
    }



@app.get("/reports/{incident_id}/{format}")
def download_report(
    request: Request,
    incident_id: int,
    format: str,
) -> FileResponse:
    if format not in {
        "json",
        "csv",
    }:
        raise HTTPException(
            status_code=400,
            detail=(
                "Report format must be json or csv."
            ),
        )

    runtime = get_runtime()
    destination_dir = report_directory(
        runtime
    )

    matches = sorted(
        (
            path
            for path in destination_dir.glob(
                f"incident_{incident_id}_*.{format}"
            )
            if not path.is_symlink()
            and path.is_file()
            and _is_path_within(path, destination_dir)
        ),
        reverse=True,
    )

    if not matches:
        raise HTTPException(
            status_code=404,
            detail=(
                "Generated report was not found. "
                "Generate the report first."
            ),
        )

    media_type = _report_media_type(format)

    if request.headers.get("x-rdrs-public") == "1":
        # Read the already generated file; never regenerate or write evidence.
        import io
        if format == "json":
            body = json.dumps(redact(json.loads(matches[0].read_text(encoding="utf-8"))), ensure_ascii=False)
        else:
            output = io.StringIO(newline="")
            with matches[0].open(encoding="utf-8-sig", newline="") as source:
                reader = csv.DictReader(source)
                writer = csv.DictWriter(output, fieldnames=reader.fieldnames)
                writer.writeheader()
                for row in reader:
                    safe = {}
                    for field, value in row.items():
                        if field in {"record_type", "action", "level"}:
                            safe[field] = value if value in {"incident", "event", "affected_file", "suspect_process", "recommendation", "created", "modified", "deleted", "renamed", "moved", "NORMAL", "WARNING", "CRITICAL"} else "Redacted"
                        elif field in {"score", "incident_id"}:
                            try: safe[field] = float(value) if field == "score" else int(value)
                            except ValueError: safe[field] = ""
                        elif field == "details":
                            try: safe[field] = json.dumps(redact(json.loads(value)))
                            except (ValueError, TypeError): safe[field] = "Identifying text redacted" if value else ""
                        else:
                            safe[field] = redact(value, field)
                    writer.writerow(safe)
            body = output.getvalue()
        return Response(body, media_type=media_type, headers={"Cache-Control": "no-store", "Content-Disposition": f'attachment; filename="incident_{incident_id}_redacted.{format}"'})

    return FileResponse(
        matches[0],
        media_type=media_type,
        filename=matches[0].name,
    )


@app.post("/settings")
def update_settings(
    request: Request,
    settings: SettingsUpdate,
) -> dict[str, Any]:
    require_local_origin(request)
    runtime = get_runtime()

    if runtime._config is None:
        runtime._apply_runtime_settings(
            config_path=runtime._pending_config_path,
            data_dir=runtime._pending_data_dir,
            logs_dir=runtime._pending_logs_dir,
            db_path=runtime._pending_db_path,
        )

    if runtime._config is None:
        raise HTTPException(
            status_code=500,
            detail=(
                "Configuration is not available."
            ),
        )

    current_config = runtime._config

    # An empty payload (``{}``) asks for no change at all. Saving anyway used to
    # rewrite config.yaml -- dropping watch_paths and every comment -- so the
    # request is answered from the live configuration without touching the file.
    #
    # This is checked before the state guard below: reading the current settings
    # is always allowed, including while monitoring runs.
    if not settings.model_dump(exclude_none=True):
        return current_config.to_dict()

    # Settings are read-only while monitoring runs.
    #
    # Changing the watch folders, the database or the detection thresholds under
    # a live pipeline would leave the running monitors disagreeing with the
    # stored configuration, so the mutation is refused until monitoring stops.
    # Placed before any assignment to `config` so a refused request changes
    # nothing, not even in memory. Reading the settings and `/status` stay
    # available so the dashboard can still show the current values.
    if runtime.state in {
        RuntimeStatus.RUNNING,
        RuntimeStatus.STARTING,
        RuntimeStatus.DEGRADED,
    }:
        raise HTTPException(
            status_code=409,
            detail=(
                "Settings can be changed only when monitoring is stopped. "
                "Stop monitoring and try again."
            ),
        )

    # Validate and persist a detached candidate. A rejected multi-field
    # request or stale-file conflict must not partially alter live memory.
    config = deepcopy(current_config)

    if settings.name is not None:
        name = settings.name.strip()

        if not name:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Runtime name cannot be empty."
                ),
            )

        config.name = name

    if settings.environment is not None:
        environment = (
            settings.environment.strip()
        )

        if not environment:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Environment cannot be empty."
                ),
            )

        config.environment = environment

    if settings.shutdown_timeout is not None:
        if settings.shutdown_timeout <= 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    "shutdown_timeout must be positive."
                ),
            )

        config.shutdown_timeout = (
            settings.shutdown_timeout
        )

    if settings.log_level is not None:
        log_level = (
            settings.log_level
            .strip()
            .upper()
        )

        allowed_levels = {
            "DEBUG",
            "INFO",
            "WARNING",
            "ERROR",
            "CRITICAL",
        }

        if log_level not in allowed_levels:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Invalid log level."
                ),
            )

        config.log_level = log_level

    if settings.persistence_enabled is not None:
        # Pydantic already coerces the JSON literal, but a string such as
        # "false" would otherwise be stored verbatim and read back as truthy.
        if not isinstance(settings.persistence_enabled, bool):
            raise HTTPException(
                status_code=400,
                detail=(
                    "persistence_enabled must be true or false."
                ),
            )

        config.persistence_enabled = (
            settings.persistence_enabled
        )

    try:
        config.validate()
    except (
        ValueError,
        TypeError,
    ) as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    # Persist so the change survives a restart. Settings that only live in
    # memory would silently revert on the next start.
    config_path = runtime._pending_config_path

    if config_path is not None:
        try:
            # `save` compares the file against the digest this configuration
            # was loaded from, so a stale tab cannot clobber a newer edit.
            config.save(config_path)
        except ConfigConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail=(
                    "The configuration file changed on disk since it was "
                    "loaded, so this update was not applied. Reload the "
                    "settings and try again."
                ),
            ) from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(
                status_code=500,
                detail=(
                    "Settings could not be persisted; no change was applied: "
                    f"{exc}"
                ),
            ) from exc

    runtime._config = config
    runtime.logger = initialize_logging(
        "rdrs",
        level=config.log_level,
    )

    return config.to_dict()
