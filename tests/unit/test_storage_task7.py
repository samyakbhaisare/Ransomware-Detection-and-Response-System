"""Storage-layer coverage for schema migration and incident attribution (Task 7).

Two defects are guarded here specifically:

* **D-04** - events must be attributable to the incident they produced, and
  attribution must never steal events already owned by another incident.
* **D-05** - incident score / level / suspect / affected files live in
  dedicated columns so readers (report, API, dashboard) never have to parse
  the evidence blob.

The migration path is exercised against a genuinely old schema (built with raw
SQL, the way the original release created it) to prove that an existing
database is upgraded in place without losing rows.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from rdrs.storage import DatabaseManager


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "store" / "rdrs.db"


@pytest.fixture()
def database(db_path):
    manager = DatabaseManager(db_path)
    manager.initialize()

    yield manager

    manager.close()


# ---------------------------------------------------------------------------
# initialize / engine guards
# ---------------------------------------------------------------------------


def test_initialize_creates_the_file_and_every_table(database, db_path):
    assert db_path.is_file()
    assert database.engine is not None

    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    assert {
        "events",
        "processes",
        "scores",
        "alerts",
        "incidents",
        "runtime_state",
        "subsystem_health",
        "user_sessions",
        "network_connections",
    }.issubset(tables)


def test_operations_require_an_initialized_engine(db_path):
    manager = DatabaseManager(db_path)

    with pytest.raises(RuntimeError, match="Database not initialized"):
        manager.record_runtime_state("RUNNING")

    # _migrate_schema on an uninitialized manager is a no-op, not a crash.
    manager._migrate_schema()  # noqa: SLF001

    assert manager.engine is None


def test_context_manager_initializes_and_closes(db_path):
    with DatabaseManager(db_path) as manager:
        assert manager.engine is not None
        engine = manager.engine

    assert manager.engine is None
    assert engine is not None


def test_close_is_idempotent(database):
    database.close()
    assert database.engine is None

    database.close()
    assert database.engine is None


# ---------------------------------------------------------------------------
# Schema migration (D-05)
# ---------------------------------------------------------------------------


def test_legacy_database_is_upgraded_in_place_without_data_loss(db_path):
    """An old-schema database keeps its rows and gains the new columns."""
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Build the original schema by hand: no incident_id on events and no
    # threat_score / threat_level / suspect / affected columns on incidents.
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path VARCHAR(1024) NOT NULL,
                action VARCHAR(64) NOT NULL,
                previous_path VARCHAR(1024),
                new_path VARCHAR(1024),
                metadata_json TEXT DEFAULT '{}',
                created_at DATETIME
            );
            CREATE TABLE incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status VARCHAR(32) NOT NULL,
                severity VARCHAR(32) NOT NULL,
                description TEXT NOT NULL,
                evidence_json TEXT DEFAULT '{}',
                created_at DATETIME
            );
            """
        )
        connection.execute(
            "INSERT INTO events (path, action, metadata_json, created_at) "
            "VALUES (?, ?, ?, ?)",
            (
                "C:/legacy/old.txt",
                "modified",
                json.dumps({"entropy": 7.2}),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO incidents (status, severity, description, evidence_json, "
            "created_at) VALUES (?, ?, ?, ?, ?)",
            (
                "open",
                "CRITICAL",
                "legacy incident",
                json.dumps({"score": 70.0, "level": "CRITICAL"}),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        connection.commit()

    # Opening with the product must migrate, not recreate.
    manager = DatabaseManager(db_path)
    manager.initialize()

    try:
        events = manager.list_events(limit=10)

        assert len(events) == 1
        assert events[0]["path"] == "C:/legacy/old.txt"
        assert events[0]["incident_id"] is None
        assert events[0]["metadata"] == {"entropy": 7.2}

        incidents = manager.list_incidents(limit=10)

        assert len(incidents) == 1

        legacy = incidents[0]

        # Defaults were backfilled by the ALTER TABLE, and the evidence blob
        # is still readable as a fallback.
        assert legacy["description"] == "legacy incident"
        assert legacy["threat_score"] == pytest.approx(0.0)
        assert legacy["threat_level"] == "NORMAL"
        assert legacy["affected_files"] == []
        assert legacy["evidence"]["score"] == pytest.approx(70.0)
    finally:
        manager.close()


def test_migration_is_idempotent(db_path):
    """Re-opening a migrated database must not fail or duplicate columns."""
    first = DatabaseManager(db_path)
    first.initialize()

    incident_id = first.record_incident(
        severity="CRITICAL",
        description="first pass",
        threat_score=70.0,
        threat_level="CRITICAL",
    )
    first.close()

    second = DatabaseManager(db_path)

    # initialize() twice in a row must be harmless.
    second.initialize()
    second.initialize()

    try:
        incident = second.get_incident(incident_id)

        assert incident is not None
        assert incident["description"] == "first pass"
        assert incident["threat_level"] == "CRITICAL"
    finally:
        second.close()


# ---------------------------------------------------------------------------
# Incident columns (D-05)
# ---------------------------------------------------------------------------


def test_incident_columns_are_written_and_read_back(database):
    incident_id = database.record_incident(
        severity="CRITICAL",
        description="rapid encryption",
        evidence={"score": 70.0, "level": "CRITICAL"},
        threat_score=70.0,
        threat_level="CRITICAL",
        suspect_process={"name": "enc.exe", "pid": 9},
        affected_files=["C:/a.txt", "C:/b.txt"],
        score_id=3,
        alert_id=4,
    )

    stored = database.get_incident(incident_id)

    assert stored is not None
    assert stored["threat_score"] == pytest.approx(70.0)
    assert stored["threat_level"] == "CRITICAL"
    assert stored["suspect_process"] == {"name": "enc.exe", "pid": 9}
    assert stored["affected_files"] == ["C:/a.txt", "C:/b.txt"]
    assert stored["score_id"] == 3
    assert stored["alert_id"] == 4
    assert stored["status"] == "open"

    listed = database.list_incidents(limit=1)[0]

    assert listed == stored


def test_incident_columns_fall_back_to_evidence(database):
    """Rows written without explicit columns still expose the values."""
    incident_id = database.record_incident(
        severity="CRITICAL",
        description="legacy style",
        evidence={
            "score": 55.0,
            "level": "WARNING",
            "suspect_process": {"name": "from-evidence.exe"},
            "affected_files": ["C:/from-evidence.txt"],
        },
    )

    stored = database.get_incident(incident_id)

    assert stored["threat_score"] == pytest.approx(55.0)
    assert stored["threat_level"] == "WARNING"
    assert stored["suspect_process"] == {"name": "from-evidence.exe"}
    assert stored["affected_files"] == ["C:/from-evidence.txt"]


def test_get_incident_returns_none_for_an_unknown_id(database):
    assert database.get_incident(987654) is None


def test_affected_files_tolerate_a_legacy_single_string(database):
    """A legacy JSON-encoded single string must not break the reader.

    ``_decode_json_list`` first looks for a JSON array, then tolerates a
    JSON-encoded scalar. A raw path is not valid JSON, so the legacy shape
    that can actually reach the string branch is a JSON string.
    """
    incident_id = database.record_incident(
        severity="CRITICAL",
        description="legacy string list",
        evidence={"score": 70.0},
    )

    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE incidents SET affected_files_json = ? WHERE id = ?",
            (json.dumps("C:/single-file.txt"), incident_id),
        )

    assert database.get_incident(incident_id)["affected_files"] == [
        "C:/single-file.txt"
    ]


def test_affected_files_tolerate_a_non_json_legacy_string(database):
    """A bare legacy path must survive as a single-element list.

    A row written before the ``affected_files_json`` column existed can hold
    a plain ``"C:/file.txt"`` string, which is not valid JSON. Discarding it
    would lose the only record of which file the incident touched, so the
    raw scalar is preserved instead.
    """
    incident_id = database.record_incident(
        severity="CRITICAL",
        description="bare legacy path",
        evidence={"score": 70.0},
    )

    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE incidents SET affected_files_json = ? WHERE id = ?",
            ("C:/single-file.txt", incident_id),
        )

    assert database.get_incident(incident_id)["affected_files"] == [
        "C:/single-file.txt"
    ]


def test_corrupt_json_columns_decode_to_safe_defaults(database):
    incident_id = database.record_incident(
        severity="CRITICAL",
        description="corrupt json",
        evidence={"score": 70.0},
    )

    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE incidents SET evidence_json = ?, suspect_process_json = ?, "
            "affected_files_json = ? WHERE id = ?",
            ("{not json", "[[[", "not-a-list", incident_id),
        )

    stored = database.get_incident(incident_id)

    assert stored["evidence"] == {}
    assert stored["suspect_process"] == {}
    # "not-a-list" is not valid JSON, so it is preserved as a one-element
    # list rather than being dropped.
    assert stored["affected_files"] == ["not-a-list"]


# ---------------------------------------------------------------------------
# Event persistence + attribution (D-04)
# ---------------------------------------------------------------------------


def test_record_file_event_returns_the_row_id_and_persists_fields(database):
    event_id = database.record_file_event(
        {
            "path": "C:/sandbox/a.txt",
            "action": "renamed",
            "previous_path": "C:/sandbox/a.txt",
            "new_path": "C:/sandbox/a.txt.locked",
            "metadata": {"entropy": 7.9},
        }
    )

    assert isinstance(event_id, int)

    stored = database.list_events(limit=1)[0]

    assert stored["id"] == event_id
    assert stored["action"] == "renamed"
    assert stored["new_path"] == "C:/sandbox/a.txt.locked"
    assert stored["metadata"] == {"entropy": 7.9}
    assert stored["incident_id"] is None


def test_record_file_event_accepts_an_explicit_incident_id(database):
    database.record_file_event(
        {
            "path": "C:/sandbox/a.txt",
            "action": "modified",
            "incident_id": 42,
        }
    )

    assert database.list_events(limit=1)[0]["incident_id"] == 42


def test_attach_events_to_incident_by_id(database):
    first = database.record_file_event({"path": "C:/a", "action": "modified"})
    second = database.record_file_event({"path": "C:/b", "action": "modified"})

    assert database.attach_events_to_incident(1, [first, second]) == 2

    scoped = database.list_events_for_incident(1, limit=10)

    assert [row["id"] for row in scoped] == [first, second]

    # An empty id list is a no-op, never a full-table update.
    assert database.attach_events_to_incident(2, []) == 0


def test_attribution_never_steals_events_from_another_incident(database):
    """Concurrent incidents must stay isolated (D-04 safety property)."""
    first = database.record_file_event({"path": "C:/a", "action": "modified"})
    second = database.record_file_event({"path": "C:/b", "action": "modified"})

    assert database.attach_events_to_incident(1, [first, second]) == 2

    # Re-attributing the same events to a second incident must change nothing.
    assert database.attach_events_to_incident(2, [first, second]) == 0

    assert database.list_events_for_incident(1, limit=10)
    assert database.list_events_for_incident(2, limit=10) == []


def test_attach_events_by_paths_matches_every_path_column(database):
    by_path = database.record_file_event({"path": "C:/a.txt", "action": "modified"})
    by_new = database.record_file_event(
        {
            "path": "",
            "action": "renamed",
            "new_path": "C:/a.txt.locked",
        }
    )
    by_previous = database.record_file_event(
        {
            "path": "C:/a.txt.locked",
            "action": "renamed",
            "previous_path": "C:/a.txt",
        }
    )
    unrelated = database.record_file_event({"path": "C:/z.txt", "action": "modified"})

    updated = database.attach_events_to_incident_by_paths(
        7,
        ["C:/a.txt", "C:/a.txt.locked"],
    )

    assert updated == 3

    scoped_ids = {
        row["id"] for row in database.list_events_for_incident(7, limit=10)
    }

    assert scoped_ids == {by_path, by_new, by_previous}
    assert unrelated not in scoped_ids

    # Empty path list and blank entries are no-ops.
    assert database.attach_events_to_incident_by_paths(8, []) == 0
    assert database.attach_events_to_incident_by_paths(8, ["", None]) == 0


def test_attach_events_by_window_respects_bounds(database):
    """Only unassigned events inside [since, until] are attributed."""
    now = datetime.now(timezone.utc)

    inside_id = database.record_file_event({"path": "C:/inside", "action": "modified"})
    outside_id = database.record_file_event({"path": "C:/outside", "action": "modified"})

    inside_time = now - timedelta(minutes=2)
    outside_time = now - timedelta(days=5)

    # Rewrite the timestamps through the ORM so they use SQLite's canonical
    # format and remain comparable with real datetime bounds.
    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE events SET created_at = ? WHERE id = ?",
            (inside_time, inside_id),
        )
        connection.exec_driver_sql(
            "UPDATE events SET created_at = ? WHERE id = ?",
            (outside_time, outside_id),
        )

    updated = database.attach_events_to_incident_by_window(
        11,
        since=now - timedelta(minutes=30),
        until=now + timedelta(minutes=1),
    )

    assert updated == 1

    scoped = database.list_events_for_incident(11, limit=10)

    assert [row["id"] for row in scoped] == [inside_id]
    assert scoped[0]["path"] == "C:/inside"


def test_attach_events_by_window_since_only(database):
    """A window may be open-ended on either side."""
    now = datetime.now(timezone.utc)

    recent_id = database.record_file_event({"path": "C:/recent", "action": "modified"})
    old_id = database.record_file_event({"path": "C:/old", "action": "modified"})

    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE events SET created_at = ? WHERE id = ?",
            (now - timedelta(minutes=1), recent_id),
        )
        connection.exec_driver_sql(
            "UPDATE events SET created_at = ? WHERE id = ?",
            (now - timedelta(days=30), old_id),
        )

    assert (
        database.attach_events_to_incident_by_window(
            12,
            since=now - timedelta(hours=1),
        )
        == 1
    )

    assert [
        row["id"] for row in database.list_events_for_incident(12, limit=10)
    ] == [recent_id]


def test_attach_events_by_window_until_only(database):
    now = datetime.now(timezone.utc)

    recent_id = database.record_file_event({"path": "C:/recent", "action": "modified"})
    old_id = database.record_file_event({"path": "C:/old", "action": "modified"})

    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE events SET created_at = ? WHERE id = ?",
            (now - timedelta(minutes=1), recent_id),
        )
        connection.exec_driver_sql(
            "UPDATE events SET created_at = ? WHERE id = ?",
            (now - timedelta(days=30), old_id),
        )

    assert (
        database.attach_events_to_incident_by_window(
            13,
            until=now - timedelta(hours=1),
        )
        == 1
    )

    assert [
        row["id"] for row in database.list_events_for_incident(13, limit=10)
    ] == [old_id]


def test_attach_events_by_window_without_bounds_takes_the_whole_window(database):
    database.record_file_event({"path": "C:/a", "action": "modified"})
    database.record_file_event({"path": "C:/b", "action": "modified"})

    assert database.attach_events_to_incident_by_window(5) == 2

    assert len(database.list_events_for_incident(5, limit=10)) == 2


def test_list_events_for_incident_is_chronological_and_limited(database):
    ids = [
        database.record_file_event({"path": f"C:/e{index}", "action": "modified"})
        for index in range(5)
    ]

    database.attach_events_to_incident(3, ids)

    scoped = database.list_events_for_incident(3, limit=3)

    assert len(scoped) == 3
    assert [row["id"] for row in scoped] == ids[:3]

    # A nonsense limit is clamped to at least one row.
    assert len(database.list_events_for_incident(3, limit=0)) == 1


# ---------------------------------------------------------------------------
# Other writers + readers
# ---------------------------------------------------------------------------


def test_runtime_state_is_upserted(database):
    database.record_runtime_state("RUNNING")
    database.record_runtime_state("STOPPED")

    with database.engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT value FROM runtime_state WHERE key = 'state'"
        ).all()

    assert [row[0] for row in rows] == ["STOPPED"]


def test_subsystem_health_is_upserted(database):
    database.record_subsystem("database", True, {"detail": "first"})
    database.record_subsystem("database", False, {"detail": "second"})

    with database.engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT name, healthy, details FROM subsystem_health "
            "WHERE name = 'database'"
        ).all()

    assert len(rows) == 1
    assert rows[0][1] == 0
    assert json.loads(rows[0][2]) == {"detail": "second"}


def test_user_session_and_network_connection_round_trip(database):
    class Session:
        user = "analyst"
        session_id = "s-1"
        privilege = "admin"
        home = "C:/Users/analyst"

    class Connection:
        family = "AF_INET"
        local_address = "127.0.0.1:5000"
        remote_address = "10.0.0.9:443"
        protocol = "tcp"
        port = 443

    assert database.record_user_session(Session()) == 1
    assert database.record_network_connection(Connection()) == 1

    assert database.count_records("user_sessions") == 1
    assert database.count_records("network_connections") == 1


def test_list_readers_respect_limits_and_ordering(database):
    for index in range(4):
        database.record_score(score=float(index), level="Normal", reason=f"r{index}")
        database.record_alert(severity="INFO", message=f"m{index}")

    assert len(database.list_scores(limit=2)) == 2
    assert len(database.list_alerts(limit=2)) == 2
    assert len(database.list_incidents(limit=1)) == 0

    # Newest first.
    assert database.list_scores(limit=1)[0]["reason"] == "r3"

    # A zero/negative limit is clamped rather than returning everything.
    assert len(database.list_scores(limit=0)) == 1


def test_count_records_supports_aliases_and_rejects_unknown_tables(database):
    database.record_file_event({"path": "C:/a", "action": "modified"})

    assert database.count_records("events") == 1
    assert database.count_records("file_events") == 1
    assert database.count_records("processes") == 0
    assert database.count_records("process_telemetry") == 0

    with pytest.raises(ValueError, match="Unsupported table"):
        database.count_records("secrets")


def test_decode_json_helpers_never_raise():
    decode_object = DatabaseManager._decode_json  # noqa: SLF001
    decode_list = DatabaseManager._decode_json_list  # noqa: SLF001

    assert decode_object(None) == {}
    assert decode_object("") == {}
    assert decode_object("{bad") == {}
    assert decode_object("[1,2]") == {}
    assert decode_object('{"a": 1}') == {"a": 1}

    assert decode_list(None) == []
    assert decode_list("") == []
    assert decode_list('["x"]') == ["x"]
    assert decode_list('"solo"') == ["solo"]
    assert decode_list("17") == []
    # A bare non-JSON scalar is preserved (not discarded) so a legacy single
    # path is never silently lost.
    assert decode_list("{bad") == ["{bad"]


def test_iso_normalizes_naive_and_aware_datetimes():
    naive = datetime(2026, 1, 2, 3, 4, 5)
    aware = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    assert DatabaseManager._iso(None) is None  # noqa: SLF001
    assert DatabaseManager._iso(naive) == "2026-01-02T03:04:05+00:00"  # noqa: SLF001
    assert DatabaseManager._iso(aware) == "2026-01-02T03:04:05+00:00"  # noqa: SLF001
