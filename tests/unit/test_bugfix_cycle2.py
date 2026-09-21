"""Regression tests for the second consolidated cycle (BUG 4 … BUG 7 + findings).

Each test targets a defect observed in live verification, so a reintroduction
fails here.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rdrs.api import app, prune_reports
from rdrs.runtime import RuntimeManager, RuntimeStatus
from rdrs.storage import DatabaseManager

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _config(tmp_path: Path, *, persistence: bool) -> Path:
    """A config rooted entirely in tmp_path, with persistence pinned."""
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    body = (PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8")

    body = body.replace(
        "  enabled: false", "  enabled: true"
    ).replace(
        "  enabled: true", f"  enabled: {'true' if persistence else 'false'}"
    )

    body = (
        body.replace("data_dir: data", f"data_dir: {(tmp_path / 'data').as_posix()}")
        .replace("logs_dir: logs", f"logs_dir: {(tmp_path / 'logs').as_posix()}")
        .replace(
            "db_path: data\\rdrs.db",
            f"db_path: {(tmp_path / 'data' / 'rdrs.db').as_posix()}",
        )
        .replace(
            "  watch_paths:\n    - data\n",
            f"  watch_paths:\n    - {(tmp_path / 'data').as_posix()}\n",
        )
    )

    path = tmp_path / "rdrs.yaml"
    path.write_text(body, encoding="utf-8")

    return path


# ==========================================================================
# BUG 4 — the runtime must restart after a stop
# ==========================================================================


def test_bug4_start_stop_start_without_a_process_restart(tmp_path):
    RuntimeManager.reset_singleton()
    runtime = RuntimeManager(config_path=_config(tmp_path, persistence=True))

    try:
        runtime.start()
        assert runtime.state == RuntimeStatus.RUNNING

        runtime.stop()
        assert runtime.state == RuntimeStatus.STOPPED

        # The whole point: a second start must work.
        runtime.start()
        assert runtime.state == RuntimeStatus.RUNNING

        runtime.stop()
        runtime.start()
        assert runtime.state == RuntimeStatus.RUNNING
    finally:
        runtime.stop()
        RuntimeManager.reset_singleton()


def test_bug4_polling_while_stopped_does_not_block_a_restart(tmp_path):
    """`stop()` releases the database on purpose; `start()` must rebuild it.

    The defect was not that the handle is released -- it is that nothing put it
    back, so every later start raised and returned 409.
    """
    RuntimeManager.reset_singleton()
    runtime = RuntimeManager(config_path=_config(tmp_path, persistence=True))

    try:
        runtime.start()
        assert runtime._database is not None

        runtime.stop()

        # Reads while stopped must keep working.
        for _ in range(5):
            status = runtime.status()
            assert status["state"] == RuntimeStatus.STOPPED.value

        # The handle is gone, but a restart must rebuild it.
        runtime.start()
        assert runtime.state == RuntimeStatus.RUNNING
        assert runtime._database is not None
        assert runtime._database.engine is not None
    finally:
        runtime.stop()
        RuntimeManager.reset_singleton()


def test_bug4_dashboard_endpoints_do_not_500_after_a_stop(tmp_path):
    RuntimeManager.reset_singleton()
    RuntimeManager(config_path=_config(tmp_path, persistence=True))

    with TestClient(app) as client:
        assert client.post("/monitor/stop").status_code == 200

        # Poll exactly as the dashboard does, several times.
        for _ in range(5):
            assert client.get("/status").status_code == 200
            assert client.get("/dashboard-data").status_code == 200

        # And the runtime can be started again through the API.
        assert client.post("/monitor/start").status_code == 200
        assert client.get("/status").json()["state"] == "RUNNING"

        assert client.post("/monitor/stop").status_code == 200

    RuntimeManager.reset_singleton()


def test_bug4_persistence_disabled_still_starts(tmp_path):
    """`persistence.enabled: false` must not leave the runtime unstartable."""
    RuntimeManager.reset_singleton()
    runtime = RuntimeManager(config_path=_config(tmp_path, persistence=False))

    try:
        runtime.start()

        assert runtime.state == RuntimeStatus.RUNNING
        assert runtime._database is not None
        assert runtime._database.durable is False
    finally:
        runtime.stop()
        RuntimeManager.reset_singleton()


def test_bug4_api_starts_with_persistence_disabled(tmp_path):
    RuntimeManager.reset_singleton()
    RuntimeManager(config_path=_config(tmp_path, persistence=False))

    with TestClient(app) as client:
        # The app lifespan starts the runtime, so this already proves that a
        # disabled persistence setting does not prevent start-up.
        assert client.get("/status").json()["state"] == "RUNNING"
        assert client.get("/dashboard-data").status_code == 200

        # And the stop -> start cycle works through the API.
        assert client.post("/monitor/stop").status_code == 200
        assert client.post("/monitor/start").status_code == 200
        assert client.get("/status").json()["state"] == "RUNNING"
        assert client.get("/dashboard-data").status_code == 200

        assert client.post("/monitor/stop").status_code == 200

    RuntimeManager.reset_singleton()


# ==========================================================================
# BUG 5 / BUG 7 — retention must never break report generation
# ==========================================================================


class _ExplodingPath(type(Path())):
    """A path whose unlink() raises something that is not an OSError."""

    def unlink(self, *args, **kwargs):
        raise RuntimeError("delete guard: SAFE_DELETE_BULK_CONFIRM_REQUIRED")


def test_bug5_prune_survives_a_non_oserror_delete_failure(tmp_path):
    """The delete guard raises a non-OSError; it must not escape."""
    files = []

    for index in range(6):
        path = tmp_path / f"incident_5_2026010{index}T000000Z.json"
        path.write_text("{}", encoding="utf-8")
        files.append(path)

    original = Path.unlink

    def exploding_unlink(self, *args, **kwargs):
        if self.suffix == ".json":
            raise RuntimeError("SAFE_DELETE_BULK_CONFIRM_REQUIRED")
        return original(self, *args, **kwargs)

    Path.unlink = exploding_unlink

    try:
        # Must not raise.
        result = prune_reports(tmp_path, 5, keep=2)
    finally:
        Path.unlink = original

    assert result["removed"] == []
    assert len(result["blocked"]) == 4


def test_bug5_prune_never_raises_on_an_unlistable_directory(tmp_path):
    missing = tmp_path / "does-not-exist"

    result = prune_reports(missing, 1)

    assert result["removed"] == []
    assert result["considered"] == 0


def test_bug7_retention_caps_the_directory(tmp_path):
    for index in range(30):
        for suffix in (".json", ".csv"):
            (tmp_path / f"incident_8_20260101T0000{index:02d}Z{suffix}").write_text(
                "{}", encoding="utf-8"
            )

    result = prune_reports(tmp_path, 8, keep=10)

    assert len(list(tmp_path.glob("incident_8_*"))) == 10
    assert len(result["removed"]) == 50
    assert result["blocked"] == []


def test_bug7_newest_json_and_csv_always_survive(tmp_path):
    for index in range(8):
        (tmp_path / f"incident_9_2026010{index}T000000Z.json").write_text(
            "{}", encoding="utf-8"
        )

    csv_file = tmp_path / "incident_9_20260101T000000Z.csv"
    csv_file.write_text("a,b", encoding="utf-8")

    prune_reports(tmp_path, 9, keep=2)

    assert csv_file.is_file()
    assert list(tmp_path.glob("incident_9_*.json"))


def test_bug7_prune_leaves_other_incidents_alone(tmp_path):
    for index in range(6):
        (tmp_path / f"incident_1_2026010{index}T000000Z.json").write_text(
            "{}", encoding="utf-8"
        )

    other = tmp_path / "incident_2_20260101T000000Z.json"
    other.write_text("{}", encoding="utf-8")

    unrelated = tmp_path / "notes.txt"
    unrelated.write_text("keep", encoding="utf-8")

    prune_reports(tmp_path, 1, keep=2)

    assert other.is_file()
    assert unrelated.is_file()


def test_bug5_generate_succeeds_when_cleanup_is_blocked(tmp_path):
    """Report generation must return 200 even if retention cannot delete."""
    RuntimeManager.reset_singleton()
    RuntimeManager(config_path=_config(tmp_path, persistence=True))

    with TestClient(app) as client:
        # The lifespan has already started the runtime.
        runtime = RuntimeManager()

        # Record a real incident so the report has something to describe.
        database = runtime._database
        score_id = database.record_score(95.0, "CRITICAL", "cycle-2 test")
        incident_id = database.record_incident(
            "CRITICAL",
            "Ransomware-like burst detected; signals: rapid_encryption",
            evidence={"score": 95.0},
            threat_score=95.0,
            threat_level="CRITICAL",
            affected_files=["data/x.txt"],
            score_id=score_id,
        )

        original = Path.unlink

        def blocked_unlink(self, *args, **kwargs):
            if "reports" in str(self):
                raise RuntimeError("SAFE_DELETE_BULK_CONFIRM_REQUIRED")
            return original(self, *args, **kwargs)

        Path.unlink = blocked_unlink

        try:
            response = client.post(
                "/reports/generate",
                params={"incident_id": incident_id, "format": "json"},
            )

            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["generated"] is True
            assert Path(payload["path"]).is_file()
        finally:
            Path.unlink = original

    RuntimeManager.reset_singleton()


# ==========================================================================
# BUG 6 — the frontend must report report-generation failures
# ==========================================================================


def test_bug6_frontend_wraps_report_generation_in_try_catch():
    html = (
        PROJECT_ROOT / "src" / "rdrs" / "dashboard" / "index.html"
    ).read_text(encoding="utf-8")

    body = html[html.index("async function generateReport") :]
    body = body[: body.index("\n    }")]

    assert "try {" in body
    assert "catch (error)" in body
    assert "finally {" in body
    assert "report failed" in body


def test_bug6_frontend_guards_against_duplicate_generation():
    html = (
        PROJECT_ROOT / "src" / "rdrs" / "dashboard" / "index.html"
    ).read_text(encoding="utf-8")

    assert "reportPending" in html
    assert "state.reportPending = false" in html


# ==========================================================================
# Autonomous discovery — the watchdog thread must not die on a failed persist
# ==========================================================================


def test_persist_failure_does_not_escape_the_monitor():
    """A persist that always fails must not propagate out of _handle_event."""
    from rdrs.monitoring import DirectoryMonitor

    class ExplodingDatabase:
        def record_file_event(self, payload):
            raise RuntimeError("cannot commit transaction")

    monitor = DirectoryMonitor(
        Path("data"),
        database=ExplodingDatabase(),
    )

    from rdrs.monitoring import FileEvent

    event = FileEvent(
        path="data/x.txt",
        action="created",
        timestamp="2026-09-19T00:00:00+00:00",
    )

    # Must not raise: this runs on a watchdog thread.
    monitor._handle_event(event)


def test_in_memory_database_is_not_durable(tmp_path):
    manager = DatabaseManager(None)

    assert manager.durable is False
    assert manager.db_path is None

    manager.initialize()
    assert manager.engine is not None

    manager.close()
    assert manager.engine is None


def test_file_backed_database_is_durable(tmp_path):
    target = tmp_path / "durable.db"
    manager = DatabaseManager(target)

    assert manager.durable is True
    assert manager.db_path == target

    manager.initialize()
    assert target.is_file()

    manager.close()

def test_bug4_in_memory_data_survives_a_stop_start(tmp_path):
    """With persistence off, stopping must not erase what was collected.

    `initialize()` builds a fresh engine, and for an in-memory database that
    means an empty one -- so re-initialising on every start wiped the history
    whenever monitoring was stopped and started.
    """
    for persistence in (True, False):
        RuntimeManager.reset_singleton()
        runtime = RuntimeManager(
            config_path=_config(tmp_path / str(persistence), persistence=persistence)
        )

        try:
            runtime.start()
            runtime._database.record_score(88.0, "CRITICAL", "survival probe")
            before = runtime._database.count_records("scores")
            assert before == 1

            runtime.stop()
            runtime.start()

            assert runtime._database.count_records("scores") == before, (
                "data was lost across stop/start with persistence=%s" % persistence
            )
        finally:
            runtime.stop()
            RuntimeManager.reset_singleton()

def test_bug4_in_memory_data_survives_a_stop_start(tmp_path):
    """With persistence off, stopping must not erase what was collected.

    `initialize()` builds a fresh engine, and for an in-memory database that
    means an empty one -- so re-initialising on every start wiped the history
    whenever monitoring was stopped and started.
    """
    for persistence in (True, False):
        RuntimeManager.reset_singleton()
        runtime = RuntimeManager(
            config_path=_config(tmp_path / str(persistence), persistence=persistence)
        )

        try:
            runtime.start()
            runtime._database.record_score(88.0, "CRITICAL", "survival probe")
            before = runtime._database.count_records("scores")
            assert before == 1

            runtime.stop()
            runtime.start()

            assert runtime._database.count_records("scores") == before, (
                "data was lost across stop/start with persistence=%s" % persistence
            )
        finally:
            runtime.stop()
            RuntimeManager.reset_singleton()

# ==========================================================================
# Discovery D-8 / D-9 — the CLI must honour the configuration, and stop must
# be idempotent
# ==========================================================================


def test_d8_cli_honours_the_configured_database_path(tmp_path):
    """`RDRS_CONFIG_PATH` must redirect the database, not just the config.

    `_build_runtime()` used to pass explicit data_dir/logs_dir/db_path pinned to
    the project root, so the CLI silently operated on the project's own
    database no matter which configuration it was told to read.
    """
    import subprocess
    import sys

    root = tmp_path / "cli"
    (root / "data").mkdir(parents=True)
    (root / "logs").mkdir(parents=True)

    body = (PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8")
    body = body.replace("  enabled: false", "  enabled: true")
    body = (
        body.replace("data_dir: data", f"data_dir: {(root / 'data').as_posix()}")
        .replace("logs_dir: logs", f"logs_dir: {(root / 'logs').as_posix()}")
        .replace(
            "db_path: data\\rdrs.db",
            f"db_path: {(root / 'data' / 'rdrs.db').as_posix()}",
        )
        .replace(
            "  watch_paths://n    - data\n",
            f"  watch_paths://n    - {(root / 'data').as_posix()}\n",
        )
    )

    config_path = root / "rdrs.yaml"
    config_path.write_text(body, encoding="utf-8")

    env = {**os.environ, "RDRS_CONFIG_PATH": str(config_path)}

    subprocess.run(
        [sys.executable, "-m", "rdrs.cli", "start"],
        capture_output=True, text=True, cwd=PROJECT_ROOT, env=env, timeout=120,
    )
    subprocess.run(
        [sys.executable, "-m", "rdrs.cli", "stop"],
        capture_output=True, text=True, cwd=PROJECT_ROOT, env=env, timeout=120,
    )

    assert (root / "data" / "rdrs.db").is_file(), (
        "the CLI did not use the database named by the configuration"
    )


def test_d9_cli_stop_is_idempotent(tmp_path):
    """`stop` on an already-stopped runtime must succeed, not exit 1."""
    import subprocess
    import sys

    config_path = _config(tmp_path, persistence=True)
    env = {**os.environ, "RDRS_CONFIG_PATH": str(config_path)}

    first = subprocess.run(
        [sys.executable, "-m", "rdrs.cli", "stop"],
        capture_output=True, text=True, cwd=PROJECT_ROOT, env=env, timeout=120,
    )
    second = subprocess.run(
        [sys.executable, "-m", "rdrs.cli", "stop"],
        capture_output=True, text=True, cwd=PROJECT_ROOT, env=env, timeout=120,
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert "STOPPED" in second.stdout.upper()
