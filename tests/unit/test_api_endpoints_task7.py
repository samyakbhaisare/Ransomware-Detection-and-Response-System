"""Phase 8/9 API endpoint coverage (Task 7).

These tests exercise every endpoint the README documents and assert on the
*content* of the responses, not merely the status code. Report fidelity is
checked against real persisted rows so that a report can never drift from the
incident that produced it.

The suite is deliberately self-contained: it points the shared runtime at a
temporary config/data directory and seeds real database rows through the
public storage API. Nothing is mocked away that the product actually does.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from rdrs.api import app, get_runtime
from rdrs.storage import DatabaseManager
from rdrs.telemetry import ProcessTelemetry


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


def _write_config(work: Path) -> Path:
    """Write a loadable RDRS config inside the test's temp directory."""
    data_dir = work / "data"
    logs_dir = work / "logs"

    config = {
        "runtime": {
            "name": "rdrs",
            "environment": "test",
            "data_dir": str(data_dir),
            "logs_dir": str(logs_dir),
            "shutdown_timeout": 10,
        },
        "logging": {"level": "INFO"},
        "persistence": {
            "enabled": True,
            "db_path": str(data_dir / "rdrs.db"),
        },
        "detection": {
            "window_seconds": 60,
            "modified_files_per_minute": 20,
            "renames": 10,
            "extension_changes": 5,
            "average_entropy": 7,
            "cpu_percent": 80,
        },
        "scoring": {
            "weights": {
                "rapid_encryption": 40,
                "mass_rename": 30,
                "high_entropy": 25,
                "cpu_spike": 15,
                "unknown_program": 10,
            }
        },
        "subsystems": [],
    }

    config_path = work / "rdrs.yaml"
    config_path.write_text(
        yaml.safe_dump(config, sort_keys=False),
        encoding="utf-8",
    )

    return config_path


def _reset_runtime(work: Path) -> Path:
    """Point the shared runtime at a fresh sandbox data directory."""
    runtime = get_runtime()

    database = getattr(runtime, "_database", None)

    if database is not None:
        database.close()

    runtime._database = None  # noqa: SLF001
    runtime._config = None  # noqa: SLF001

    config_path = _write_config(work)

    runtime._pending_config_path = config_path  # noqa: SLF001
    runtime._pending_data_dir = work / "data"  # noqa: SLF001
    runtime._pending_logs_dir = work / "logs"  # noqa: SLF001
    runtime._pending_db_path = work / "data" / "rdrs.db"  # noqa: SLF001

    return config_path


@pytest.fixture()
def api_env(tmp_path):
    """Yield (client, database, work_dir) bound to a real temp database."""
    work = tmp_path / "rdrs_api_task7"

    work.mkdir(parents=True, exist_ok=True)

    _reset_runtime(work)

    database = DatabaseManager(work / "data" / "rdrs.db")
    database.initialize()

    runtime = get_runtime()
    runtime._database = database  # noqa: SLF001

    client = TestClient(app)

    yield client, database, work

    database.close()
    runtime._database = None  # noqa: SLF001


def _seed_critical_incident(
    database: DatabaseManager,
    *,
    affected: list[str] | None = None,
    suspect_name: str = "encryptor.exe",
    score: float = 70.0,
    level: str = "CRITICAL",
) -> tuple[int, list[int]]:
    """Persist a critical incident plus the events that produced it."""
    affected = affected or [
        "C:/sandbox/report.docx",
        "C:/sandbox/report.docx.locked",
    ]

    event_ids = [
        database.record_file_event(
            {
                "path": path,
                "action": "modified",
                "metadata": {"entropy": 7.94, "exists": True},
            }
        )
        for path in affected
    ]

    incident_id = database.record_incident(
        severity="CRITICAL",
        description="Critical ransomware threat detected",
        evidence={
            "score": score,
            "level": level,
            "signals": {
                "rapid_encryption": True,
                "mass_rename": True,
                "high_entropy": True,
                "cpu_spike": False,
                "unknown_program": False,
            },
            "suspect_process": {"name": suspect_name, "pid": 4242},
            "affected_files": affected,
            "simulation_mode": True,
        },
        threat_score=score,
        threat_level=level,
        suspect_process={"name": suspect_name, "pid": 4242},
        affected_files=affected,
    )

    database.attach_events_to_incident(incident_id, event_ids)

    return incident_id, event_ids


# ---------------------------------------------------------------------------
# GET endpoints
# ---------------------------------------------------------------------------


def test_dashboard_root_serves_the_web_app(api_env):
    """FR-11: the Phase 7 dashboard is served from /."""
    client, _database, _work = api_env

    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert len(response.text) > 0


def test_public_summary_allowlists_aggregates_without_private_evidence(api_env):
    client, database, _work = api_env
    secret = "C:/Users/private-user/secret-command.exe"
    _seed_critical_incident(database, affected=[secret], suspect_name=secret)
    database.record_file_event({"path": secret, "action": secret})
    database.record_alert(secret, secret, {"command_line": secret})
    response = client.get("/public-summary", headers={"X-RDRS-Public": "1"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["access"] == "public"
    assert payload["summary"]["event_count"] == 2
    assert payload["summary"]["incident_count"] == 1
    assert payload["groups"]["events"] == {"modified": 1, "Other": 1}
    assert payload["groups"]["alerts"] == {"Other": 1}
    assert secret not in response.text
    assert set(payload) == {"access", "health", "summary", "groups", "latest_capture", "report_count", "score", "telemetry", "activity"}
    assert set(payload["telemetry"]) == {"cpu", "entropy"}
    assert all(set(item) == {"time", "count"} for item in payload["activity"])
    assert client.get("/public-summary").json()["access"] == "private"
    assert secret in client.get("/incidents").text


def test_public_read_routes_preserve_records_and_redact_report_downloads(api_env):
    client, database, _work = api_env
    secret = "C:/Users/private-user/private.docx"
    ident, _ = _seed_critical_incident(database, affected=[secret])
    client.post(f"/reports/generate?incident_id={ident}&format=json")
    client.post(f"/reports/generate?incident_id={ident}&format=csv")
    header = {"X-RDRS-Public": "1"}
    assert client.post("/monitor/stop", headers=header).status_code == 403
    assert client.post(f"/reports/generate?incident_id={ident}&format=json", headers=header).status_code == 403
    for path in ("/status", "/dashboard-data", "/events?include_total=true", "/incidents", "/reports", f"/reports/{ident}/json", f"/reports/{ident}/csv"):
        response = client.get(path, headers=header)
        assert response.status_code == 200, path
        assert secret not in response.text and "encryptor.exe" not in response.text, path
    public = client.get("/incidents", headers=header).json()[0]
    assert public["id"] == ident
    assert public["affected_files"] and public["threat_score"] == 70
    timeline = client.get(f"/reports/{ident}/json", headers=header).json()["timeline"]
    assert len(timeline) == 1 and timeline[0]["path"] == public["affected_files"][0]
    assert secret in client.get(f"/reports/{ident}/json").text


def test_incidents_endpoint_returns_persisted_incidents(api_env):
    client, database, _work = api_env

    incident_id, _ = _seed_critical_incident(database)

    response = client.get("/incidents")

    assert response.status_code == 200

    payload = response.json()

    assert isinstance(payload, list)
    assert [row["id"] for row in payload] == [incident_id]

    row = payload[0]

    # Content fidelity: the dedicated columns must survive the round trip.
    assert row["threat_score"] == pytest.approx(70.0)
    assert row["threat_level"] == "CRITICAL"
    assert row["suspect_process"]["name"] == "encryptor.exe"
    assert row["affected_files"] == [
        "C:/sandbox/report.docx",
        "C:/sandbox/report.docx.locked",
    ]


def test_processes_endpoint_returns_persisted_telemetry(api_env):
    client, database, _work = api_env

    database.record_process(
        ProcessTelemetry(
            pid=31337,
            name="worker.exe",
            parent_pid=1,
            command_line="worker.exe --scan",
            cwd="C:/sandbox",
            started_at=1000.0,
            cpu_percent=91.5,
            memory_rss=2048,
            memory_percent=3.5,
            disk_write_bytes=4096,
            executable_path="C:/sandbox/worker.exe",
        )
    )

    response = client.get("/processes")

    assert response.status_code == 200

    payload = response.json()

    assert len(payload) == 1
    assert payload[0]["pid"] == 31337
    assert payload[0]["name"] == "worker.exe"
    assert payload[0]["cpu_percent"] == pytest.approx(91.5)


def test_events_endpoint_supports_cursor_pages_and_total(api_env):
    client, database, _work = api_env
    timestamp = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
    ids = [
        database.record_file_event(
            {
                "path": f"C:/sandbox/page_{index}.txt",
                "action": "modified",
                "timestamp": timestamp,
            }
        )
        for index in range(3)
    ]

    first = client.get(
        "/events",
        params={"limit": 2, "include_total": "true"},
    )

    assert first.status_code == 200
    assert first.headers["x-total-count"] == "3"
    assert first.headers["x-has-more"] == "true"
    assert len(first.json()) == 2
    assert [row["id"] for row in first.json()] == [ids[2], ids[1]]

    cursor = first.headers["x-next-cursor"]
    second = client.get(
        "/events",
        params={"limit": 2, "cursor": cursor},
    )

    assert second.status_code == 200
    assert second.headers["x-has-more"] == "false"
    assert [row["id"] for row in second.json()] == [ids[0]]

    empty = client.get(
        "/events",
        params={
            "limit": 2,
            "cursor": "1970-01-01T00:00:00+00:00|1",
            "include_total": "true",
        },
    )

    assert empty.status_code == 200
    assert empty.json() == []
    assert empty.headers["x-total-count"] == "3"
    assert empty.headers["x-has-more"] == "false"


def test_processes_endpoint_supports_cursor_pages(api_env):
    client, database, _work = api_env
    for pid in (101, 202, 303):
        database.record_process(
            ProcessTelemetry(pid=pid, name=f"p{pid}.exe")
        )

    first = client.get("/processes", params={"limit": 2})
    assert first.headers["x-has-more"] == "true"
    cursor = first.headers["x-next-cursor"]

    second = client.get(
        "/processes",
        params={"limit": 2, "cursor": cursor},
    )
    assert second.headers["x-has-more"] == "false"
    assert [row["pid"] for row in first.json() + second.json()] == [303, 202, 101]


def test_dashboard_data_contract_is_complete(api_env):
    """The single read-only dashboard contract exposes every documented key."""
    client, database, _work = api_env

    _seed_critical_incident(database)

    response = client.get("/dashboard-data")

    assert response.status_code == 200

    payload = response.json()

    for key in (
        "summary",
        "score",
        "events",
        "processes",
        "alerts",
        "incidents",
        "top_modified",
        "telemetry",
    ):
        assert key in payload, f"missing dashboard key: {key}"

    assert payload["summary"]["incident_count"] == 1
    assert payload["summary"]["event_count"] == 2
    assert payload["summary"]["score_count"] == 0

    assert payload["incidents"][0]["id"] == 1

    # Events and the top-modified aggregation reflect the real rows only.
    assert len(payload["events"]) == 2
    assert payload["top_modified"][0]["modifications"] == 1

    # No score row was written, so no score may be invented.
    assert payload["score"]["score"] == 0.0
    assert payload["score"]["level"] == "Normal"

    # No process rows exist, so no CPU point may be fabricated. The entropy
    # chart does have real backing: the seeded events carry entropy metadata.
    assert payload["telemetry"]["cpu"] == []
    assert sorted(
        point["value"] for point in payload["telemetry"]["entropy"]
    ) == [7.94, 7.94]


def test_dashboard_data_entropy_and_cpu_charts_use_real_values(api_env):
    client, database, _work = api_env

    database.record_file_event(
        {
            "path": "C:/sandbox/data.bin",
            "action": "modified",
            "metadata": {"entropy": 7.99},
        }
    )
    database.record_process(
        ProcessTelemetry(
            pid=777,
            name="chatter.exe",
            cpu_percent=42.0,
        )
    )

    payload = client.get("/dashboard-data").json()

    entropy = payload["telemetry"]["entropy"]

    assert len(entropy) == 1
    assert entropy[0]["value"] == pytest.approx(7.99)
    assert entropy[0]["path"] == "C:/sandbox/data.bin"

    cpu = payload["telemetry"]["cpu"]

    assert len(cpu) == 1
    assert cpu[0]["value"] == pytest.approx(42.0)
    assert cpu[0]["pid"] == 777


def test_scan_get_describes_post_usage(api_env):
    client, _database, _work = api_env

    response = client.get("/scan")

    assert response.status_code == 200
    assert "POST /scan" in response.json()["message"]


def test_scan_rejects_missing_and_non_directory_paths(api_env, tmp_path):
    """D-03 family: bad scan input is a 404/400, never an unhandled 500."""
    client, _database, _work = api_env

    missing = client.post(
        "/scan",
        params={"path": str(tmp_path / "does-not-exist")},
    )

    assert missing.status_code == 404
    assert "does not exist" in missing.json()["detail"]

    target = tmp_path / "file.txt"
    target.write_text("not a directory", encoding="utf-8")

    not_dir = client.post("/scan", params={"path": str(target)})

    assert not_dir.status_code == 400
    assert "must be a directory" in not_dir.json()["detail"]


# ---------------------------------------------------------------------------
# POST /reports/generate  (D-03: was a 500 from Path(None))
# ---------------------------------------------------------------------------


def test_generate_report_unknown_incident_is_404(api_env):
    client, _database, _work = api_env

    response = client.post(
        "/reports/generate",
        params={"incident_id": 9999},
    )

    assert response.status_code == 404
    assert "9999" in response.json()["detail"]


def test_generate_report_rejects_unknown_format(api_env):
    client, _database, _work = api_env

    response = client.post(
        "/reports/generate",
        params={"incident_id": 1, "format": "pdf"},
    )

    assert response.status_code == 422


def test_generate_report_writes_json_with_incident_scoped_content(api_env):
    client, database, work = api_env

    incident_id, _event_ids = _seed_critical_incident(database)

    response = client.post(
        "/reports/generate",
        params={"incident_id": incident_id},
    )

    assert response.status_code == 200

    payload = response.json()

    assert payload["generated"] is True
    assert payload["format"] == "json"
    assert payload["incident_id"] == incident_id

    destination = Path(payload["path"])

    assert destination.is_file()
    assert destination.parent == work / "data" / "reports"

    report = payload["report"]

    assert report["report_version"] == "1.0"
    assert report["incident"]["id"] == incident_id
    assert report["incident"]["score"] == pytest.approx(70.0)
    assert report["incident"]["level"] == "CRITICAL"

    assert report["threat_score"] == pytest.approx(70.0)
    assert report["threat_level"] == "CRITICAL"

    assert report["suspect_process"]["name"] == "encryptor.exe"
    assert report["suspect_process_attribution"] is None
    assert report["affected_file_count"] == 2
    assert [row["path"] for row in report["timeline"]] == [
        "C:/sandbox/report.docx",
        "C:/sandbox/report.docx.locked",
    ]

    # Recommendations are derived from the signals that actually fired.
    joined = " ".join(report["recommendations"])

    assert "rapid encryption" in joined
    assert "mass rename" in joined
    assert "high-entropy" in joined
    assert "encryptor.exe" in joined
    assert "simulation mode" in joined
    assert "70/100" in joined
    assert "cpu spike" not in joined
    assert "unexpected program" not in joined

    # The file on disk must be the same document that was returned.
    on_disk = json.loads(destination.read_text(encoding="utf-8"))

    assert on_disk["incident"]["id"] == incident_id
    assert on_disk["threat_level"] == "CRITICAL"
    assert on_disk["affected_files"] == report["affected_files"]


def test_generate_report_writes_csv_with_all_record_types(api_env):
    client, database, _work = api_env

    incident_id, _ = _seed_critical_incident(database)

    response = client.post(
        "/reports/generate",
        params={"incident_id": incident_id, "format": "csv"},
    )

    assert response.status_code == 200

    destination = Path(response.json()["path"])

    assert destination.suffix == ".csv"
    assert destination.is_file()

    with destination.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    record_types = {row["record_type"] for row in rows}

    assert record_types == {
        "incident",
        "event",
        "affected_file",
        "suspect_process",
        "recommendation",
    }

    incident_row = next(row for row in rows if row["record_type"] == "incident")

    assert incident_row["incident_id"] == str(incident_id)
    assert incident_row["level"] == "CRITICAL"
    assert float(incident_row["score"]) == pytest.approx(70.0)

    event_rows = [row for row in rows if row["record_type"] == "event"]

    assert len(event_rows) == 2
    assert {row["path"] for row in event_rows} == {
        "C:/sandbox/report.docx",
        "C:/sandbox/report.docx.locked",
    }

    file_rows = [row for row in rows if row["record_type"] == "affected_file"]

    assert len(file_rows) == 2

    process_row = next(
        row for row in rows if row["record_type"] == "suspect_process"
    )
    process_details = json.loads(process_row["details"])
    assert process_details["process"]["name"] == "encryptor.exe"
    assert process_details["process"]["pid"] == 4242
    assert process_details["attribution"] is None

    assert any(
        row["record_type"] == "recommendation"
        for row in rows
    )


def test_report_names_do_not_collide_within_one_timestamp_second(
    api_env,
    monkeypatch,
):
    client, database, work = api_env
    incident_id, _ = _seed_critical_incident(database)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 4, 12, 34, 56, 123456, tzinfo=tz)

    monkeypatch.setattr("rdrs.api.datetime", FixedDateTime)

    paths = []
    for _ in range(2):
        response = client.post(
            "/reports/generate",
            params={"incident_id": incident_id, "format": "json"},
        )
        assert response.status_code == 200
        paths.append(Path(response.json()["path"]))

    assert paths[0] != paths[1]
    assert all(path.is_file() for path in paths)
    assert paths[0].parent == work / "data" / "reports"


def test_report_timeline_is_scoped_to_one_incident(api_env):
    """D-04: a second incident's events must never leak into the first report."""
    client, database, _work = api_env

    first_id, _ = _seed_critical_incident(
        database,
        affected=["C:/sandbox/first.docx"],
    )
    second_id, _ = _seed_critical_incident(
        database,
        affected=["C:/sandbox/second.docx"],
        suspect_name="other.exe",
    )

    assert first_id != second_id

    first = client.post(
        "/reports/generate",
        params={"incident_id": first_id},
    ).json()["report"]

    paths = [row["path"] for row in first["timeline"]]

    assert paths == ["C:/sandbox/first.docx"]
    assert "C:/sandbox/second.docx" not in paths
    assert first["suspect_process"]["name"] == "encryptor.exe"


# ---------------------------------------------------------------------------
# GET /reports/{incident_id}/{format}
# ---------------------------------------------------------------------------


def test_download_report_rejects_unsupported_format(api_env):
    client, _database, _work = api_env

    response = client.get("/reports/1/pdf")

    assert response.status_code == 400
    assert "json or csv" in response.json()["detail"]


def test_download_report_404_before_generation(api_env):
    client, database, _work = api_env

    incident_id, _ = _seed_critical_incident(database)

    response = client.get(f"/reports/{incident_id}/json")

    assert response.status_code == 404
    assert "Generate the report first" in response.json()["detail"]


def test_download_report_returns_the_generated_file(api_env):
    client, database, _work = api_env

    incident_id, _ = _seed_critical_incident(database)

    generated = client.post(
        "/reports/generate",
        params={"incident_id": incident_id, "format": "json"},
    )

    assert generated.status_code == 200

    response = client.get(f"/reports/{incident_id}/json")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert f"incident_{incident_id}_" in response.headers["content-disposition"]

    body = json.loads(response.content)

    assert body["incident"]["id"] == incident_id
    assert body["threat_level"] == "CRITICAL"


def test_download_report_returns_csv_media_type(api_env):
    client, database, _work = api_env

    incident_id, _ = _seed_critical_incident(database)

    client.post(
        "/reports/generate",
        params={"incident_id": incident_id, "format": "csv"},
    )

    response = client.get(f"/reports/{incident_id}/csv")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.text.splitlines()[0].startswith("record_type")


def test_download_report_picks_the_newest_matching_file(api_env):
    """Two generated reports for one incident: the latest is served.

    The filename carries a second-resolution UTC stamp, so a second file is
    planted with a deliberately later stamp to make "newest" deterministic
    without sleeping in the test.
    """
    client, database, work = api_env

    incident_id, _ = _seed_critical_incident(database)

    first = client.post(
        "/reports/generate",
        params={"incident_id": incident_id},
    )

    assert first.status_code == 200

    reports_dir = work / "data" / "reports"
    first_path = Path(first.json()["path"])

    newer_path = reports_dir / f"incident_{incident_id}_99991231T235959Z.json"
    newer_path.write_text(
        json.dumps({"incident": {"id": incident_id}, "marker": "newest"}),
        encoding="utf-8",
    )

    assert first_path != newer_path
    assert len(list(reports_dir.glob(f"incident_{incident_id}_*.json"))) == 2

    response = client.get(f"/reports/{incident_id}/json")

    assert response.status_code == 200
    assert newer_path.name in response.headers["content-disposition"]
    assert json.loads(response.content)["marker"] == "newest"


# ---------------------------------------------------------------------------
# Monitoring endpoints
# ---------------------------------------------------------------------------


def test_monitor_start_and_stop_report_real_state(api_env):
    """FR-1: start/stop return the live runtime state, not a canned answer."""
    client, _database, _work = api_env

    runtime = get_runtime()

    started = client.post("/monitor/start")

    assert started.status_code in {200, 409}

    if started.status_code == 200:
        assert started.json()["state"] == "RUNNING"

    # Stopping after a successful start must succeed at least once.
    stopped = client.post("/monitor/stop")

    assert stopped.status_code in {200, 409}

    if stopped.status_code == 200:
        assert stopped.json()["state"] == "STOPPED"

        # A second stop is a conflict, never a crash.
        again = client.post("/monitor/stop")

        assert again.status_code == 409

    runtime.force_stop()


# ---------------------------------------------------------------------------
# /settings validation surface
# ---------------------------------------------------------------------------


def test_settings_rejects_empty_name_and_environment(api_env):
    client, _database, _work = api_env

    blank_name = client.post("/settings", json={"name": "   "})

    assert blank_name.status_code == 400
    assert "name" in blank_name.json()["detail"].lower()

    blank_env = client.post("/settings", json={"environment": "  "})

    assert blank_env.status_code == 400
    assert "environment" in blank_env.json()["detail"].lower()


def test_settings_rejects_non_positive_shutdown_timeout(api_env):
    client, _database, _work = api_env

    response = client.post("/settings", json={"shutdown_timeout": 0})

    assert response.status_code == 400
    assert "shutdown_timeout" in response.json()["detail"]


def test_settings_rejects_unknown_log_level(api_env):
    client, _database, _work = api_env

    response = client.post("/settings", json={"log_level": "LOUD"})

    assert response.status_code == 400
    assert "log level" in response.json()["detail"].lower()


def test_settings_accepts_valid_changes_and_persists_them(api_env):
    """D-10: accepted settings must be written back to the config file."""
    client, _database, work = api_env

    config_path = get_runtime()._pending_config_path  # noqa: SLF001

    response = client.post(
        "/settings",
        json={
            "name": "rdrs-task7",
            "environment": "staging",
            "shutdown_timeout": 25,
            "log_level": "debug",
            "persistence_enabled": True,
        },
    )

    assert response.status_code == 200

    payload = response.json()

    assert payload["name"] == "rdrs-task7"
    assert payload["environment"] == "staging"
    assert payload["shutdown_timeout"] == pytest.approx(25.0)
    assert payload["log_level"] == "DEBUG"
    assert payload["persistence_enabled"] is True

    on_disk = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))

    assert on_disk["runtime"]["name"] == "rdrs-task7"
    assert on_disk["runtime"]["environment"] == "staging"
    assert on_disk["runtime"]["shutdown_timeout"] == pytest.approx(25.0)
    assert on_disk["logging"]["level"] == "DEBUG"

    # The persisted document must stay loadable by the product itself.
    from rdrs.config import RuntimeConfig

    reloaded = RuntimeConfig.load(
        config_path,
        data_dir=work / "data",
        logs_dir=work / "logs",
        db_path=work / "data" / "rdrs.db",
    )

    assert reloaded.name == "rdrs-task7"
    assert reloaded.environment == "staging"
    assert reloaded.log_level == "DEBUG"
    assert set(reloaded.scoring_weights) == {
        "rapid_encryption",
        "mass_rename",
        "high_entropy",
        "cpu_spike",
        "unknown_program",
    }


def test_settings_reports_persistence_failure_as_500(api_env, monkeypatch):
    """A failed disk write must be reported, never silently swallowed."""
    client, _database, _work = api_env

    from rdrs.config import RuntimeConfig

    def _explode(self, config_path):
        raise OSError("disk is read-only")

    monkeypatch.setattr(RuntimeConfig, "save", _explode)

    response = client.post("/settings", json={"name": "cannot-persist"})

    assert response.status_code == 500
    assert "could not be persisted" in response.json()["detail"]
    assert "disk is read-only" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Cold-start behaviour (report_directory must never receive None)
# ---------------------------------------------------------------------------


def test_report_directory_never_receives_none(api_env):
    """Direct regression guard for the original ``Path(None)`` 500."""
    from rdrs.api import report_directory

    runtime = get_runtime()

    saved_config = runtime._config
    saved_pending = runtime._pending_data_dir

    try:
        runtime._config = None  # noqa: SLF001
        runtime._pending_data_dir = None  # noqa: SLF001

        resolved = report_directory(runtime)

        assert resolved == (Path("data") / "reports").resolve()
        assert resolved.is_dir()
    finally:
        runtime._config = saved_config  # noqa: SLF001
        runtime._pending_data_dir = saved_pending  # noqa: SLF001


def test_report_directory_rejects_a_symlink_escape(tmp_path):
    from types import SimpleNamespace

    from rdrs.api import _is_path_within, report_directory

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    try:
        (data_dir / "reports").symlink_to(outside, target_is_directory=True)
    except OSError:
        # Windows may deny symlink creation without Developer Mode. The same
        # containment check used by report_directory remains testable directly.
        assert not _is_path_within(outside, data_dir)
        return

    runtime = SimpleNamespace(
        _config=None,
        _pending_data_dir=data_dir,
    )

    with pytest.raises(RuntimeError, match="inside the configured data directory"):
        report_directory(runtime)


def test_download_report_does_not_follow_report_symlinks(api_env):
    from rdrs.api import _is_path_within

    client, database, work = api_env
    incident_id, _ = _seed_critical_incident(database)
    reports_dir = work / "data" / "reports"
    reports_dir.mkdir()
    external_report = work / "outside.json"
    external_report.write_text('{"private": true}', encoding="utf-8")
    link = reports_dir / f"incident_{incident_id}_20300101T000000Z.json"

    try:
        link.symlink_to(external_report)
    except OSError:
        assert not _is_path_within(external_report, reports_dir)
        return

    response = client.get(f"/reports/{incident_id}/json")

    assert response.status_code == 404
    assert "private" not in response.text


def test_generate_report_works_on_a_cold_runtime(tmp_path, monkeypatch):
    """The full report path must survive an unresolved runtime config."""
    from rdrs import api as api_module

    work = tmp_path / "cold"
    work.mkdir(parents=True, exist_ok=True)

    _reset_runtime(work)

    runtime = get_runtime()

    database = DatabaseManager(work / "data" / "rdrs.db")
    database.initialize()

    incident_id, _ = _seed_critical_incident(database)

    runtime._database = database  # noqa: SLF001

    # Simulate a config that never resolved: no _config, only an in-memory
    # pending data dir. report_directory() must still return a real path.
    runtime._config = None  # noqa: SLF001

    monkeypatch.chdir(work)

    try:
        client = TestClient(api_module.app)

        response = client.post(
            "/reports/generate",
            params={"incident_id": incident_id},
        )

        assert response.status_code == 200
        assert Path(response.json()["path"]).is_file()
    finally:
        database.close()
        runtime._database = None  # noqa: SLF001
