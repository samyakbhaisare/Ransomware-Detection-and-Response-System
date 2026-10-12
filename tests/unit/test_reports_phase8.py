"""FR-12 + Build Guide Phase 8 regression tests.

Phase 8 requires an incident report in **JSON and CSV** (required; HTML/PDF
optional) including:

* a timeline of events
* the threat score
* the list of affected files
* the suspect process
* recommendations

Done when: "After an incident you can generate at least a JSON report and a CSV
report file."
"""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rdrs.api import app
from rdrs.runtime import RuntimeManager

PROJECT_ROOT = Path(__file__).resolve().parents[2]


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
        yield test_client

    RuntimeManager.reset_singleton()


def trigger_incident(client, tmp_path) -> int:
    """Drive a real burst so a reportable incident exists.

    Real files are written into the watched folder so the FILE MONITOR records
    genuine events. Calling ``runtime._handle_file_event`` alone only feeds the
    detector -- persistence is the monitor's job -- so the incident timeline
    would be empty and the test would not reflect the real pipeline.
    """
    sandbox = tmp_path / "sandbox"
    runtime = RuntimeManager()

    # Let the process monitor capture so the incident carries a suspect.
    deadline = time.time() + 45

    while time.time() < deadline:
        if runtime._database.count_records("processes"):
            break
        time.sleep(1)

    # Real evidence files: the monitor persists these as events.
    for index in range(12):
        (sandbox / f"doc_{index}.docx").write_bytes(b"REAL-CONTENT-" * 200)

    deadline = time.time() + 30

    while time.time() < deadline:
        if runtime._database.count_records("events") >= 12:
            break
        time.sleep(0.5)

    now = time.time()

    for index in range(60):
        runtime._handle_file_event(
            {
                "action": "modified",
                "path": str(sandbox / f"burst_{index}.txt"),
                "timestamp": now + index * 0.02,
                "extension": ".txt",
                "metadata": {"entropy": 7.95, "exists": True, "is_file": True},
            }
        )

    for index in range(12):
        runtime._handle_file_event(
            {
                "action": "renamed",
                "path": str(sandbox / f"doc_{index}.locked"),
                "timestamp": now + 1.2 + index * 0.02,
                "extension": ".locked",
                "previous_path": str(sandbox / f"doc_{index}.docx"),
                "new_path": str(sandbox / f"doc_{index}.locked"),
                "metadata": {"entropy": 7.95, "exists": True, "is_file": True},
            }
        )

    time.sleep(1.5)

    incidents = client.get("/incidents").json()

    assert incidents, "the burst did not create an incident"

    return incidents[0]["id"]


# --------------------------------------------------------------------------
# Done-when: JSON and CSV report files
# --------------------------------------------------------------------------


def test_done_when_json_and_csv_reports_are_generated(client, tmp_path):
    incident_id = trigger_incident(client, tmp_path)

    json_response = client.post(
        "/reports/generate",
        params={"format": "json", "incident_id": incident_id},
    )
    csv_response = client.post(
        "/reports/generate",
        params={"format": "csv", "incident_id": incident_id},
    )

    assert json_response.status_code == 200
    assert csv_response.status_code == 200

    json_path = Path(json_response.json()["path"])
    csv_path = Path(csv_response.json()["path"])

    assert json_path.is_file()
    assert csv_path.is_file()
    assert json_path.suffix == ".json"
    assert csv_path.suffix == ".csv"


# --------------------------------------------------------------------------
# the five required contents
# --------------------------------------------------------------------------


def test_json_report_contains_every_required_section(client, tmp_path):
    incident_id = trigger_incident(client, tmp_path)

    response = client.post(
        "/reports/generate",
        params={"format": "json", "incident_id": incident_id},
    )

    report = json.loads(Path(response.json()["path"]).read_text(encoding="utf-8"))

    # 1. a timeline of events
    assert "timeline" in report
    assert report["timeline"], "the report has no event timeline"
    assert report["timeline"][0].get("path")

    # 2. the threat score
    assert report["threat_score"] >= 70.0
    assert report["threat_level"] == "CRITICAL"

    # 3. the list of affected files
    assert report["affected_files"]
    assert report["affected_file_count"] == len(report["affected_files"])

    # 4. the suspect process
    assert report["suspect_process"] is not None
    assert report["suspect_process"].get("name")
    assert "heuristic" in report["suspect_process_attribution"].lower()
    assert "not causally attributed" in report["suspect_process_attribution"].lower()

    # 5. recommendations
    assert report["recommendations"]
    assert all(isinstance(item, str) for item in report["recommendations"])


def test_json_report_is_valid_and_self_describing(client, tmp_path):
    incident_id = trigger_incident(client, tmp_path)

    response = client.post(
        "/reports/generate",
        params={"format": "json", "incident_id": incident_id},
    )

    report = json.loads(Path(response.json()["path"]).read_text(encoding="utf-8"))

    assert report["report_version"]
    assert report["generated_at"]
    assert report["incident"]["id"] == incident_id


def test_csv_report_has_a_header_and_rows(client, tmp_path):
    incident_id = trigger_incident(client, tmp_path)

    response = client.post(
        "/reports/generate",
        params={"format": "csv", "incident_id": incident_id},
    )

    path = Path(response.json()["path"])

    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))

    assert len(rows) > 1, "the CSV report has no data rows"

    header = rows[0]

    for column in (
        "record_type",
        "timestamp",
        "incident_id",
        "action",
        "path",
        "score",
        "level",
        "details",
    ):
        assert column in header, f"{column} missing from the CSV header"

    # Every data row has the same width as the header.
    assert all(len(row) == len(header) for row in rows[1:])


def test_csv_report_carries_the_incident_summary(client, tmp_path):
    incident_id = trigger_incident(client, tmp_path)

    response = client.post(
        "/reports/generate",
        params={"format": "csv", "incident_id": incident_id},
    )

    path = Path(response.json()["path"])

    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    record_types = {row["record_type"] for row in rows}

    # The CSV must carry the incident summary, not only raw events.
    assert record_types & {"incident", "summary"}, record_types
    assert "suspect_process" in record_types, record_types

    process_row = next(
        row for row in rows if row["record_type"] == "suspect_process"
    )
    process_details = json.loads(process_row["details"])
    assert process_details["process"]["name"]
    assert "causal" in process_details["attribution"]


# --------------------------------------------------------------------------
# listing and download
# --------------------------------------------------------------------------


def test_reports_listing_counts_the_generated_files(client, tmp_path):
    incident_id = trigger_incident(client, tmp_path)

    client.post(
        "/reports/generate",
        params={"format": "json", "incident_id": incident_id},
    )
    client.post(
        "/reports/generate",
        params={"format": "csv", "incident_id": incident_id},
    )

    listing = client.get("/reports").json()

    assert listing["count"] == len(listing["incidents"])
    assert listing["count"] >= 1


@pytest.mark.parametrize(
    ("fmt", "media_type"),
    [("json", "application/json"), ("csv", "text/csv")],
)
def test_report_download_serves_the_file(client, tmp_path, fmt, media_type):
    """The download route is /reports/{incident_id}/{format}."""
    incident_id = trigger_incident(client, tmp_path)

    client.post(
        "/reports/generate",
        params={"format": fmt, "incident_id": incident_id},
    )

    response = client.get(f"/reports/{incident_id}/{fmt}")

    assert response.status_code == 200
    assert media_type in response.headers["content-type"]
    assert response.content


def test_unsupported_report_format_is_rejected(client, tmp_path):
    incident_id = trigger_incident(client, tmp_path)

    response = client.post(
        "/reports/generate",
        params={"format": "pdf", "incident_id": incident_id},
    )

    # Phase 8 makes HTML/PDF optional; an unsupported format must be refused,
    # not silently produce something.
    assert response.status_code in (400, 422)
