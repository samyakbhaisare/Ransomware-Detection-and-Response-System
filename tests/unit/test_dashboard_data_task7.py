"""DashboardData aggregation coverage (Task 7).

Phase 7 dashboard reads must be provably derived from persisted rows: no
counters are maintained separately and no value is ever fabricated. These
tests seed real rows and assert the exact aggregation, including the
defensive helpers that keep a malformed row from breaking the dashboard.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rdrs.dashboard_data import DashboardData
from rdrs.storage import DatabaseManager
from rdrs.telemetry import ProcessTelemetry


@pytest.fixture()
def database(tmp_path):
    manager = DatabaseManager(tmp_path / "dash" / "rdrs.db")
    manager.initialize()

    yield manager

    manager.close()


@pytest.fixture()
def data(database):
    return DashboardData(database)


# ---------------------------------------------------------------------------
# Construction + serialization helpers
# ---------------------------------------------------------------------------


def test_database_is_required():
    with pytest.raises(ValueError, match="database is required"):
        DashboardData(None)


def test_iso_handles_none_objects_and_plain_values():
    assert DashboardData._iso(None) is None  # noqa: SLF001
    assert DashboardData._iso("2026-01-02T03:04:05+00:00") == (  # noqa: SLF001
        "2026-01-02T03:04:05+00:00"
    )
    assert DashboardData._iso(17) == "17"  # noqa: SLF001


def test_json_object_tolerates_every_metadata_shape():
    decode = DashboardData._json_object  # noqa: SLF001

    assert decode(None) == {}
    assert decode({"entropy": 7.5}) == {"entropy": 7.5}
    assert decode('{"entropy": 7.5}') == {"entropy": 7.5}

    # Non-object JSON, malformed JSON and non-string types must not raise.
    assert decode("[1, 2, 3]") == {}
    assert decode("{not json") == {}
    assert decode(12) == {}


def test_safe_float_and_int_fall_back_to_defaults():
    assert DashboardData._safe_float("3.5") == pytest.approx(3.5)  # noqa: SLF001
    assert DashboardData._safe_float("abc") == 0.0  # noqa: SLF001
    assert DashboardData._safe_float(None, 1.5) == pytest.approx(1.5)  # noqa: SLF001

    assert DashboardData._safe_int("42") == 42  # noqa: SLF001
    assert DashboardData._safe_int("abc") == 0  # noqa: SLF001
    assert DashboardData._safe_int(None, 9) == 9  # noqa: SLF001


# ---------------------------------------------------------------------------
# latest_score
# ---------------------------------------------------------------------------


def test_latest_score_is_normal_when_nothing_is_persisted(data):
    """No score row means no score is invented."""
    assert data.latest_score() == {
        "score": 0.0,
        "level": "Normal",
        "reason": "",
        "created_at": None,
    }


def test_latest_score_returns_the_newest_row(data, database):
    database.record_score(
        score=12.0,
        level="Normal",
        reason="baseline",
    )
    database.record_score(
        score=78.5,
        level="Critical",
        reason="rapid encryption detected",
    )

    latest = data.latest_score()

    assert latest["score"] == pytest.approx(78.5)
    assert latest["level"] == "Critical"
    assert latest["reason"] == "rapid encryption detected"
    assert latest["created_at"] is not None


# ---------------------------------------------------------------------------
# recent_processes / cpu_history
# ---------------------------------------------------------------------------


def test_recent_processes_returns_newest_first(data, database):
    for pid, name in ((101, "first.exe"), (202, "second.exe")):
        database.record_process(
            ProcessTelemetry(pid=pid, name=name, cpu_percent=float(pid) / 10)
        )

    rows = data.recent_processes(limit=10)

    assert [row["pid"] for row in rows] == [202, 101]

    newest = rows[0]

    assert newest["name"] == "second.exe"
    assert newest["cpu_percent"] == pytest.approx(20.2)
    assert newest["captured_at"] is not None
    assert newest["parent_pid"] is None
    assert newest["command_line"] == ""
    assert newest["executable_path"] == ""


def test_recent_processes_clamps_a_nonsense_limit(data, database):
    database.record_process(ProcessTelemetry(pid=1, name="one.exe"))

    # limit=0 would otherwise produce an empty page; it is clamped to 1.
    assert len(data.recent_processes(limit=0)) == 1


def test_cpu_history_is_chronological(data, database):
    for pid in (1, 2, 3):
        database.record_process(
            ProcessTelemetry(pid=pid, name=f"p{pid}.exe", cpu_percent=float(pid))
        )

    history = data.cpu_history(limit=10)

    # recent_processes is newest-first, so the chart must reverse it.
    assert [point["pid"] for point in history] == [1, 2, 3]
    assert [point["value"] for point in history] == [1.0, 2.0, 3.0]
    assert all(point["time"] is not None for point in history)


def test_cpu_history_is_empty_without_telemetry(data):
    assert data.cpu_history() == []


# ---------------------------------------------------------------------------
# recent_events / entropy_history
# ---------------------------------------------------------------------------


def test_recent_events_normalizes_rows(data, database):
    database.record_file_event(
        {
            "path": "C:/sandbox/a.txt",
            "action": "modified",
            "previous_path": "C:/sandbox/a.txt",
            "new_path": "C:/sandbox/a.txt.locked",
            "metadata": {"entropy": 7.91},
        }
    )

    rows = data.recent_events(limit=5)

    assert len(rows) == 1

    row = rows[0]

    assert row["path"] == "C:/sandbox/a.txt"
    assert row["action"] == "modified"
    assert row["previous_path"] == "C:/sandbox/a.txt"
    assert row["new_path"] == "C:/sandbox/a.txt.locked"
    assert row["metadata"] == {"entropy": 7.91}
    assert row["created_at"] is not None


def test_entropy_history_keeps_only_valid_entropy(data, database):
    database.record_file_event(
        {
            "path": "C:/sandbox/good.bin",
            "action": "modified",
            "metadata": {"entropy": 7.5},
        }
    )
    database.record_file_event(
        {
            "path": "C:/sandbox/no-entropy.txt",
            "action": "created",
            "metadata": {"size": 10},
        }
    )
    database.record_file_event(
        {
            "path": "C:/sandbox/non-numeric.bin",
            "action": "modified",
            "metadata": {"entropy": "very high"},
        }
    )
    database.record_file_event(
        {
            "path": "C:/sandbox/out-of-range.bin",
            "action": "modified",
            "metadata": {"entropy": 9.4},
        }
    )

    history = data.entropy_history(limit=10)

    assert len(history) == 1
    assert history[0]["value"] == pytest.approx(7.5)
    assert history[0]["path"] == "C:/sandbox/good.bin"


def test_entropy_history_accepts_the_boundary_values(data, database):
    for path, value in (
        ("C:/sandbox/zero.bin", 0.0),
        ("C:/sandbox/eight.bin", 8.0),
    ):
        database.record_file_event(
            {
                "path": path,
                "action": "modified",
                "metadata": {"entropy": value},
            }
        )

    values = sorted(point["value"] for point in data.entropy_history(limit=10))

    assert values == [0.0, 8.0]


def test_telemetry_bundles_cpu_and_entropy(data, database):
    database.record_process(ProcessTelemetry(pid=5, name="p5.exe", cpu_percent=5.0))
    database.record_file_event(
        {"path": "C:/sandbox/x.bin", "action": "modified", "metadata": {"entropy": 6.0}}
    )

    telemetry = data.telemetry()

    assert set(telemetry) == {"cpu", "entropy"}
    assert telemetry["cpu"][0]["pid"] == 5
    assert telemetry["entropy"][0]["value"] == pytest.approx(6.0)


# ---------------------------------------------------------------------------
# recent_alerts / recent_incidents / summary
# ---------------------------------------------------------------------------


def test_recent_alerts_exposes_persisted_fields(data, database):
    database.record_alert(
        severity="CRITICAL",
        message="Ransomware burst detected",
        evidence={"score": 70.0},
    )

    alerts = data.recent_alerts(limit=5)

    assert len(alerts) == 1

    alert = alerts[0]

    assert alert["severity"] == "CRITICAL"
    assert alert["message"] == "Ransomware burst detected"
    assert alert["evidence"] == {"score": 70.0}
    assert alert["created_at"] is not None


def test_recent_alerts_is_empty_on_an_empty_database(data):
    assert data.recent_alerts() == []


def test_recent_incidents_normalizes_rows(data, database):
    incident_id = database.record_incident(
        severity="CRITICAL",
        description="Critical ransomware threat detected",
        evidence={"score": 70.0},
        threat_score=70.0,
        threat_level="CRITICAL",
    )

    rows = data.recent_incidents(limit=5)

    assert len(rows) == 1

    row = rows[0]

    assert row["id"] == incident_id
    assert row["severity"] == "CRITICAL"
    assert row["status"] == "open"
    assert row["description"] == "Critical ransomware threat detected"
    assert row["created_at"] is not None


def test_recent_incidents_is_empty_on_an_empty_database(data):
    assert data.recent_incidents() == []


def test_summary_counts_only_real_rows(data, database):
    assert data.summary() == {
        "event_count": 0,
        "process_count": 0,
        "score_count": 0,
        "alert_count": 0,
        "incident_count": 0,
    }

    database.record_file_event({"path": "C:/a", "action": "modified"})
    database.record_file_event({"path": "C:/b", "action": "modified"})
    database.record_process(ProcessTelemetry(pid=1, name="p.exe"))
    database.record_score(score=10.0, level="Normal", reason="ok")
    database.record_alert(severity="WARNING", message="heads up")
    database.record_incident(severity="WARNING", description="incident")

    assert data.summary() == {
        "event_count": 2,
        "process_count": 1,
        "score_count": 1,
        "alert_count": 1,
        "incident_count": 1,
    }


# ---------------------------------------------------------------------------
# top_modified_files
# ---------------------------------------------------------------------------


def test_top_modified_files_counts_real_modifications(data, database):
    for _ in range(3):
        database.record_file_event(
            {"path": "C:/sandbox/hot.docx", "action": "modified"}
        )

    database.record_file_event(
        {"path": "C:/sandbox/warm.docx", "action": "modified"}
    )

    # Renames and creations are not modifications and must not be counted.
    database.record_file_event(
        {
            "path": "C:/sandbox/ignored.docx",
            "action": "renamed",
            "new_path": "C:/sandbox/ignored.docx.locked",
        }
    )
    database.record_file_event(
        {"path": "C:/sandbox/new.docx", "action": "created"}
    )

    rows = data.top_modified_files(limit=10)

    assert rows == [
        {"path": "C:/sandbox/hot.docx", "modifications": 3},
        {"path": "C:/sandbox/warm.docx", "modifications": 1},
    ]


def test_top_modified_files_falls_back_to_new_path(data, database):
    database.record_file_event(
        {
            "path": "",
            "action": "modified",
            "new_path": "C:/sandbox/renamed-only.bin",
        }
    )

    assert data.top_modified_files() == [
        {"path": "C:/sandbox/renamed-only.bin", "modifications": 1}
    ]


def test_top_modified_files_respects_the_limit(data, database):
    for index in range(5):
        database.record_file_event(
            {"path": f"C:/sandbox/f{index}.bin", "action": "modified"}
        )

    assert len(data.top_modified_files(limit=2)) == 2


# ---------------------------------------------------------------------------
# complete()
# ---------------------------------------------------------------------------


def test_complete_bundles_every_dashboard_section(data, database):
    database.record_file_event(
        {"path": "C:/sandbox/a.bin", "action": "modified", "metadata": {"entropy": 7.0}}
    )
    database.record_process(ProcessTelemetry(pid=9, name="p9.exe", cpu_percent=9.0))
    database.record_alert(severity="WARNING", message="heads up")
    database.record_incident(severity="WARNING", description="incident")

    payload = data.complete()

    assert set(payload) == {
        "summary",
        "score",
        "events",
        "processes",
        "alerts",
        "incidents",
        "top_modified",
        "telemetry",
    }

    assert payload["summary"]["event_count"] == 1
    assert payload["summary"]["process_count"] == 1
    assert payload["summary"]["alert_count"] == 1
    assert payload["summary"]["incident_count"] == 1

    assert payload["events"][0]["path"] == "C:/sandbox/a.bin"
    assert payload["processes"][0]["pid"] == 9
    assert payload["alerts"][0]["message"] == "heads up"
    assert payload["incidents"][0]["description"] == "incident"
    assert payload["top_modified"][0]["modifications"] == 1
    assert payload["telemetry"]["entropy"][0]["value"] == pytest.approx(7.0)
    assert payload["telemetry"]["cpu"][0]["pid"] == 9


def test_complete_honours_every_limit(data, database):
    for index in range(6):
        database.record_file_event(
            {
                "path": f"C:/sandbox/l{index}.bin",
                "action": "modified",
                "metadata": {"entropy": float(index) / 10.0},
            }
        )
        database.record_process(ProcessTelemetry(pid=index + 1, name=f"p{index}.exe"))
        database.record_alert(severity="INFO", message=f"m{index}")
        database.record_incident(severity="INFO", description=f"i{index}")

    payload = data.complete(
        event_limit=2,
        process_limit=3,
        alert_limit=1,
        incident_limit=2,
        modified_file_limit=4,
    )

    assert len(payload["events"]) == 2
    assert len(payload["processes"]) == 3
    assert len(payload["alerts"]) == 1
    assert len(payload["incidents"]) == 2
    assert len(payload["top_modified"]) == 4
    assert len(payload["telemetry"]["cpu"]) == 3
    assert len(payload["telemetry"]["entropy"]) == 2


def test_complete_on_an_empty_database_is_a_valid_document(data):
    payload = data.complete()

    assert payload["summary"]["incident_count"] == 0
    assert payload["events"] == []
    assert payload["processes"] == []
    assert payload["alerts"] == []
    assert payload["incidents"] == []
    assert payload["top_modified"] == []
    assert payload["telemetry"] == {"cpu": [], "entropy": []}
    assert payload["score"] == {
        "score": 0.0,
        "level": "Normal",
        "reason": "",
        "created_at": None,
    }
