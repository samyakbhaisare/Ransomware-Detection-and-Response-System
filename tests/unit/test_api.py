"""API endpoint tests.

Every test runs against a throwaway configuration and database.

This file previously built a module-level ``TestClient(app)`` with no runtime
configuration, so the shared runtime fell back to the project's real
``config.yaml``. ``test_settings_endpoint`` then POSTed to ``/settings``, and
because the endpoint persists changes so they survive a restart, **running the
suite rewrote the shipped configuration file** -- silently stripping its
comments and settings. The client is now bound to a temp config.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rdrs.api import app
from rdrs.runtime import RuntimeManager

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def client(tmp_path):
    """A TestClient whose runtime is bound to a temporary configuration."""
    (tmp_path / "data").mkdir()
    (tmp_path / "logs").mkdir()

    base = (PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8")

    config_path = tmp_path / "rdrs.yaml"
    config_path.write_text(
        base.replace(
            "data_dir: data", f"data_dir: {(tmp_path / 'data').as_posix()}"
        )
        .replace(
            "logs_dir: logs", f"logs_dir: {(tmp_path / 'logs').as_posix()}"
        )
        .replace(
            "db_path: data\\rdrs.db",
            f"db_path: {(tmp_path / 'data' / 'rdrs.db').as_posix()}",
        )
        .replace(
            "  watch_paths:\n    - data\n",
            f"  watch_paths:\n    - {(tmp_path / 'data').as_posix()}\n",
        ),
        encoding="utf-8",
    )

    RuntimeManager.reset_singleton()
    RuntimeManager(config_path=config_path)

    with TestClient(app) as test_client:
        yield test_client

    RuntimeManager.reset_singleton()


def test_health_endpoint(client):
    response = client.get("/health")

    assert response.status_code == 200

    data = response.json()
    assert "state" in data
    assert "healthy" in data
    assert "subsystems" in data
    assert "degraded" in data


def test_status_endpoint(client):
    response = client.get("/status")

    assert response.status_code == 200

    data = response.json()
    assert "state" in data
    assert "healthy" in data
    assert "started_at" in data
    assert "subsystems" in data
    assert "config" in data


def test_alerts_endpoint(client):
    response = client.get("/alerts")

    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_events_endpoint(client):
    response = client.get("/events")

    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_alerts_limit_validation(client):
    response = client.get("/alerts?limit=0")

    assert response.status_code == 422


def test_events_limit_validation(client):
    response = client.get("/events?limit=0")

    assert response.status_code == 422


def test_scan_endpoint(client, tmp_path):
    # Scan a dedicated folder: the fixture's config file also lives in tmp_path.
    scan_root = tmp_path / "scan_me"
    scan_root.mkdir()

    sample = scan_root / "sample.txt"
    sample.write_text("RDRS scan test", encoding="utf-8")

    response = client.post("/scan", params={"path": str(scan_root)})

    assert response.status_code == 200

    data = response.json()
    assert data["path"] == str(scan_root)
    assert data["count"] == 1
    assert str(sample) in data["files"]


def test_reports_endpoint(client):
    response = client.get("/reports")

    assert response.status_code == 200

    data = response.json()
    assert "count" in data
    assert "incidents" in data
    assert data["count"] == len(data["incidents"])


def test_settings_endpoint(client, tmp_path):
    """POST /settings applies and persists -- to the TEMP config, not the repo."""
    # Settings are editable only while monitoring is stopped (FSD).
    client.post("/monitor/stop")

    response = client.post(
        "/settings",
        json={
            "environment": "test",
            "shutdown_timeout": 15,
        },
    )

    assert response.status_code == 200

    data = response.json()
    assert data["environment"] == "test"
    assert data["shutdown_timeout"] == 15.0
    assert data["name"] == "rdrs"

    # The change was persisted to the temporary file ...
    from rdrs.config import RuntimeConfig

    on_disk = RuntimeConfig.load(tmp_path / "rdrs.yaml")
    assert on_disk.environment == "test"
    assert on_disk.shutdown_timeout == 15.0

    # ... not to the shipped configuration. (tests/conftest.py fails the whole
    # run if any test modifies config.yaml; this asserts it directly too.)
    shipped = (PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8")
    assert "watch_paths" in shipped
    assert RuntimeConfig.load(PROJECT_ROOT / "config.yaml").environment == "test"
