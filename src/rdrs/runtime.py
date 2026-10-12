from __future__ import annotations

import logging
import signal
import threading
import time
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from .burst_detection import BurstAnalysis, BurstDetectionEngine
from .config import RuntimeConfig
from .logging import (
    close_rotating_logs,
    initialize_logging,
    initialize_rotating_logs,
)
from .monitoring import RealtimeFileMonitor
from .scoring import ThreatScore
from .response import IncidentResponseEngine
from .storage import DatabaseManager
from .telemetry import ProcessMonitor


class RuntimeStatus(str, Enum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    SHUTTING_DOWN = "SHUTTING_DOWN"
    FAILED = "FAILED"


class RuntimeManager:
    _instance: "RuntimeManager | None" = None
    _lock = threading.RLock()

    def __new__(cls, *args: Any, **kwargs: Any) -> "RuntimeManager":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
            return cls._instance

    def __init__(
        self,
        config_path: str | Path | None = None,
        data_dir: str | Path | None = None,
        logs_dir: str | Path | None = None,
        db_path: str | Path | None = None,
    ) -> None:
        if getattr(self, "_initialized", False):
            if any(
                value is not None
                for value in (
                    config_path,
                    data_dir,
                    logs_dir,
                    db_path,
                )
            ):
                self._apply_runtime_settings(
                    config_path=config_path,
                    data_dir=data_dir,
                    logs_dir=logs_dir,
                    db_path=db_path,
                )
            return

        self._initialized = True

        self.state = RuntimeStatus.STOPPED

        self._config: RuntimeConfig | None = None
        self._database: DatabaseManager | None = None

        self._monitor: RealtimeFileMonitor | None = None
        self._monitors: list[RealtimeFileMonitor] = []
        self._process_monitor: ProcessMonitor | None = None
        self._process_thread: threading.Thread | None = None
        self._process_stop = threading.Event()

        self._detector: BurstDetectionEngine | None = None
        self._response: IncidentResponseEngine | None = None

        self._last_analysis: BurstAnalysis | None = None
        self._analysis_lock = threading.RLock()

        # Threat levels already responded to within the *current* burst.
        # A burst stays dangerous for every event inside the sliding window,
        # so without this guard one burst raised a fresh score/alert/incident
        # for each of its events. Cleared whenever the analysis returns to
        # NORMAL, which is what marks the end of a burst.
        self._responded_levels: set[str] = set()
        self._response_lock = threading.Lock()

        self._subsystems: dict[str, dict[str, Any]] = {}

        self.logger = initialize_logging("rdrs")

        self.started_at: float | None = None
        self._shutdown_requested = False
        self._signal_handlers_installed = False

        self._pending_config_path = (
            Path(config_path)
            if config_path is not None
            else self.default_config_path()
        )

        self._pending_data_dir = (
            Path(data_dir)
            if data_dir is not None
            else None
        )

        self._pending_logs_dir = (
            Path(logs_dir)
            if logs_dir is not None
            else None
        )

        self._pending_db_path = (
            Path(db_path)
            if db_path is not None
            else None
        )

        if any(
            value is not None
            for value in (
                config_path,
                data_dir,
                logs_dir,
                db_path,
            )
        ):
            self._apply_runtime_settings(
                config_path=config_path,
                data_dir=data_dir,
                logs_dir=logs_dir,
                db_path=db_path,
            )

    @classmethod
    def reset_singleton(cls) -> None:
        with cls._lock:
            if cls._instance is not None:
                try:
                    cls._instance.close()
                except Exception:
                    pass

            cls._instance = None

    def _apply_runtime_settings(
        self,
        *,
        config_path: str | Path | None = None,
        data_dir: str | Path | None = None,
        logs_dir: str | Path | None = None,
        db_path: str | Path | None = None,
    ) -> None:
        if config_path is None:
            config_path = self._pending_config_path

        if data_dir is None:
            data_dir = self._pending_data_dir

        if logs_dir is None:
            logs_dir = self._pending_logs_dir

        if db_path is None:
            db_path = self._pending_db_path

        try:
            self._config = RuntimeConfig.load(
                config_path=config_path,
                data_dir=data_dir,
                logs_dir=logs_dir,
                db_path=db_path,
            )
        except FileNotFoundError:
            self._config = None
            return

        self._pending_config_path = Path(config_path)

        if data_dir is not None:
            self._pending_data_dir = Path(data_dir)

        if logs_dir is not None:
            self._pending_logs_dir = Path(logs_dir)

        if db_path is not None:
            self._pending_db_path = Path(db_path)

        self.logger = initialize_logging(
            "rdrs",
            level=self._config.log_level,
        )

        self._operational_loggers = self._init_rotating_logs()

        # Same rule as ``_init_persistence``: a disabled persistence setting
        # selects a non-durable database rather than no database at all, so
        # read endpoints and ``start()`` always agree about what exists.
        # ``getattr`` keeps this working for the lightweight config stand-ins
        # the tests pass in, which only define the fields they exercise.
        self._database = DatabaseManager(
            getattr(
                self._config,
                "resolved_db_path",
                getattr(self._config, "db_path", None),
            )
            if getattr(
                self._config,
                "persistence_enabled",
                True,
            )
            else None
        )

        self._load_persisted_state()

    def _init_rotating_logs(self) -> dict[str, Any]:
        """Create the five rotating operational logs under ``logs_dir``.

        The Build Guide (Phase 1) requires dedicated, size-rotated
        ``system``/``events``/``alerts``/``errors``/``audit`` logs. They are
        wired here so real runs actually populate them instead of only the
        unit-tested factory existing.
        """
        logs_dir = (
            self._pending_logs_dir
            or getattr(
                self._config,
                "resolved_logs_dir",
                None,
            )
            or Path("logs")
        )

        try:
            loggers = initialize_rotating_logs(
                logs_dir,
                level=(
                    getattr(
                        self._config,
                        "log_level",
                        None,
                    )
                    or "INFO"
                ),
            )
        except (OSError, ValueError):
            return {}

        return loggers

    def _load_persisted_state(self) -> None:
        if self._database is None:
            return

        try:
            self._database.initialize()

            with self._database.engine.connect() as connection:
                row = connection.exec_driver_sql(
                    "SELECT value FROM runtime_state "
                    "WHERE key = 'state' "
                    "ORDER BY updated_at DESC LIMIT 1"
                ).fetchone()

            if row is None:
                return

            value = str(row[0])

            persisted = (
                RuntimeStatus[value]
                if value in RuntimeStatus.__members__
                else RuntimeStatus(value)
            )

            self.state = persisted

        except Exception:
            self.state = RuntimeStatus.STOPPED

    def _op_log(
        self,
        channel: str,
        message: str,
        *,
        level: int = logging.INFO,
        details: Any = None,
    ) -> None:
        """Write an entry to one of the five rotating operational logs.

        ``channel`` is one of ``system``/``events``/``alerts``/``errors``/
        ``audit``. Writes are best-effort: logging must never break the
        detection or response pipeline.
        """
        logger = (
            getattr(self, "_operational_loggers", {}) or {}
        ).get(channel)

        if logger is None:
            return

        try:
            logger.log(
                level,
                message,
                extra={"details": details},
            )
        except (OSError, ValueError, TypeError):
            return

    def _install_signal_handlers(self) -> None:
        if self._signal_handlers_installed:
            return

        self._signal_handlers_installed = True

        for signum in (
            signal.SIGINT,
            signal.SIGTERM,
        ):
            try:
                signal.signal(
                    signum,
                    self.handle_signal,
                )
            except (AttributeError, ValueError):
                continue

    def _register_subsystem(
        self,
        name: str,
        *,
        healthy: bool = True,
        critical: bool = True,
        details: dict[str, Any] | None = None,
    ) -> None:
        details = details or {}

        self._subsystems[name] = {
            "name": name,
            "healthy": bool(healthy),
            "critical": bool(critical),
            "details": details,
        }

        if self._database is not None:
            try:
                self._database.record_subsystem(
                    name,
                    healthy,
                    details,
                )
            except Exception:
                pass

        if self.state in {
            RuntimeStatus.RUNNING,
            RuntimeStatus.DEGRADED,
        }:
            critical_failures = any(
                info["critical"] and not info["healthy"]
                for info in self._subsystems.values()
            )

            if critical_failures:
                self.state = RuntimeStatus.DEGRADED
            elif all(
                info["healthy"]
                for info in self._subsystems.values()
                if info["critical"]
            ):
                self.state = RuntimeStatus.RUNNING

    def register_subsystem(
        self,
        name: str,
        *,
        healthy: bool = True,
        critical: bool = True,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._register_subsystem(
            name,
            healthy=healthy,
            critical=critical,
            details=details,
        )

    def _init_persistence(self) -> None:
        if self._config is None:
            raise RuntimeError(
                "Runtime configuration not initialized"
            )

        # ``persistence.enabled: false`` means "do not write anything to disk".
        # It must NOT mean "run without a database": detection, scoring and
        # incident response all need one, and returning early here left
        # ``_database`` as None so ``start()`` raised "Database must be
        # initialized before incident response" -- the runtime could then never
        # be started again without restarting the whole process.
        durable = bool(self._config.persistence_enabled)

        if self._database is None or (
            getattr(self._database, "durable", durable) != durable
        ):
            # Switching modes needs a fresh manager; an in-memory database
            # cannot be "upgraded" into a file-backed one.
            if self._database is not None:
                try:
                    self._database.close()
                except Exception:
                    pass

            self._database = DatabaseManager(
                (
                    getattr(
                        self._config,
                        "resolved_db_path",
                        getattr(self._config, "db_path", None),
                    )
                    if durable
                    else None
                )
            )

        # Re-initialise only when there is no live engine. `initialize()` always
        # builds a new engine, and for an in-memory database a new engine means
        # an empty database -- re-initialising on every start erased everything
        # the monitor had collected whenever monitoring was stopped and started.
        # A durable engine is disposed by `stop()`, so it is rebuilt here.
        # `getattr` keeps this working for the lightweight database stand-ins
        # the tests substitute, which only implement the methods they exercise.
        if getattr(self._database, "engine", None) is None:
            self._database.initialize()

        self._database.record_runtime_state(
            self.state.value
        )

    def _init_default_subsystems(self) -> None:
        self._subsystems = {}

        self._register_subsystem(
            "config",
            healthy=True,
            critical=True,
            details={
                "config_path": str(
                    getattr(
                        self._config,
                        "config_path",
                        "",
                    )
                )
            },
        )

        self._register_subsystem(
            "logging",
            healthy=True,
            critical=True,
            details={
                "level": self._config.log_level,
            },
        )

        self._register_subsystem(
            "database",
            healthy=True,
            critical=True,
            details={
                "db_path": str(
                    getattr(
                        self._config,
                        "resolved_db_path",
                        getattr(self._config, "db_path", None),
                    )
                ),
            },
        )

    def _create_detection_pipeline(self) -> None:
        if self._config is None:
            raise RuntimeError(
                "Runtime configuration not initialized"
            )

        thresholds = dict(
            getattr(
                self._config,
                "detection_thresholds",
                {},
            )
            or {}
        )
        scoring_weights = dict(
            getattr(
                self._config,
                "scoring_weights",
                {},
            )
            or {}
        )

        self._detector = BurstDetectionEngine(
            thresholds=thresholds or None,
            scoring_weights=scoring_weights or None,
        )

        # A fresh pipeline starts with no burst in progress.
        with self._response_lock:
            self._responded_levels.clear()

        if self._database is None:
            raise RuntimeError(
                "Database must be initialized before "
                "incident response"
            )

        simulation_mode = bool(
            getattr(
                self._config,
                "simulation_mode",
                True,
            )
        )

        quarantine_dir = (
            getattr(
                self._config,
                "resolved_data_dir",
                self._config.data_dir,
            )
            / "quarantine"
        )

        self._response = IncidentResponseEngine(
            self._database,
            simulation_mode=simulation_mode,
            quarantine_dir=quarantine_dir,
        )

        self._register_subsystem(
            "detection",
            healthy=True,
            critical=True,
            details={
                "engine": "BurstDetectionEngine",
                "thresholds": (
                    thresholds
                    if thresholds
                    else "engine_defaults"
                ),
            },
        )

        self._register_subsystem(
            "response",
            healthy=True,
            critical=True,
            details={
                "engine": "IncidentResponseEngine",
                "simulation_mode": simulation_mode,
                "quarantine_dir": str(
                    quarantine_dir
                ),
            },
        )

    def _handle_file_event(
        self,
        event: dict[str, Any],
    ) -> None:
        detector = self._detector

        if detector is None:
            return

        self._op_log(
            "events",
            "rdrs.file.event",
            details={
                "path": (
                    event.get("path")
                    if isinstance(event, dict)
                    else getattr(event, "path", None)
                ),
                "action": (
                    event.get("action")
                    if isinstance(event, dict)
                    else getattr(event, "action", None)
                ),
            },
        )

        persistence_error = (
            event.get("persistence_error")
            if isinstance(event, dict)
            else getattr(event, "persistence_error", None)
        )

        if persistence_error:
            details = {
                "error": str(persistence_error),
                "path": (
                    event.get("path")
                    if isinstance(event, dict)
                    else getattr(event, "path", None)
                ),
            }
            self.logger.error(
                "rdrs.file.event.persistence.failed",
                extra={"details": details},
            )
            self._op_log(
                "errors",
                "rdrs.file.event.persistence.failed",
                level=logging.ERROR,
                details=details,
            )

        try:
            ingest = getattr(detector, "ingest", None) or getattr(detector, "ingest_event", None)
            if ingest is None:
                raise AttributeError("Detector has neither ingest nor ingest_event")
            analysis = ingest(event)

            with self._analysis_lock:
                self._last_analysis = analysis

            if self._database is None:
                return

            analysis_level = self._analysis_level(analysis)
            if analysis_level == "NORMAL":
                # The burst has cleared. Forget what was already responded to
                # so a genuinely new burst is handled instead of being
                # suppressed as a continuation of the old one.
                with self._response_lock:
                    self._responded_levels.clear()
                return

            # ``BurstAnalysis`` carries ``detected``/``level``/``signals``;
            # it has no ``dangerous_burst`` attribute. Read the real field and
            # fall back to the level band so a genuine burst is never dropped
            # because of a non-existent attribute.
            if not self._is_dangerous_burst(analysis, analysis_level):
                return

            # One response per burst per severity band. A burst remains
            # dangerous for every event inside its sliding window, so
            # responding per event raised duplicate scores, duplicate alerts
            # and duplicate incidents for the *same* burst.
            #
            # Deduplication is by severity, not by signal set: a further
            # signal joining the same band is still the same ongoing threat at
            # the same severity, so re-alerting would be noise. A genuine
            # escalation (WARNING -> CRITICAL) *is* a new band and is recorded,
            # which is what lets the burst create its incident. Because
            # CRITICAL is recorded at most once per burst, at most one
            # incident is ever created per burst.
            response = self._response

            if response is None:
                raise RuntimeError(
                    "Incident response engine is not initialized"
                )

            if not self._claim_response_level(analysis_level):
                return

            score_data = getattr(analysis, "score", None)

            fired_signals = set(
                self._active_signals(analysis)
            )

            def signal_value(name: str) -> float:
                """Return the weighted contribution of one signal.

                The scoring weights come from the detection engine's own
                breakdown so the response score is identical to the score the
                detector computed - never a re-derived or hardcoded value.
                """
                if name in fired_signals:
                    value = getattr(
                        score_data,
                        name,
                        None,
                    )

                    if value:
                        return float(value)

                    return float(
                        getattr(
                            analysis,
                            name,
                            True,
                        )
                    )

                return 0.0

            response_score = ThreatScore(
                rapid_encryption=signal_value("rapid_encryption"),
                mass_rename=signal_value("mass_rename"),
                high_entropy=signal_value("high_entropy"),
                cpu_spike=signal_value("cpu_spike"),
                unknown_program=signal_value("unknown_program"),
            )

            suspect_process = analysis.suspect_process
            suspect_process_attribution = (
                "heuristic: highest CPU process in the host snapshot; "
                "not causally attributed to file operations"
                if suspect_process
                else None
            )

            result = response.process(
                response_score,
                affected_files=analysis.affected_files,
                suspect_process=suspect_process,
                suspect_process_attribution=(
                    suspect_process_attribution
                ),
                reason=self._build_detection_reason(
                    analysis
                ),
                # Persist the detector's real signal map so the generated
                # incident report explains the threat from that evidence.
                signals=self._signal_map(analysis),
            )

            if result.incident_id is not None:
                affected_paths = set(analysis.affected_files)
                event_ids = [
                    int(item["event_id"])
                    for item in analysis.events
                    if item.get("event_id") is not None
                    and affected_paths.intersection(
                        str(item.get(key))
                        for key in ("path", "previous_path", "new_path")
                        if item.get(key)
                    )
                ]
                detector = self._detector
                since = (
                    detector.window_started_at
                    if detector is not None
                    else None
                )
                until = datetime.fromtimestamp(
                    analysis.timestamp,
                    tz=timezone.utc,
                )
                self._attribute_events_to_incident(
                    result.incident_id,
                    list(analysis.affected_files),
                    event_ids=event_ids,
                    since=since,
                    until=until,
                )

            self.logger.warning(
                "rdrs.threat.detected",
                extra={
                    "details": {
                        "score": analysis.score.total,
                        "level": (
                            getattr(analysis, "level", None)
                        ),
                        "signals": (
                            self._active_signals(analysis)
                        ),
                        "affected_files": (
                            analysis.affected_files
                        ),
                        "suspect_process": (
                            analysis.suspect_process
                        ),
                        "suspect_process_attribution": (
                            suspect_process_attribution
                        ),
                        "score_id": result.score_id,
                        "alert_id": result.alert_id,
                        "incident_id": (
                            result.incident_id
                        ),
                        "quarantined_files": (
                            result.quarantined_files
                        ),
                    }
                },
            )

            threat_details = {
                "score": analysis.score.total,
                "level": self._analysis_level(analysis),
                "signals": self._active_signals(analysis),
                "affected_files": list(
                    analysis.affected_files
                ),
                "suspect_process": (
                    analysis.suspect_process
                ),
                "suspect_process_attribution": (
                    suspect_process_attribution
                ),
                "score_id": result.score_id,
                "alert_id": result.alert_id,
                "incident_id": result.incident_id,
                "quarantined_files": (
                    result.quarantined_files
                ),
            }

            self._op_log(
                "alerts",
                "rdrs.threat.detected",
                level=logging.WARNING,
                details=threat_details,
            )

            # The Build Guide requires an audit-log entry whenever a critical
            # incident is raised, independent of the alert stream.
            if result.incident_id is not None:
                self._op_log(
                    "audit",
                    "rdrs.incident.created",
                    level=logging.CRITICAL,
                    details={
                        **threat_details,
                        "actor": "RDRS",
                        "action": "critical_threat_response",
                    },
                )

            # The severity was claimed atomically before response processing.
            # The detector window remains intact so a burst may still escalate.

        except Exception as exc:
            self.logger.exception(
                "rdrs.detection.pipeline.failed",
                extra={
                    "details": str(exc),
                    "event": event,
                },
            )

            self._op_log(
                "errors",
                "rdrs.detection.pipeline.failed",
                level=logging.ERROR,
                details={
                    "error": str(exc),
                    "event": event,
                },
            )

            self._register_subsystem(
                "detection",
                healthy=False,
                critical=True,
                details={
                    "error": str(exc),
                },
            )

    def _claim_response_level(self, level: str) -> bool:
        """Atomically claim one response per severity in the active burst."""
        with self._response_lock:
            if level in self._responded_levels:
                return False
            self._responded_levels.add(level)
            return True

    @staticmethod
    def _analysis_level(analysis: BurstAnalysis) -> str:
        """Return the analysis threat band as an upper-case string.

        ``BurstAnalysis.level`` is a plain ``str`` today, but historical and
        alternate detectors may hand back a ``ThreatLevel`` enum. Both are
        normalised here so comparison is always string-to-string.
        """
        raw = getattr(analysis, "level", None)

        if raw is None:
            score = getattr(analysis, "score", None)
            raw = getattr(score, "level", None)

        raw = getattr(raw, "value", raw)

        if raw is None:
            return "NORMAL"

        return str(raw).upper()

    @classmethod
    def _is_dangerous_burst(
        cls,
        analysis: BurstAnalysis,
        level: str | None = None,
    ) -> bool:
        """Decide whether an analysis must drive the response pipeline.

        A burst is actionable when the detector flagged it (``detected``) or
        when the weighted score lands in the WARNING/CRITICAL bands. The
        ``dangerous_burst`` attribute is honoured when a detector actually
        provides it, but its absence must not silently disable detection.
        """
        flagged = getattr(analysis, "dangerous_burst", None)

        if flagged is not None:
            return bool(flagged)

        if getattr(analysis, "detected", False):
            return True

        resolved = level or cls._analysis_level(analysis)

        return resolved in {"WARNING", "CRITICAL"}

    @staticmethod
    def _active_signals(
        analysis: BurstAnalysis,
    ) -> list[str]:
        """Return the burst's fired signal names.

        ``BurstAnalysis.to_dict()["signals"]`` is a **list of signal names**
        (e.g. ``["rapid_encryption", "mass_rename"]``), not a mapping. This
        helper accepts both shapes so callers never assume one form.
        """
        raw = analysis.to_dict().get("signals")

        if isinstance(raw, dict):
            return [
                str(name)
                for name, fired in raw.items()
                if fired
            ]

        if isinstance(raw, (list, tuple, set)):
            return [str(name) for name in raw]

        return []

    @staticmethod
    def _signal_map(
        analysis: BurstAnalysis,
    ) -> dict[str, bool]:
        """Return ``{signal_name: fired}`` for every known detection signal.

        This is the shape persisted into the incident evidence and consumed
        by :func:`rdrs.api._build_recommendations`, so the generated report
        explains *why* the incident was raised rather than only its score.
        """
        fired = set(
            RuntimeManager._active_signals(analysis)
        )

        return {
            name: name in fired
            for name in (
                "rapid_encryption",
                "mass_rename",
                "high_entropy",
                "cpu_spike",
                "unknown_program",
            )
        }

    @staticmethod
    def _build_detection_reason(
        analysis: BurstAnalysis,
    ) -> str:
        active_signals = (
            RuntimeManager._active_signals(analysis)
        )

        if not active_signals:
            return (
                "Dangerous ransomware-like burst detected"
            )

        return (
            "Ransomware-like burst detected; "
            "signals: "
            + ", ".join(active_signals)
        )

    def _attribute_events_to_incident(
        self,
        incident_id: int,
        affected_files: list[str] | None = None,
        *,
        event_ids: list[int] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> int:
        """Attach the burst's events to the incident just created.

        The events that produced the burst were persisted to the ``events``
        table immediately before the incident existed, so they carry no
        ``incident_id`` yet. They are attributed to this incident here, which
        is what makes the Phase 8 report timeline genuinely incident-scoped.

        Attribution is by the affected-file set first (exact and stable), and
        falls back to the detection window when no file list is available.
        Events that already belong to another incident are never reassigned,
        so concurrent incidents stay isolated.

        Attribution is an evidence-quality enhancement, not a prerequisite for
        incident response: a database that does not expose the attribution
        helpers is skipped rather than allowed to break the pipeline.
        """
        database = self._database

        if database is None:
            return 0

        if event_ids:
            attach_by_id = getattr(
                database,
                "attach_events_to_incident",
                None,
            )
            if attach_by_id is not None:
                try:
                    # Stable event identities take precedence over heuristic
                    # path matching. A partial identity match is not broadened
                    # to unrelated historical rows.
                    return int(
                        attach_by_id(incident_id, event_ids)
                    )
                except (RuntimeError, OSError, ValueError):
                    return 0

        by_paths = getattr(
            database,
            "attach_events_to_incident_by_paths",
            None,
        )
        by_window = getattr(
            database,
            "attach_events_to_incident_by_window",
            None,
        )

        attributed = 0

        if affected_files and by_paths is not None:
            try:
                attributed = by_paths(
                    incident_id,
                    list(affected_files),
                    since=since,
                    until=until,
                )
            except (RuntimeError, OSError, ValueError):
                attributed = 0

        if not attributed and by_window is not None:
            detector = self._detector

            since = (
                getattr(
                    detector,
                    "window_started_at",
                    None,
                )
                if detector is not None
                else None
            )

            try:
                attributed = by_window(
                    incident_id,
                    since=since,
                    until=until,
                )
            except (RuntimeError, OSError, ValueError):
                return 0

        if attributed:
            self.logger.info(
                "rdrs.incident.events_attributed",
                extra={
                    "details": {
                        "incident_id": incident_id,
                        "events_attributed": attributed,
                    }
                },
            )

        return attributed

    @staticmethod
    def _is_measurable_process(process: Any) -> bool:
        """Return whether a process snapshot may be used as the suspect.

        Excludes kernel pseudo-processes, which have no meaningful CPU usage:
        on Windows pid 0 is the "System Idle Process" whose CPU time is *idle*
        time. Including it would make it the highest-CPU process every cycle
        and fire the ``cpu_spike`` signal continuously.

        A snapshot with no readable pid is kept -- it is better to consider it
        than to silently discard evidence.
        """
        pid = getattr(process, "pid", None)

        if pid is None:
            return True

        try:
            return int(pid) > 0
        except (TypeError, ValueError):
            return True

    @staticmethod
    def default_config_path() -> Path:
        """Resolve the configuration file the runtime loads by default.

        The FSD (FR-1) requires the system to "Load configuration from
        config.yaml", and the Build Guide's project structure places
        ``config.yaml`` at the project root. So the root ``config.yaml`` is the
        canonical file and is preferred.

        ``config/rdrs.yaml`` is accepted as a fallback so an existing checkout
        or an external caller that still points at the older layout keeps
        working. The FSD-named file always wins when both are present.
        """
        root = Path(__file__).resolve().parents[2]

        candidates = [
            root / "config.yaml",
            root / "config" / "rdrs.yaml",
        ]

        for candidate in candidates:
            if candidate.is_file():
                return candidate

        # Nothing exists yet (for example a fresh clone before first run):
        # return the canonical path so the error names the file the FSD
        # specifies.
        return candidates[0]

    def _process_monitor_loop(self) -> None:
        # Capture immediately, then settle into the interval. Waiting *before*
        # the first capture delayed all process telemetry by a whole interval.
        # The state is set to RUNNING moments after this thread starts, so an
        # early pass may legitimately skip; those paths wait briefly instead of
        # spinning.
        while True:
            if self.state not in {
                RuntimeStatus.RUNNING,
                RuntimeStatus.DEGRADED,
            }:
                if self._process_stop.wait(0.5):
                    return
                continue

            if self._database is None:
                if self._process_stop.wait(0.5):
                    return
                continue

            database = self._database
            process_monitor = self._process_monitor

            if process_monitor is None:
                if self._process_stop.wait(0.5):
                    return
                continue

            try:
                capture_persisted = False
                snapshots = (
                    process_monitor.capture_all()
                )

                if snapshots:
                    # Persist the whole capture in one transaction. Committing
                    # each of several hundred snapshots separately made the
                    # write the slowest part of the cycle.
                    record_many = getattr(
                        database,
                        "record_processes",
                        None,
                    )

                    if record_many is not None:
                        try:
                            capture_persisted = record_many(snapshots) == len(snapshots)
                        except (
                            RuntimeError,
                            OSError,
                        ):
                            record_many = None

                    if record_many is None:
                        capture_persisted = True
                        for snapshot in snapshots:
                            try:
                                database.record_process(
                                    snapshot
                                )
                            except (
                                RuntimeError,
                                OSError,
                            ):
                                capture_persisted = False
                                continue

                detector = self._detector

                if detector is not None and snapshots:
                    # Kernel pseudo-processes must never be nominated as the
                    # suspect. On Windows pid 0 is the "System Idle Process",
                    # whose CPU time is idle time -- selecting it would feed a
                    # ~100% CPU reading to the detector and fire cpu_spike on
                    # every cycle. Only known-invalid pids are excluded: an
                    # unknown pid is kept rather than silently dropping
                    # evidence.
                    candidates = [
                        process
                        for process in snapshots
                        if RuntimeManager._is_measurable_process(
                            process
                        )
                    ]

                    if not candidates:
                        continue

                    suspect_snapshot = max(
                        candidates,
                        key=lambda process: float(
                            getattr(process, "cpu_percent", 0.0)
                        ),
                    )

                    update_processes = getattr(detector, "update_processes", None)
                    if update_processes is not None:
                        update_processes([suspect_snapshot])
                    else:
                        detector.update_process(
                            suspect_snapshot.__dict__.copy()
                        )

                    with self._analysis_lock:
                        self._last_analysis = detector.last_analysis

                # Clear a stale failure only after a complete persisted capture
                # and successful analysis, not an empty or failed write cycle.
                if capture_persisted and not self._subsystems.get(
                    "process_monitor", {}
                ).get("healthy", True):
                    self._register_subsystem(
                        "process_monitor",
                        healthy=True,
                        critical=False,
                    )

            except Exception as exc:
                self.logger.warning(
                    "process.telemetry.failed",
                    extra={
                        "details": str(exc)
                    },
                )

                self._register_subsystem(
                    "process_monitor",
                    healthy=False,
                    critical=False,
                    details={
                        "error": str(exc),
                    },
                )

            # Wait out the sampling interval, then capture again.
            if self._process_stop.wait(5.0):
                return

    def _start_process_monitor(self) -> None:
        if (
            self._process_thread is not None
            and self._process_thread.is_alive()
        ):
            raise RuntimeError("Process monitor is already running")

        self._process_monitor = ProcessMonitor()
        self._process_stop.clear()

        # Establish CPU baselines up front so the first capture reports real
        # CPU percentages. Without this the first cycle would report 0.0 for
        # every process (there is nothing to compare against yet).
        try:
            primed = self._process_monitor.prime()
        except Exception as exc:
            primed = 0
            self.logger.warning(
                "process.telemetry.prime.failed",
                extra={"details": str(exc)},
            )

        self.logger.info(
            "process.monitor.started",
            extra={
                "details": {
                    "primed_processes": primed,
                    "interval_seconds": 5,
                }
            },
        )

        self._process_thread = threading.Thread(
            target=self._process_monitor_loop,
            name="rdrs-process-monitor",
            daemon=True,
        )

        self._process_thread.start()

    def _shutdown_timeout_seconds(self) -> float:
        """Return the configured maximum shutdown wait for one runtime stop."""
        timeout = getattr(self._config, "shutdown_timeout", 10.0)
        return max(0.0, float(timeout))

    def _stop_process_monitor(
        self,
        timeout: float | None = None,
    ) -> bool:
        self._process_stop.set()

        thread = self._process_thread

        if thread is not None:
            thread.join(
                timeout=(
                    self._shutdown_timeout_seconds()
                    if timeout is None
                    else max(0.0, float(timeout))
                )
            )

            if thread.is_alive():
                return False

        self._process_thread = None
        self._process_monitor = None
        return True

    def start(self) -> dict[str, Any]:
        if self.state in {
            RuntimeStatus.STARTING,
            RuntimeStatus.RUNNING,
        }:
            raise RuntimeError(
                "Runtime is already started and running"
            )

        if self.state == RuntimeStatus.SHUTTING_DOWN:
            raise RuntimeError(
                "Runtime is shutting down; wait for "
                "completion before starting again"
            )

        if self.state == RuntimeStatus.FAILED:
            raise RuntimeError(
                "Runtime is in FAILED state; reset or "
                "reinitialize before starting"
            )

        self.state = RuntimeStatus.STARTING
        self._shutdown_requested = False

        self.logger.info(
            "runtime.start.begin",
            extra={
                "state": self.state.value
            },
        )

        try:
            if self._config is None:
                self._apply_runtime_settings(
                    config_path=(
                        self._pending_config_path
                    ),
                    data_dir=self._pending_data_dir,
                    logs_dir=self._pending_logs_dir,
                    db_path=self._pending_db_path,
                )

            if self._config is None:
                raise RuntimeError(
                    "Configuration file not found or invalid"
                )

            if not getattr(self, "_operational_loggers", None):
                self._operational_loggers = self._init_rotating_logs()

            self._init_persistence()
            self._init_default_subsystems()
            self._create_detection_pipeline()
            self._install_signal_handlers()

            # Exclude everything RDRS itself writes inside the monitored tree.
            # Without these the detector would observe its own output: a
            # quarantined evidence copy or a generated report would look like
            # new file activity and could feed back into the burst counters.
            monitor_excludes = [
                "rdrs.db",
                "rdrs.db-journal",
                "rdrs.db-wal",
                "rdrs.db-shm",
                "quarantine",
                "reports",
                "*.tmp",
                "*.part",
                "*.swp",
            ]

            # Watch every configured folder (Build Guide Phase 1: the config
            # states "which folders to watch"). Falls back to data_dir when the
            # configuration does not list any.
            monitored_paths = self._config.monitored_paths

            self._monitors = []

            for monitored_path in monitored_paths:
                monitor = RealtimeFileMonitor(
                    monitored_path,
                    recursive=True,
                    excludes=monitor_excludes,
                    callback=self._handle_file_event,
                    database=self._database,
                )

                monitor.start()

                self._monitors.append(monitor)

            # The first monitor is also kept as ``_monitor`` for callers that
            # expect a single instance.
            self._monitor = self._monitors[0]

            self._register_subsystem(
                "file_monitor",
                healthy=all(
                    monitor.running
                    for monitor in self._monitors
                ),
                critical=True,
                details={
                    "root": str(
                        monitored_paths[0]
                    ),
                    "roots": [
                        str(item)
                        for item in monitored_paths
                    ],
                    "recursive": True,
                    "excludes": monitor_excludes,
                    "callback": (
                        "BurstDetectionEngine"
                    ),
                },
            )

            self._start_process_monitor()

            self._register_subsystem(
                "process_monitor",
                healthy=(
                    self._process_thread is not None
                    and self._process_thread.is_alive()
                ),
                critical=False,
                details={
                    "interval_seconds": 5,
                    "provider": "psutil",
                    "database_persistence": True,
                    "detection_integration": True,
                },
            )

            self.started_at = time.time()
            self.state = RuntimeStatus.RUNNING

            self.logger.info(
                "runtime.start.success",
                extra={
                    "state": self.state.value,
                    "subsystems": list(
                        self._subsystems.keys()
                    ),
                },
            )

            self._op_log(
                "system",
                "runtime.start.success",
                details={
                    "state": self.state.value,
                    "subsystems": list(
                        self._subsystems.keys()
                    ),
                    "data_dir": str(
                        getattr(
                            self._config,
                            "resolved_data_dir",
                            self._config.data_dir,
                        )
                    ) if self._config else None,
                    "logs_dir": str(
                        getattr(
                            self._config,
                            "resolved_logs_dir",
                            self._config.logs_dir,
                        )
                    ) if self._config else None,
                },
            )

            if self._database is not None:
                self._database.record_runtime_state(
                    self.state.value
                )

            return self.status()

        except Exception as exc:
            self.state = RuntimeStatus.FAILED

            self.logger.exception(
                "runtime.start.failed",
                extra={
                    "details": str(exc)
                },
            )

            try:
                process_stopped = self._stop_process_monitor(
                    timeout=self._shutdown_timeout_seconds()
                )
            except Exception:
                process_stopped = False

            monitors_stopped = True
            for monitor in self._monitors:
                try:
                    monitors_stopped = (
                        monitor.stop(
                            timeout=self._shutdown_timeout_seconds()
                        )
                        and monitors_stopped
                    )
                except Exception:
                    monitors_stopped = False

            if process_stopped and monitors_stopped:
                self._monitors = []
                self._monitor = None

            if self._database is not None:
                try:
                    self._database.record_runtime_state(
                        self.state.value
                    )
                except Exception:
                    pass

            raise RuntimeError(
                f"Failed to initialize runtime: {exc}"
            ) from exc

    def stop(self) -> dict[str, Any]:
        if self.state in {
            RuntimeStatus.STOPPED,
            RuntimeStatus.FAILED,
        }:
            raise RuntimeError(
                "Runtime is not running and cannot be stopped"
            )

        if self.state == RuntimeStatus.SHUTTING_DOWN:
            raise RuntimeError(
                "Runtime is already shutting down"
            )

        self.state = RuntimeStatus.SHUTTING_DOWN

        self.logger.warning(
            "runtime.stop.begin",
            extra={
                "state": self.state.value
            },
        )

        try:
            deadline = (
                time.monotonic()
                + self._shutdown_timeout_seconds()
            )
            process_stopped = self._stop_process_monitor(
                timeout=max(0.0, deadline - time.monotonic())
            )

            monitors_stopped = True
            for monitor in self._monitors:
                monitors_stopped = (
                    monitor.stop(
                        timeout=max(0.0, deadline - time.monotonic())
                    )
                    and monitors_stopped
                )

            if not process_stopped or not monitors_stopped:
                raise RuntimeError(
                    "Runtime shutdown timed out while waiting for monitor threads"
                )

            self._monitors = []
            self._monitor = None

            for subsystem in self._subsystems.values():
                subsystem["healthy"] = False

            if self._database is not None:
                self._database.record_runtime_state(
                    self.state.value
                )

            self.state = RuntimeStatus.STOPPED

            if self._database is not None:
                self._database.record_runtime_state(
                    self.state.value
                )

            self._cleanup()

            self.logger.info(
                "runtime.stop.success",
                extra={
                    "state": self.state.value
                },
            )

            self._op_log(
                "system",
                "runtime.stop.success",
                details={"state": self.state.value},
            )

            return self.status()

        except Exception as exc:
            self.state = RuntimeStatus.FAILED

            self.logger.exception(
                "runtime.stop.failed",
                extra={
                    "details": str(exc)
                },
            )

            self._op_log(
                "errors",
                "runtime.stop.failed",
                level=logging.ERROR,
                details={"error": str(exc)},
            )

            raise RuntimeError(
                f"Failed to stop runtime: {exc}"
            ) from exc

    def force_stop(self) -> None:
        deadline = time.monotonic() + self._shutdown_timeout_seconds()
        process_stopped = self._stop_process_monitor(
            timeout=max(0.0, deadline - time.monotonic())
        )

        monitors_stopped = True
        for monitor in self._monitors:
            try:
                monitors_stopped = (
                    monitor.stop(
                        timeout=max(0.0, deadline - time.monotonic())
                    )
                    and monitors_stopped
                )
            except Exception:
                monitors_stopped = False

        if not process_stopped or not monitors_stopped:
            # Keep shared DB/log resources alive while a writer thread is still
            # running. A later close/force_stop may complete the cleanup.
            self.state = RuntimeStatus.FAILED
            self._shutdown_requested = True
            return

        self._monitors = []
        self._monitor = None

        if self._database is not None:
            # An in-memory database lives inside its engine's single pooled
            # connection, so disposing it would erase everything collected so
            # far. It also holds no file handle to release, so it is kept for
            # the life of the process and survives a stop/start.
            if getattr(self._database, "durable", True):
                try:
                    self._database.close()
                except Exception:
                    pass

                self._database = None

        self._detector = None
        self._response = None

        # ``_operational_loggers`` is created by ``start()``; a stop/shutdown
        # on a runtime that never started must still be safe.
        close_rotating_logs(
            getattr(self, "_operational_loggers", {}) or {}
        )
        self._operational_loggers = {}

        with self._analysis_lock:
            self._last_analysis = None

        # A restart must be able to respond to a burst afresh.
        with self._response_lock:
            self._responded_levels.clear()

        self.started_at = None
        self._subsystems = {}
        self._shutdown_requested = True
        self.state = RuntimeStatus.STOPPED

    def _cleanup(self) -> None:
        deadline = time.monotonic() + self._shutdown_timeout_seconds()
        if not self._stop_process_monitor(
            timeout=max(0.0, deadline - time.monotonic())
        ):
            self.state = RuntimeStatus.FAILED
            return

        monitors_stopped = True
        for monitor in self._monitors:
            try:
                monitors_stopped = (
                    monitor.stop(
                        timeout=max(0.0, deadline - time.monotonic())
                    )
                    and monitors_stopped
                )
            except Exception:
                monitors_stopped = False

        if not monitors_stopped:
            self.state = RuntimeStatus.FAILED
            return

        self._monitors = []
        self._monitor = None

        if self._database is not None:
            # An in-memory database lives inside its engine's single pooled
            # connection, so disposing it would erase everything collected so
            # far. It also holds no file handle to release, so it is kept for
            # the life of the process and survives a stop/start.
            if getattr(self._database, "durable", True):
                try:
                    self._database.close()
                except Exception:
                    pass

                self._database = None

        self._detector = None
        self._response = None

        # ``_operational_loggers`` is created by ``start()``; a stop/shutdown
        # on a runtime that never started must still be safe.
        close_rotating_logs(
            getattr(self, "_operational_loggers", {}) or {}
        )
        self._operational_loggers = {}

        with self._analysis_lock:
            self._last_analysis = None

        self.started_at = None
        self._subsystems = {}
        self._shutdown_requested = True

    def close(self) -> None:
        try:
            if self.state in {
                RuntimeStatus.STARTING,
                RuntimeStatus.RUNNING,
                RuntimeStatus.DEGRADED,
                RuntimeStatus.SHUTTING_DOWN,
            }:
                self.stop()
            else:
                self.force_stop()
        except Exception:
            self.force_stop()

    def handle_signal(
        self,
        signum: int,
        frame: Any = None,
    ) -> None:
        signal_name = {
            signal.SIGINT: "SIGINT",
            signal.SIGTERM: "SIGTERM",
        }.get(
            signum,
            str(signum),
        )

        self.logger.warning(
            "runtime.signal.received",
            extra={
                "signal": signal_name,
                "state": self.state.value,
            },
        )

        if self.state in {
            RuntimeStatus.RUNNING,
            RuntimeStatus.DEGRADED,
            RuntimeStatus.STARTING,
        }:
            try:
                self.stop()
            except RuntimeError:
                self.state = RuntimeStatus.SHUTTING_DOWN

    def _config_for_status(self) -> RuntimeConfig | None:
        """Return the effective configuration for a status read.

        Before ``start()`` the runtime has not resolved its configuration, so
        ``/status`` used to report an empty ``config`` block. The dashboard
        reads its Settings page from that block, so every field rendered blank
        until the operator started monitoring -- and the operator had no way to
        see what configuration was about to be used.

        The configuration file is readable without starting anything, so it is
        resolved here on demand. This is read-only: the result is deliberately
        NOT cached into ``self._config``, because ``start()`` treats a set
        ``_config`` as "already initialised" and would then skip its own
        logging, rotating-log and database setup.
        """
        if self._config is not None:
            return self._config

        try:
            return RuntimeConfig.load(
                config_path=self._pending_config_path,
                data_dir=self._pending_data_dir,
                logs_dir=self._pending_logs_dir,
                db_path=self._pending_db_path,
            )
        except (OSError, ValueError):
            return None

    def status(self) -> dict[str, Any]:
        with self._analysis_lock:
            analysis = self._last_analysis

        # Resolved even when stopped, so the Settings page shows the real
        # configuration instead of a block of nulls.
        config = self._config_for_status()

        result: dict[str, Any] = {
            "state": self.state.value,
            "healthy": (
                self.state == RuntimeStatus.RUNNING
            ),
            "started_at": self.started_at,
            # ISO-8601 companion to the raw epoch above. Every other timestamp
            # the API emits (score, events, alerts, incidents, processes) is
            # ISO, so clients had to special-case this one field. The epoch
            # value is kept for backward compatibility.
            "started_at_iso": (
                datetime.fromtimestamp(
                    self.started_at,
                    tz=timezone.utc,
                ).isoformat()
                if self.started_at is not None
                else None
            ),
            "subsystems": {
                name: {
                    "healthy": info["healthy"],
                    "critical": info["critical"],
                    "details": info["details"],
                }
                for name, info in self._subsystems.items()
            },
            "config": (
                config.to_dict()
                if config is not None
                else {}
            ),
            # Explicit so the UI can say "unavailable" rather than rendering
            # blank fields, and can never fall back to a placeholder.
            "config_available": config is not None,
            "unhealthy_subsystems": [
                name
                for name, info in self._subsystems.items()
                if not info["healthy"]
            ],
        }

        if analysis is not None:
            analysis_data = analysis.to_dict()

            result["threat"] = {
                "score": analysis_data["score"],
                "level": analysis_data["level"],
                "signals": analysis_data["signals"],
                "dangerous_burst": (
                    analysis_data.get("dangerous_burst",False)
                ),
                "affected_files": (
                    analysis_data["affected_files"]
                ),
                "suspect_process": (
                    analysis_data["suspect_process"]
                ),
                "metrics": analysis_data["metrics"],
            }
        else:
            result["threat"] = {
                "score": 0,
                "level": "NORMAL",
                "signals": {},
                "dangerous_burst": False,
                "affected_files": [],
                "suspect_process": None,
                "metrics": {},
            }

        if self.state in {
            RuntimeStatus.DEGRADED,
            RuntimeStatus.STOPPED,
            RuntimeStatus.FAILED,
        }:
            result["healthy"] = False

        return result

    def health_status(self) -> dict[str, Any]:
        subsystem_status = {
            name: info["healthy"]
            for name, info in self._subsystems.items()
        }

        healthy = (
            all(subsystem_status.values())
            if subsystem_status
            else self.state == RuntimeStatus.RUNNING
        )

        return {
            "state": self.state.value,
            "healthy": healthy,
            "subsystems": subsystem_status,
            "degraded": (
                self.state == RuntimeStatus.DEGRADED
            ),
        }

    def shutdown(self) -> dict[str, Any]:
        return self.stop()

    def __repr__(self) -> str:
        return (
            "RuntimeManager("
            f"state={self.state.value}, "
            f"started_at={self.started_at})"
        )













