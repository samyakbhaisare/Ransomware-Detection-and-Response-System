"""Regression tests for the audited defects B-01 .. B-07.

Every test here fails against the pre-fix code and passes after it. The
front-end half of B-05/B-06/B-07 is verified by executing the dashboard's own
JavaScript (see ``tests/dashboard_frontend_harness.mjs``, invoked from this
module), because inspecting markup is not evidence that the UI behaves.

Defects covered
---------------
B-01  the sliding window was measured against the wall clock, so any event
      whose timestamp lagged the sampling clock by more than ``window_seconds``
      was appended and then immediately purged. A real 232-event burst scored
      0.0.
B-02  the detector window was wiped after every response, so a burst climbing
      towards CRITICAL was reset to zero and never created an incident, while
      each event of one burst raised a duplicate score and alert.
B-03  double-encoded UTF-8 (mojibake) in the dashboard.
B-04  a UTF-8 BOM at the head of a document served with an explicit charset.
B-05  ``/status`` reported an empty config block before ``start()``, so the
      Settings page rendered blank fields.
B-06  refresh reloaded only the active page, gave no feedback, and could race
      itself.
B-07  the Start/Stop buttons could be left permanently disabled after a failed
      control request, and DEGRADED named no failing subsystem.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from rdrs.burst_detection import (
    DEFAULT_SCORING_WEIGHTS,
    DEFAULT_THRESHOLDS,
    BurstDetectionEngine,
)
from rdrs.runtime import RuntimeManager

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = PROJECT_ROOT / "src" / "rdrs" / "dashboard" / "index.html"
FRONTEND_HARNESS = PROJECT_ROOT / "tests" / "dashboard_frontend_harness.mjs"

CONFIG_TEMPLATE = """\
runtime:
  name: rdrs
  environment: test
  data_dir: {data}
  logs_dir: {logs}
  shutdown_timeout: 5.0
logging:
  level: INFO
persistence:
  enabled: true
  db_path: {db}
detection:
  window_seconds: 60.0
  modified_files_per_minute: 20.0
  renames: 10.0
  extension_changes: 5.0
  average_entropy: 7.0
  cpu_percent: 80.0
scoring:
  weights:
    rapid_encryption: 40.0
    mass_rename: 30.0
    high_entropy: 25.0
    cpu_spike: 15.0
    unknown_program: 10.0
subsystems:
- name: config
  healthy: true
  critical: true
"""


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def build_burst(
    base: float,
    *,
    modifications: int = 60,
    renames: int = 15,
    entropy: float = 7.9,
    prefix: str = "file",
) -> list[dict]:
    """Build the Build Guide section 15 burst shape as detector events.

    High-entropy writes followed by a mass ``.txt`` -> ``.locked`` rename,
    which is what the guide's safe self-test produces.
    """
    events: list[dict] = []

    for index in range(modifications):
        events.append(
            {
                "action": "modified",
                "path": f"sandbox/{prefix}_{index}.txt",
                "timestamp": base + index * 0.02,
                "extension": ".txt",
                "previous_path": None,
                "new_path": None,
                "metadata": {
                    "entropy": entropy,
                    "exists": True,
                    "is_file": True,
                },
            }
        )

    for index in range(renames):
        events.append(
            {
                "action": "renamed",
                "path": f"sandbox/{prefix}_{index}.locked",
                "timestamp": base + 1.2 + index * 0.02,
                "extension": ".locked",
                "previous_path": f"sandbox/{prefix}_{index}.txt",
                "new_path": f"sandbox/{prefix}_{index}.locked",
                "metadata": {
                    "entropy": entropy,
                    "exists": True,
                    "is_file": True,
                },
            }
        )

    return events


def peak_of(engine: BurstDetectionEngine, events: list[dict]) -> tuple[float, str]:
    """Feed events and return the peak (score, level) seen during the burst."""
    peak_score = 0.0
    peak_level = "NORMAL"

    for event in events:
        analysis = engine.ingest(event)

        if analysis.score.total > peak_score:
            peak_score = analysis.score.total
            peak_level = analysis.level

    return peak_score, peak_level


@pytest.fixture
def runtime_in_tmp(tmp_path):
    """A real RuntimeManager wired entirely to ``tmp_path``."""
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "logs").mkdir(exist_ok=True)

    config_path = tmp_path / "rdrs.yaml"
    config_path.write_text(
        CONFIG_TEMPLATE.format(
            data=(tmp_path / "data").as_posix(),
            logs=(tmp_path / "logs").as_posix(),
            db=(tmp_path / "data" / "rdrs.db").as_posix(),
        ),
        encoding="utf-8",
    )

    RuntimeManager.reset_singleton()
    runtime = RuntimeManager(config_path=config_path)

    yield runtime

    try:
        if runtime.state.value in {"RUNNING", "DEGRADED"}:
            runtime.stop()
    except Exception:
        pass

    RuntimeManager.reset_singleton()


# ==========================================================================
# B-01  detection timestamp window
# ==========================================================================


def test_b01_historical_burst_is_detected():
    """A burst whose timestamps lag the sampling clock must still be detected.

    This is the reported failure: re-feeding a real persisted burst gave a
    window of 0 and a score of 0.0, because every event was purged on arrival.
    """
    engine = BurstDetectionEngine()

    # Six hours behind the wall clock, exactly like a replay of stored events.
    base = time.time() - 6 * 3600

    score, level = peak_of(engine, build_burst(base))

    assert score >= 70.0, f"historical burst scored only {score}"
    assert level == "CRITICAL"


def test_b01_lagging_event_is_not_purged_on_ingest():
    """A single stale event must survive its own ingestion."""
    engine = BurstDetectionEngine()

    analysis = engine.ingest(
        {
            "action": "modified",
            "path": "sandbox/a.txt",
            "timestamp": time.time() - 10_000,
            "extension": ".txt",
            "metadata": {"entropy": 7.9, "exists": True, "is_file": True},
        }
    )

    assert analysis.metrics.event_count == 1


def test_b01_live_burst_still_detected():
    """The live path must keep working: real-time stamps, real cadence."""
    engine = BurstDetectionEngine()

    score, level = peak_of(engine, build_burst(time.time()))

    assert score >= 70.0
    assert level == "CRITICAL"


def test_b01_window_still_expires_on_real_elapsed_time():
    """Fixing the anchor must not disable window expiry.

    ``evaluate`` is an explicit "assess as of now" call, so it honours the
    sampling clock and lets an idle window drain.
    """

    class FakeClock:
        def __init__(self) -> None:
            self.value = 0.0

        def __call__(self) -> float:
            return self.value

        def advance(self, seconds: float) -> None:
            self.value += seconds

    clock = FakeClock()
    engine = BurstDetectionEngine(clock=clock)

    for index in range(20):
        engine.ingest(
            {
                "action": "modified",
                "path": f"sandbox/f{index}.txt",
                "extension": ".txt",
                "metadata": {"entropy": 4.0, "exists": True, "is_file": True},
            }
        )

    assert engine.evaluate().rapid_encryption is True

    clock.advance(61)

    expired = engine.evaluate()

    assert expired.metrics.modified_files_per_minute == 0
    assert expired.rapid_encryption is False


def test_b01_stale_out_of_order_event_does_not_pollute_window():
    """A late-arriving ancient event must not inflate the counters."""
    engine = BurstDetectionEngine()
    now = time.time()

    for index in range(5):
        engine.ingest(
            {
                "action": "modified",
                "path": f"sandbox/live_{index}.txt",
                "timestamp": now + index,
                "extension": ".txt",
                "metadata": {"entropy": 1.0, "exists": True, "is_file": True},
            }
        )

    before = engine.last_analysis.metrics.event_count

    # An event from hours ago arrives last (out of order).
    engine.ingest(
        {
            "action": "modified",
            "path": "sandbox/ancient.txt",
            "timestamp": now - 7200,
            "extension": ".txt",
            "metadata": {"entropy": 1.0, "exists": True, "is_file": True},
        }
    )

    assert engine.last_analysis.metrics.event_count == before


def test_b01_detection_thresholds_are_unchanged():
    """The fix must not weaken any threshold or scoring weight."""
    assert DEFAULT_THRESHOLDS == {
        "window_seconds": 60.0,
        "modified_files_per_minute": 20.0,
        "renames": 10.0,
        "extension_changes": 5.0,
        "average_entropy": 7.0,
        "cpu_percent": 80.0,
    }

    assert DEFAULT_SCORING_WEIGHTS == {
        "rapid_encryption": 40.0,
        "mass_rename": 30.0,
        "high_entropy": 25.0,
        "cpu_spike": 15.0,
        "unknown_program": 10.0,
    }


def test_b01_persisted_replay_of_a_stored_burst_is_detected(tmp_path):
    """Replay a burst that was persisted to the database earlier.

    This is the exact failing path. ``record_file_event`` does not store the
    event's own timestamp -- ``created_at`` is the *write* time -- so a stored
    burst replayed later necessarily carries timestamps that lag the sampling
    clock. Pre-fix, every one of those events was purged on arrival and the
    burst scored 0.0.

    The events are backdated to model "the burst was recorded a while ago and
    is being replayed now", which is precisely how the original failure arose.
    """
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import text

    from rdrs.storage import DatabaseManager

    database = DatabaseManager(tmp_path / "replay.db")
    database.initialize()

    events = build_burst(time.time(), prefix="stored")

    for event in events:
        database.record_file_event(event)

    # Age the stored rows: the burst happened six hours before the replay.
    backdated = (
        datetime.now(timezone.utc) - timedelta(hours=6)
    ).replace(tzinfo=None)

    with database.engine.begin() as connection:
        connection.execute(
            text("UPDATE events SET created_at = :stamp"),
            {"stamp": backdated},
        )

    # Read back exactly as the replay does.
    with database.engine.connect() as connection:
        rows = list(
            connection.execute(
                text(
                    "SELECT path, action, previous_path, new_path, "
                    "metadata_json, created_at FROM events "
                    "ORDER BY created_at"
                )
            )
        )

    assert len(rows) == len(events)

    replayed: list[dict] = []

    for row in rows:
        stamp = (
            datetime.fromisoformat(str(row[5]))
            .replace(tzinfo=timezone.utc)
            .timestamp()
        )

        metadata = json.loads(row[4] or "{}")
        item = dict(metadata)
        item["timestamp"] = stamp
        item.setdefault("path", row[0])
        item["action"] = row[1]

        if row[3]:
            item["new_path"] = row[3]
        if row[2]:
            item["previous_path"] = row[2]

        replayed.append(item)

    # The replay is six hours behind the wall clock, which is the condition
    # that used to purge the entire burst.
    lag = time.time() - max(item["timestamp"] for item in replayed)
    assert lag > DEFAULT_THRESHOLDS["window_seconds"]

    engine = BurstDetectionEngine()
    peak_score, peak_level = peak_of(engine, replayed)

    assert peak_score >= 70.0, (
        f"persisted replay scored only {peak_score} "
        f"(window held {engine.last_analysis.metrics.event_count} events)"
    )
    assert peak_level == "CRITICAL"

    database.close()


def test_b01_normal_activity_stays_normal():
    """No false positive: a quiet stream must not raise a threat."""
    engine = BurstDetectionEngine()
    now = time.time()

    for index in range(5):
        engine.ingest(
            {
                "action": "modified",
                "path": f"sandbox/quiet_{index}.txt",
                "timestamp": now + index,
                "extension": ".txt",
                "metadata": {
                    "entropy": 3.2,
                    "exists": True,
                    "is_file": True,
                },
            }
        )

    analysis = engine.last_analysis

    assert analysis.level == "NORMAL"
    assert analysis.score.total < 40.0


# ==========================================================================
# B-02  detection -> scoring -> persistence -> alert -> incident
# ==========================================================================


def _feed(runtime: RuntimeManager, events: list[dict]) -> None:
    for event in events:
        runtime._handle_file_event(event)


def test_b02_one_burst_creates_exactly_one_incident(runtime_in_tmp):
    """One burst must reach CRITICAL and create exactly one incident."""
    runtime = runtime_in_tmp
    runtime.start()
    database = runtime._database

    _feed(runtime, build_burst(time.time()))

    analysis = runtime._last_analysis

    assert analysis.level == "CRITICAL", (
        f"burst reached only {analysis.level} "
        f"({analysis.score.total}); the window was wiped mid-burst"
    )
    assert database.count_records("incidents") == 1
    assert database.count_records("alerts") >= 1
    assert database.count_records("scores") >= 1


def test_b02_sustained_burst_does_not_duplicate_alerts(runtime_in_tmp):
    """A sustained burst at one severity must produce one response."""
    runtime = runtime_in_tmp
    runtime.start()
    database = runtime._database

    # A long, unchanging WARNING-level burst: enough modifications to fire
    # rapid_encryption and high_entropy, but no renames, so the band stays
    # WARNING and never escalates.
    now = time.time()
    events = []

    for index in range(120):
        events.append(
            {
                "action": "modified",
                "path": f"sandbox/sustained_{index}.txt",
                "timestamp": now + index * 0.02,
                "extension": ".txt",
                "previous_path": None,
                "new_path": None,
                "metadata": {
                    "entropy": 7.9,
                    "exists": True,
                    "is_file": True,
                },
            }
        )

    _feed(runtime, events)

    analysis = runtime._last_analysis

    assert analysis.level == "WARNING"
    assert database.count_records("scores") == 1, (
        "one unchanged burst must be scored once, not once per event"
    )
    assert database.count_records("alerts") == 1
    assert database.count_records("incidents") == 0


def test_b02_warning_band_creates_alert_but_no_incident(runtime_in_tmp):
    """Build Guide Phase 5: only Critical creates an incident."""
    runtime = runtime_in_tmp
    runtime.start()
    database = runtime._database

    now = time.time()

    for index in range(30):
        runtime._handle_file_event(
            {
                "action": "modified",
                "path": f"sandbox/warn_{index}.txt",
                "timestamp": now + index * 0.02,
                "extension": ".txt",
                "previous_path": None,
                "new_path": None,
                "metadata": {
                    "entropy": 7.9,
                    "exists": True,
                    "is_file": True,
                },
            }
        )

    assert database.count_records("alerts") >= 1
    assert database.count_records("incidents") == 0


def test_b02_new_burst_after_recovery_creates_a_new_incident(
    runtime_in_tmp,
):
    """Deduplication must not suppress a genuinely new burst."""
    runtime = runtime_in_tmp
    runtime.start()
    database = runtime._database

    first = time.time()
    _feed(runtime, build_burst(first, prefix="first"))

    assert database.count_records("incidents") == 1

    # Let the first burst age out of the window, which returns the analysis to
    # NORMAL and clears the dedup state.
    runtime._detector.reset()
    runtime._last_analysis = runtime._detector.last_analysis

    assert runtime._last_analysis.level == "NORMAL"

    second = time.time() + 1
    _feed(runtime, build_burst(second, prefix="second"))

    assert database.count_records("incidents") == 2


def test_b02_incident_records_the_real_score(runtime_in_tmp):
    """The persisted incident must carry the detector's own score."""
    runtime = runtime_in_tmp
    runtime.start()
    database = runtime._database

    _feed(runtime, build_burst(time.time()))

    detector_score = runtime._last_analysis.score.total

    with database.engine.connect() as connection:
        from sqlalchemy import text

        row = connection.execute(
            text("SELECT threat_score, threat_level FROM incidents")
        ).fetchone()

    assert row is not None
    assert float(row[0]) == float(detector_score)
    assert str(row[1]) == "CRITICAL"


def test_b02_long_escalating_burst_creates_exactly_one_incident(
    runtime_in_tmp,
):
    """A long burst that escalates must not pile up duplicate incidents.

    Reproduces the end-to-end failure: the pre-fix code wiped the window after
    every response and re-fired for each subsequent event, so one continuous
    burst produced several incidents (observed: 2) and a trail of duplicate
    scores and alerts (observed: 7 of each).
    """
    runtime = runtime_in_tmp
    runtime.start()
    database = runtime._database

    now = time.time()

    # A long, escalating burst: high-entropy writes accumulate, then a mass
    # rename pushes the score from WARNING into CRITICAL, and the activity
    # continues afterwards at CRITICAL.
    events: list[dict] = []

    for index in range(80):
        events.append(
            {
                "action": "modified",
                "path": f"sandbox/long_{index}.txt",
                "timestamp": now + index * 0.02,
                "extension": ".txt",
                "previous_path": None,
                "new_path": None,
                "metadata": {
                    "entropy": 7.9,
                    "exists": True,
                    "is_file": True,
                },
            }
        )

    for index in range(20):
        events.append(
            {
                "action": "renamed",
                "path": f"sandbox/long_{index}.locked",
                "timestamp": now + 2.0 + index * 0.02,
                "extension": ".locked",
                "previous_path": f"sandbox/long_{index}.txt",
                "new_path": f"sandbox/long_{index}.locked",
                "metadata": {
                    "entropy": 7.9,
                    "exists": True,
                    "is_file": True,
                },
            }
        )

    # Continue the burst well past the escalation point.
    for index in range(40):
        events.append(
            {
                "action": "modified",
                "path": f"sandbox/long_tail_{index}.txt",
                "timestamp": now + 3.0 + index * 0.02,
                "extension": ".txt",
                "previous_path": None,
                "new_path": None,
                "metadata": {
                    "entropy": 7.9,
                    "exists": True,
                    "is_file": True,
                },
            }
        )

    _feed(runtime, events)

    assert runtime._last_analysis.level == "CRITICAL"

    scores = database.count_records("scores")
    alerts = database.count_records("alerts")
    incidents = database.count_records("incidents")

    assert incidents == 1, (
        f"one burst created {incidents} incidents; duplicates are not allowed"
    )
    assert scores <= 2, (
        f"one burst produced {scores} scores; expected at most one per "
        "severity band"
    )
    assert alerts <= 2, (
        f"one burst produced {alerts} alerts; expected at most one per "
        "severity band"
    )
    assert scores == alerts


def test_b02_no_incident_for_normal_activity(runtime_in_tmp):
    """No false positive: quiet activity must create nothing."""
    runtime = runtime_in_tmp
    runtime.start()
    database = runtime._database

    now = time.time()

    for index in range(5):
        runtime._handle_file_event(
            {
                "action": "modified",
                "path": f"sandbox/quiet_{index}.txt",
                "timestamp": now + index,
                "extension": ".txt",
                "previous_path": None,
                "new_path": None,
                "metadata": {
                    "entropy": 2.0,
                    "exists": True,
                    "is_file": True,
                },
            }
        )

    assert database.count_records("scores") == 0
    assert database.count_records("alerts") == 0
    assert database.count_records("incidents") == 0


def test_b02_detector_window_survives_a_response():
    """The detector must not be reset by the response pipeline."""
    engine = BurstDetectionEngine()

    peak_score, _ = peak_of(engine, build_burst(time.time()))

    # The window still holds the burst, so the analysis is unchanged.
    assert engine.last_analysis.score.total == peak_score
    assert engine.last_analysis.metrics.event_count > 0


# ==========================================================================
# B-03 / B-04  dashboard encoding
# ==========================================================================

MOJIBAKE_EM_DASH = b"\xc3\xa2\xe2\x82\xac\xe2\x80\x9d"
MOJIBAKE_MIDDLE_DOT = b"\xc3\x82\xc2\xb7"
UTF8_BOM = b"\xef\xbb\xbf"


def test_b03_dashboard_has_no_mojibake():
    raw = DASHBOARD.read_bytes()

    assert raw.count(MOJIBAKE_EM_DASH) == 0, (
        "double-encoded em dash still present in the dashboard"
    )
    assert raw.count(MOJIBAKE_MIDDLE_DOT) == 0, (
        "double-encoded middle dot still present in the dashboard"
    )


def test_b03_dashboard_keeps_the_real_characters():
    raw = DASHBOARD.read_bytes()

    assert "\u2014".encode("utf-8") in raw, "em dash was lost"
    assert "\u00b7".encode("utf-8") in raw, "middle dot was lost"


def test_b03_footer_renders_correctly():
    text = DASHBOARD.read_text(encoding="utf-8")

    assert "RDRS \u00b7 Data provided by the RDRS API" in text


def test_b03_dashboard_is_valid_utf8():
    raw = DASHBOARD.read_bytes()

    raw.decode("utf-8")  # must not raise


def test_b04_dashboard_has_no_bom():
    raw = DASHBOARD.read_bytes()

    assert raw[:3] != UTF8_BOM, (
        "UTF-8 BOM present in a document served with charset=utf-8"
    )


def test_b03_served_dashboard_bytes_are_clean():
    """The bytes the API actually serves must be clean, not just the file."""
    from fastapi.testclient import TestClient

    from rdrs.api import app

    client = TestClient(app)
    response = client.get("/")

    assert response.status_code == 200
    assert "charset=utf-8" in response.headers.get("content-type", "").lower()

    raw = response.content

    assert raw[:3] != UTF8_BOM
    assert raw.count(MOJIBAKE_EM_DASH) == 0
    assert raw.count(MOJIBAKE_MIDDLE_DOT) == 0
    raw.decode("utf-8")


# ==========================================================================
# B-05  settings
# ==========================================================================


def test_b05_status_reports_config_before_start(runtime_in_tmp):
    """Cold start must expose the real configuration, not a block of nulls."""
    runtime = runtime_in_tmp

    payload = runtime.status()

    assert payload["state"] == "STOPPED"
    assert payload["config_available"] is True

    config = payload["config"]

    assert config["name"] == "rdrs"
    assert config["environment"] == "test"
    assert config["shutdown_timeout"] == 5.0
    assert config["log_level"] == "INFO"


def test_b05_status_config_matches_after_start(runtime_in_tmp):
    """Starting must not change what the Settings page reports."""
    runtime = runtime_in_tmp

    before = runtime.status()["config"]

    runtime.start()

    after = runtime.status()["config"]

    for key in ("name", "environment", "shutdown_timeout", "log_level"):
        assert before[key] == after[key]


def test_b05_status_reports_unavailable_when_config_missing(tmp_path):
    """A missing config must be reported as unavailable, not as blanks."""
    RuntimeManager.reset_singleton()

    runtime = RuntimeManager(
        config_path=tmp_path / "does-not-exist.yaml"
    )

    try:
        payload = runtime.status()

        assert payload["config_available"] is False
        assert payload["config"] == {}
    finally:
        RuntimeManager.reset_singleton()


def test_b05_dashboard_never_renders_openapi_placeholder():
    """The dashboard must not be able to display a schema hint as config."""
    text = DASHBOARD.read_text(encoding="utf-8")

    assert "PLACEHOLDER_VALUES" in text, (
        "no guard against rendering an OpenAPI schema hint as a value"
    )
    assert '"string"' in text, (
        "the schema-hint guard does not list the 'string' sentinel"
    )


# ==========================================================================
# B-06 / B-07  front end, executed
# ==========================================================================


def test_b06_b07_frontend_behaviour_is_executed():
    """Run the dashboard's own JavaScript and require every assertion to pass.

    Markup inspection cannot prove the Refresh button works, that the buttons
    are restored after a failed control request, or that a placeholder is never
    rendered. The harness loads the real ``<script>`` block into a Node vm with
    a DOM stub and drives the real functions.
    """
    node = shutil.which("node")

    assert node is not None, (
        "Node.js is required to execute the dashboard front-end regression "
        "harness. Install Node, or run it directly: "
        f"node {FRONTEND_HARNESS.relative_to(PROJECT_ROOT).as_posix()}"
    )

    assert FRONTEND_HARNESS.is_file(), (
        f"front-end harness missing: {FRONTEND_HARNESS}"
    )

    completed = subprocess.run(
        [node, str(FRONTEND_HARNESS)],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert completed.returncode == 0, (
        "front-end regression harness failed:\n"
        + completed.stdout
        + completed.stderr
    )

    assert "0 failed" in completed.stdout


def test_b06_refresh_has_overlap_guard_and_feedback():
    text = DASHBOARD.read_text(encoding="utf-8")

    assert "state.refreshing" in text, "no in-flight refresh guard"
    assert "Last refreshed" in text, "no visible refresh feedback"
    assert "state.controlPending" in text, "no in-flight control guard"


def test_b07_controls_restore_with_finally():
    text = DASHBOARD.read_text(encoding="utf-8")

    assert "CONTROL_STATES" in text, (
        "button state is not derived from the runtime's real capabilities"
    )
    assert "finally {" in text, "no guaranteed cleanup for the control buttons"


def test_b07_degraded_names_the_failing_subsystem():
    text = DASHBOARD.read_text(encoding="utf-8")

    assert "unhealthy_subsystems" in text, (
        "DEGRADED does not surface which subsystem is unhealthy"
    )


def test_b07_status_exposes_unhealthy_subsystems(runtime_in_tmp):
    """The backend must report the failing subsystem the UI needs to show."""
    runtime = runtime_in_tmp
    runtime.start()

    runtime._register_subsystem(
        "file_monitor",
        healthy=False,
        critical=True,
        details={"probe": True},
    )

    payload = runtime.status()

    assert payload["state"] == "DEGRADED"
    assert "file_monitor" in payload["unhealthy_subsystems"]

    # And it must recover.
    runtime._register_subsystem(
        "file_monitor",
        healthy=True,
        critical=True,
        details={"probe": True},
    )

    assert runtime.status()["state"] == "RUNNING"
    assert runtime.status()["unhealthy_subsystems"] == []


# ==========================================================================
# safety: the suite must never touch the operator's data
# ==========================================================================


def test_suite_does_not_delete_the_live_database():
    """The test suite must not destroy the project's database.

    An autouse fixture in ``test_runtime.py`` used to unlink
    ``<project>/data/rdrs.db`` before and after every test, relying on the
    sandbox to make the deletion fail. When it did not, running the suite
    destroyed a 12 MB live database.

    The guard below is deliberately narrow: tests may legitimately reference a
    ``data/rdrs.db`` *under tmp_path*, so only a path anchored on the project
    root is rejected.
    """
    this_file = Path(__file__).resolve()
    offenders: list[str] = []

    for candidate in sorted(PROJECT_ROOT.glob("tests/**/*.py")):
        if candidate.resolve() == this_file:
            continue

        for number, line in enumerate(
            candidate.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            # Anchored on the project directory (e.g. parents[2] / "data").
            if "rdrs.db" in line and "parents[" in line:
                offenders.append(f"{candidate.name}:{number}: {line.strip()}")

    assert not offenders, (
        "tests resolve the live database path from the project root:\n"
        + "\n".join(offenders)
    )
