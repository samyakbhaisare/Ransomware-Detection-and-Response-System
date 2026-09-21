"""Persistence-mode and history behaviour.

These pin the behaviour that was reported as "the dashboard went to zero when I
changed settings":

* **Persistence Disabled** selects a non-durable in-memory database, so the
  dashboard legitimately reads empty. It must **not** delete, truncate or
  recreate the durable SQLite file, and every row in it must survive untouched.
* **Persistence Enabled** reads the durable file, so previously recorded events,
  alerts, incidents and scores become visible again.
* Changing an unrelated setting, and stopping/starting monitoring, must not reset
  history or drop configuration.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rdrs.api import app
from rdrs.runtime import RuntimeManager, RuntimeStatus

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASE_CONFIG = (PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8")

HISTORY_TABLES = ("events", "alerts", "incidents", "scores")


def _write_config(work: Path, *, persistence: bool) -> Path:
    sandbox = work / "sandbox"
    data = work / "data"
    logs = work / "logs"
    for directory in (sandbox, data, logs):
        directory.mkdir(parents=True, exist_ok=True)

    out = []
    for line in BASE_CONFIG.splitlines():
        stripped = line.strip()
        if stripped.startswith("enabled:") and ("true" in line or "false" in line):
            out.append("  enabled: %s" % ("true" if persistence else "false"))
        else:
            out.append(line)

    body = "\n".join(out) + "\n"
    body = (
        body.replace("data_dir: data", f"data_dir: {data.as_posix()}")
        .replace("logs_dir: logs", f"logs_dir: {logs.as_posix()}")
        .replace("db_path: data\\rdrs.db", f"db_path: {(data / 'rdrs.db').as_posix()}")
        .replace(
            "  watch_paths:\n    - data\n",
            f"  watch_paths:\n    - {sandbox.as_posix()}\n",
        )
    )

    path = work / "rdrs.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def _counts(client: TestClient) -> dict:
    summary = (client.get("/dashboard-data").json().get("summary")) or {}
    return {
        "events": summary.get("event_count", 0),
        "alerts": summary.get("alert_count", 0),
        "incidents": summary.get("incident_count", 0),
        "scores": summary.get("score_count", 0),
    }


def _on_disk(db_file: Path) -> dict:
    conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
    try:
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in HISTORY_TABLES
        }
    finally:
        conn.close()


def _seed(work: Path, db_file: Path, *, persistence: bool = True) -> dict:
    """Start the runtime, write rows directly, and return the on-disk counts."""
    path = _write_config(work, persistence=persistence)
    RuntimeManager.reset_singleton()
    runtime = RuntimeManager(config_path=path)
    runtime.start()

    database = runtime._database
    database.record_score(88.0, "CRITICAL", "history seed")
    counts = _on_disk(db_file) if db_file.is_file() else {}

    runtime.stop()
    RuntimeManager.reset_singleton()
    return counts


@pytest.fixture()
def work(tmp_path):
    return tmp_path


@pytest.fixture(autouse=True)
def _leave_no_runtime_behind():
    """Guarantee this module hands the next test a clean runtime.

    These tests deliberately start and stop the runtime several times, including
    through the FastAPI lifespan. Without an explicit teardown the singleton can
    survive a test in a non-STOPPED state, and a later watchdog test in the same
    session then fails to receive filesystem events.
    """
    yield

    try:
        runtime = RuntimeManager()
        if runtime.state in {
            RuntimeStatus.STARTING,
            RuntimeStatus.RUNNING,
            RuntimeStatus.DEGRADED,
            RuntimeStatus.SHUTTING_DOWN,
        }:
            runtime.stop()
    except Exception:
        pass

    RuntimeManager.reset_singleton()

    # Give the watchdog observers a moment to release their handles before the
    # next test starts its own.
    time.sleep(0.2)


def test_disabled_mode_does_not_delete_the_durable_database(work):
    """Disabling persistence must leave the file and every row intact."""
    db_file = work / "data" / "rdrs.db"

    seeded = _seed(work, db_file, persistence=True)
    assert db_file.is_file(), "the durable database was not created"
    assert seeded["scores"] >= 1

    size_before = db_file.stat().st_size

    # Now switch to the non-durable mode. The TestClient lifespan owns
    # start/stop, so the runtime is not started explicitly here.
    path = _write_config(work, persistence=False)
    RuntimeManager.reset_singleton()

    with TestClient(app) as client:
        runtime = RuntimeManager()
        assert runtime._database.durable is False, "Disabled must select an in-memory database"
        assert runtime._database.db_path is None, "an in-memory database has no file path"

        # The dashboard is legitimately empty in this mode ...
        empty = _counts(client)
        assert empty["events"] == 0 and empty["alerts"] == 0

    RuntimeManager.reset_singleton()

    # ... but the durable file is untouched.
    assert db_file.is_file(), "Disabled mode deleted the durable database"
    assert db_file.stat().st_size == size_before, "Disabled mode rewrote the durable file"
    assert _on_disk(db_file) == seeded, "Disabled mode changed durable rows"


def test_reenabling_persistence_restores_the_visible_history(work):
    """Switching back to Enabled must show the previous history again."""
    db_file = work / "data" / "rdrs.db"

    seeded = _seed(work, db_file, persistence=True)

    path = _write_config(work, persistence=False)
    RuntimeManager.reset_singleton()
    RuntimeManager(config_path=path).start()
    RuntimeManager().stop()
    RuntimeManager.reset_singleton()

    path = _write_config(work, persistence=True)
    RuntimeManager.reset_singleton()
    RuntimeManager(config_path=path).start()

    assert _on_disk(db_file) == seeded, "history changed across the mode switch"
    RuntimeManager().stop()
    RuntimeManager.reset_singleton()


def test_stop_then_start_preserves_history_in_both_modes(work):
    """A stop/start cycle inside one process must not reset anything."""
    for persistence in (True, False):
        path = _write_config(work, persistence=persistence)
        RuntimeManager.reset_singleton()
        runtime = RuntimeManager(config_path=path)
        runtime.start()

        runtime._database.record_score(70.0, "CRITICAL", f"persistence={persistence}")
        before = runtime._database.count_records("scores")

        runtime.stop()
        runtime.start()
        after = runtime._database.count_records("scores")

        assert after >= before, (
            f"history was lost across stop/start with persistence={persistence}"
        )

        runtime.stop()
        RuntimeManager.reset_singleton()


def test_an_unrelated_setting_change_keeps_history_and_other_config(work):
    """Changing one setting must not reset history or other configuration."""
    db_file = work / "data" / "rdrs.db"

    seeded = _seed(work, db_file, persistence=True)

    path = _write_config(work, persistence=True)
    RuntimeManager.reset_singleton()

    # The TestClient lifespan starts and stops the runtime for us.
    with TestClient(app) as client:
        runtime = RuntimeManager()
        original = runtime._config
        original_thresholds = dict(original.detection_thresholds)
        original_weights = dict(original.scoring_weights)
        original_watch_paths = list(original.watch_paths)

        client.post("/monitor/stop")
        response = client.post("/settings", json={"environment": "test"})
        assert response.status_code == 200, response.text

        reloaded = runtime._config
        assert dict(reloaded.detection_thresholds) == original_thresholds, (
            "an unrelated setting change altered the detection thresholds"
        )
        assert dict(reloaded.scoring_weights) == original_weights, (
            "an unrelated setting change altered the scoring weights"
        )
        assert list(reloaded.watch_paths) == original_watch_paths, (
            "an unrelated setting change altered watch_paths"
        )

    RuntimeManager.reset_singleton()

    assert _on_disk(db_file) == seeded, "history changed across the settings change"
