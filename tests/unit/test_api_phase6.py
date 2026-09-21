"""FR-10 + Build Guide Phase 6 regression tests.

Phase 6 requires FastAPI endpoints:

    GET  /health, /status, /alerts, /events, /reports
    POST /scan, /settings

JSON from every endpoint, and a working automatic documentation page.

Done when: "You can call /status and /alerts in the browser and get JSON
responses back."
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rdrs.api import app
from rdrs.runtime import RuntimeManager

PROJECT_ROOT = Path(__file__).resolve().parents[2]

REQUIRED_GET = ["/health", "/status", "/alerts", "/events", "/reports"]


@pytest.fixture()
def client(tmp_path):
    for name in ("data", "logs", "sandbox"):
        (tmp_path / name).mkdir()

    base = (PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8")

    config_path = tmp_path / "rdrs.yaml"
    config_path.write_text(
        base.replace("data_dir: data", f"data_dir: {(tmp_path / 'data').as_posix()}")
        .replace("logs_dir: logs", f"logs_dir: {(tmp_path / 'logs').as_posix()}")
        .replace(
            "db_path: data\\rdrs.db",
            f"db_path: {(tmp_path / 'data' / 'rdrs.db').as_posix()}",
        )
        .replace(
            "  watch_paths:\n    - data\n",
            f"  watch_paths:\n    - {(tmp_path / 'sandbox').as_posix()}\n",
        ),
        encoding="utf-8",
    )

    RuntimeManager.reset_singleton()
    RuntimeManager(config_path=config_path)

    with TestClient(app) as test_client:
        test_client.sandbox = tmp_path / "sandbox"
        yield test_client

    RuntimeManager.reset_singleton()


# --------------------------------------------------------------------------
# the seven required endpoints
# --------------------------------------------------------------------------


@pytest.mark.parametrize("path", REQUIRED_GET)
def test_required_get_endpoints_return_json(client, path):
    response = client.get(path)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() is not None


def test_post_scan_returns_json(client):
    sample = client.sandbox / "sample.txt"
    sample.write_text("scan me", encoding="utf-8")

    response = client.post("/scan", params={"path": str(client.sandbox)})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")

    payload = response.json()

    assert payload["path"] == str(client.sandbox)
    assert payload["count"] == 1


def test_post_settings_returns_json(client):
    # Settings are only editable while monitoring is stopped (FSD).
    assert client.post("/monitor/stop").status_code == 200

    response = client.post("/settings", json={"environment": "staging"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["environment"] == "staging"


# --------------------------------------------------------------------------
# Done-when
# --------------------------------------------------------------------------


def test_done_when_status_and_alerts_return_json(client):
    status = client.get("/status")
    alerts = client.get("/alerts")

    assert status.status_code == 200
    assert isinstance(status.json(), dict)

    assert alerts.status_code == 200
    assert isinstance(alerts.json(), list)


# --------------------------------------------------------------------------
# automatic documentation
# --------------------------------------------------------------------------


def test_docs_page_is_served(client):
    assert client.get("/docs").status_code == 200


def test_openapi_schema_documents_every_required_endpoint(client):
    schema = client.get("/openapi.json")

    assert schema.status_code == 200

    paths = schema.json()["paths"]

    for path in REQUIRED_GET:
        assert path in paths, f"{path} is missing from the OpenAPI schema"

    for path in ("/scan", "/settings"):
        assert path in paths, f"{path} is missing from the OpenAPI schema"
        assert "post" in paths[path]


# --------------------------------------------------------------------------
# validation and error handling
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["/events?limit=0", "/alerts?limit=0", "/processes?limit=0", "/events?limit=99999"],
)
def test_invalid_limits_are_rejected(client, path):
    assert client.get(path).status_code == 422


def test_scan_without_a_path_is_rejected(client):
    assert client.post("/scan").status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "   "},
        {"environment": "  "},
        {"log_level": "LOUD"},
        {"shutdown_timeout": 0},
    ],
)
def test_invalid_settings_are_rejected(client, payload):
    # Settings are editable only while monitoring is stopped (FSD), so the
    # validation path is reached in the STOPPED state.
    client.post("/monitor/stop")

    assert client.post("/settings", json=payload).status_code == 400


# --------------------------------------------------------------------------
# every endpoint the dashboard calls must exist
# --------------------------------------------------------------------------


def test_every_dashboard_endpoint_exists():
    """No dead UI control: every path the dashboard calls is a real route."""
    import re

    html = (PROJECT_ROOT / "src" / "rdrs" / "dashboard" / "index.html").read_text(
        encoding="utf-8"
    )

    block = re.search(r"const API = \{(.*?)\};", html, re.S).group(1)
    paths = set(re.findall(r':\s*"(/[^"]+)"', block))

    assert paths, "no API paths found in the dashboard"

    source = (PROJECT_ROOT / "src" / "rdrs" / "api.py").read_text(
        encoding="utf-8-sig"
    )

    missing = sorted(path for path in paths if f'"{path}"' not in source)

    assert not missing, f"the dashboard calls endpoints that do not exist: {missing}"
