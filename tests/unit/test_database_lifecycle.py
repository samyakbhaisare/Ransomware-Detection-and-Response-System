"""Regression tests for the shared-database lifecycle.

A read endpoint used to call `close_if_stopped()` in its `finally`, which
disposed the **shared** SQLite engine whenever monitoring was not running. With
two overlapping requests -- the dashboard's 5-second poll and a manual refresh,
for instance -- one request's cleanup could dispose the engine while another was
still reading, producing `RuntimeError: Database not initialized` and an HTTP 500
on `/dashboard-data` and friends.

These tests pin the behaviour that replaced it: read endpoints never close the
shared database, and the read surface stays healthy while monitoring is stopped.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rdrs.api import app
from rdrs.runtime import RuntimeManager

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _make_config(tmp_path: Path) -> Path:
    """Copy the project config into a temp tree with temp paths."""
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "logs").mkdir(exist_ok=True)

    body = (PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8")
    body = (
        body.replace(
            "data_dir: data",
            f"data_dir: {(tmp_path / 'data').as_posix()}",
        )
        .replace(
            "logs_dir: logs",
            f"logs_dir: {(tmp_path / 'logs').as_posix()}",
        )
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


@pytest.fixture()
def stopped_client(tmp_path):
    """A TestClient whose runtime has been stopped."""
    path = _make_config(tmp_path)

    RuntimeManager.reset_singleton()
    RuntimeManager(config_path=path)

    with TestClient(app) as client:
        client.post("/monitor/stop")
        yield client

    RuntimeManager.reset_singleton()


def test_read_endpoints_never_close_the_shared_database(stopped_client):
    """Repeated reads while stopped must all succeed."""
    for _ in range(5):
        for path in (
            "/status",
            "/dashboard-data",
            "/events",
            "/processes",
            "/alerts",
            "/incidents",
            "/reports",
        ):
            response = stopped_client.get(path)
            assert response.status_code == 200, f"{path} -> {response.status_code}"


def test_database_is_still_initialised_after_reads_while_stopped(stopped_client):
    """The engine must survive a read; closing it was the defect."""
    assert stopped_client.get("/dashboard-data").status_code == 200

    runtime = RuntimeManager()
    assert runtime._database is not None, "the manager was discarded"
    assert runtime._database.engine is not None, "the shared engine was closed"


def test_stop_then_start_cycle_keeps_reads_working(stopped_client):
    """A full stop -> read -> start -> read cycle must not break the API."""
    assert stopped_client.post("/monitor/stop").status_code in {200, 409}
    assert stopped_client.get("/status").status_code == 200
    assert stopped_client.get("/dashboard-data").status_code == 200

    assert stopped_client.post("/monitor/start").status_code == 200
    assert stopped_client.get("/status").status_code == 200
    assert stopped_client.get("/dashboard-data").status_code == 200


def test_stop_is_repeatable_and_reads_survive_each_time(stopped_client):
    """Three stop/poll cycles, as the dashboard would drive them."""
    for _ in range(3):
        stopped_client.post("/monitor/stop")

        for _ in range(4):
            assert stopped_client.get("/dashboard-data").status_code == 200

        assert stopped_client.post("/monitor/start").status_code == 200
        assert stopped_client.get("/dashboard-data").status_code == 200


def test_the_shared_engine_is_not_disposed_by_a_read():
    """`close_if_stopped` must no longer exist as a per-request cleanup."""
    import rdrs.api as api_module

    assert not hasattr(api_module, "close_if_stopped"), (
        "the per-request database close was reintroduced; it races concurrent "
        "reads and produces HTTP 500 while monitoring is stopped"
    )
