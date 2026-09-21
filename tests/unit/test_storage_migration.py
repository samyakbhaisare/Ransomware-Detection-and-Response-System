"""FR-05 regression tests — SQLite schema migration.

Defect covered
--------------
``_migrate_schema`` added only the columns named in a hand-maintained
``_ADDED_COLUMNS`` map. That map had drifted from the SQLAlchemy model: it
covered ``threat_score``, ``threat_level``, ``suspect_process_json``,
``affected_files_json``, ``score_id`` and ``alert_id``, but **not** ``status``,
``evidence_json`` or ``created_at``.

A legacy database was therefore still missing ``incidents.status`` after
initialisation, and every query against the table failed with::

    sqlite3.OperationalError: no such column: incidents.status

The migration now derives the column set from the model, so it cannot fall
behind a schema change again.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from rdrs.storage import Base, DatabaseManager


def columns_of(path: Path, table: str) -> list[str]:
    connection = sqlite3.connect(path)

    try:
        return [
            row[1]
            for row in connection.execute(f"PRAGMA table_info({table})")
        ]
    finally:
        connection.close()


def build_legacy_database(path: Path) -> None:
    """Create the oldest incidents table shape the project shipped."""
    connection = sqlite3.connect(path)

    try:
        connection.execute(
            "CREATE TABLE incidents ("
            "id INTEGER PRIMARY KEY, "
            "severity TEXT NOT NULL, "
            "description TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO incidents (severity, description) "
            "VALUES ('CRITICAL', 'legacy incident')"
        )
        connection.commit()
    finally:
        connection.close()


# --------------------------------------------------------------------------
# the migration must bring a legacy table up to the current model
# --------------------------------------------------------------------------


def test_migration_adds_every_model_column(tmp_path):
    """A legacy table must end up with exactly the model's columns."""
    path = tmp_path / "legacy.db"
    build_legacy_database(path)

    database = DatabaseManager(path)
    database.initialize()

    try:
        migrated = set(columns_of(path, "incidents"))
        expected = {
            column.name
            for column in Base.metadata.tables["incidents"].columns
        }

        missing = expected - migrated
        assert not missing, f"migration left columns missing: {sorted(missing)}"
    finally:
        database.close()


def test_migration_adds_the_previously_missed_columns(tmp_path):
    """The exact columns the hand-written map forgot."""
    path = tmp_path / "legacy.db"
    build_legacy_database(path)

    database = DatabaseManager(path)
    database.initialize()

    try:
        migrated = set(columns_of(path, "incidents"))

        for column in ("status", "evidence_json", "created_at"):
            assert column in migrated, (
                f"{column} was not added; queries on incidents will fail"
            )
    finally:
        database.close()


def test_legacy_rows_survive_the_migration(tmp_path):
    path = tmp_path / "legacy.db"
    build_legacy_database(path)

    database = DatabaseManager(path)
    database.initialize()

    try:
        assert database.count_records("incidents") == 1

        incident = database.list_incidents(limit=1)[0]

        assert incident["severity"] == "CRITICAL"
        assert incident["description"] == "legacy incident"
        # Added columns are readable, not an OperationalError.
        assert "status" in incident
        assert "threat_level" in incident
    finally:
        database.close()


def test_migration_is_idempotent(tmp_path):
    path = tmp_path / "legacy.db"
    build_legacy_database(path)

    first = DatabaseManager(path)
    first.initialize()
    after_first = columns_of(path, "incidents")
    first.close()

    second = DatabaseManager(path)
    second.initialize()
    after_second = columns_of(path, "incidents")
    second.close()

    assert after_first == after_second
    assert len(after_second) == len(set(after_second)), "duplicate columns"


def test_migration_handles_a_table_with_only_an_id(tmp_path):
    """Even a near-empty legacy table must be brought up to date."""
    path = tmp_path / "bare.db"

    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE events (id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()

    database = DatabaseManager(path)
    database.initialize()

    try:
        migrated = set(columns_of(path, "events"))
        expected = {
            column.name
            for column in Base.metadata.tables["events"].columns
        }

        assert not (expected - migrated)
    finally:
        database.close()


def test_migration_leaves_a_current_database_untouched(tmp_path):
    """A database created by create_all needs no migration."""
    path = tmp_path / "current.db"

    database = DatabaseManager(path)
    database.initialize()
    before = columns_of(path, "incidents")
    database.close()

    again = DatabaseManager(path)
    again.initialize()
    after = columns_of(path, "incidents")
    again.close()

    assert before == after


def test_migration_covers_every_table_in_the_model(tmp_path):
    """No model table may be left with missing columns."""
    path = tmp_path / "legacy-all.db"

    # Every table exists, but each has only its id column.
    connection = sqlite3.connect(path)

    try:
        for table in Base.metadata.sorted_tables:
            connection.execute(
                f"CREATE TABLE {table.name} (id INTEGER PRIMARY KEY)"
            )
        connection.commit()
    finally:
        connection.close()

    database = DatabaseManager(path)
    database.initialize()

    try:
        for table in Base.metadata.sorted_tables:
            migrated = set(columns_of(path, table.name))
            expected = {column.name for column in table.columns}

            assert not (expected - migrated), (
                f"{table.name} still missing "
                f"{sorted(expected - migrated)}"
            )
    finally:
        database.close()


# --------------------------------------------------------------------------
# migration DDL generation
# --------------------------------------------------------------------------


def test_migration_ddl_gives_non_nullable_columns_a_default(tmp_path):
    """SQLite cannot add a NOT NULL column without a default."""
    database = DatabaseManager(tmp_path / "ddl.db")
    database.initialize()

    try:
        dialect = database.engine.dialect
        incidents = Base.metadata.tables["incidents"]

        severity = incidents.columns["severity"]
        ddl = DatabaseManager._migration_ddl(severity, dialect)

        assert ddl.startswith("severity ")
        assert "DEFAULT ''" in ddl

        threat_score = incidents.columns["threat_score"]
        numeric_ddl = DatabaseManager._migration_ddl(threat_score, dialect)
        assert "DEFAULT 0" in numeric_ddl
    finally:
        database.close()


def test_migration_ddl_leaves_nullable_columns_without_a_default(tmp_path):
    database = DatabaseManager(tmp_path / "ddl2.db")
    database.initialize()

    try:
        dialect = database.engine.dialect
        events = Base.metadata.tables["events"]

        incident_id = events.columns["incident_id"]
        ddl = DatabaseManager._migration_ddl(incident_id, dialect)

        assert "DEFAULT" not in ddl
    finally:
        database.close()


# --------------------------------------------------------------------------
# related storage behaviour that must keep working
# --------------------------------------------------------------------------


def test_json_decoders_tolerate_corrupt_values(tmp_path):
    database = DatabaseManager(tmp_path / "json.db")
    database.initialize()

    try:
        assert database._decode_json(None) == {}
        assert database._decode_json("") == {}
        assert database._decode_json("{not json") == {}
        assert database._decode_json("[1, 2]") == {}
        assert database._decode_json('{"a": 1}') == {"a": 1}

        assert database._decode_json_list(None) == []
        assert database._decode_json_list("") == []
        assert database._decode_json_list("[1, 2]") == [1, 2]
        # A legacy bare path is preserved rather than discarded, so the
        # affected-file record is not lost.
        assert database._decode_json_list("/legacy/path.txt") == [
            "/legacy/path.txt"
        ]
    finally:
        database.close()


def test_list_limit_is_clamped_to_at_least_one(tmp_path):
    database = DatabaseManager(tmp_path / "limits.db")
    database.initialize()

    try:
        for index in range(5):
            database.record_file_event(
                {
                    "action": "modified",
                    "path": f"f{index}.txt",
                    "metadata": {},
                }
            )

        assert len(database.list_events(limit=0)) == 1
        assert len(database.list_events(limit=-10)) == 1
        assert len(database.list_events(limit=3)) == 3
        assert len(database.list_events(limit=1000)) == 5
    finally:
        database.close()


def test_missing_lookups_return_empty_not_errors(tmp_path):
    database = DatabaseManager(tmp_path / "missing.db")
    database.initialize()

    try:
        assert database.get_incident(999_999) is None
        assert database.list_events_for_incident(999_999) == []
        assert database.list_incidents(limit=10) == []
        assert database.list_alerts(limit=10) == []
    finally:
        database.close()


def test_context_manager_closes_the_engine(tmp_path):
    with DatabaseManager(tmp_path / "ctx.db") as database:
        database.initialize()
        database.record_file_event(
            {"action": "created", "path": "c.txt", "metadata": {}}
        )
        assert database.count_records("events") == 1

    assert database.engine is None


def test_attach_never_reassigns_events_owned_by_another_incident(tmp_path):
    """Concurrent incidents must stay isolated."""
    database = DatabaseManager(tmp_path / "attach.db")
    database.initialize()

    try:
        database.record_file_event(
            {"action": "modified", "path": "owned.txt", "metadata": {}}
        )
        database.record_file_event(
            {"action": "modified", "path": "free.txt", "metadata": {}}
        )

        assert database.attach_events_to_incident_by_paths(1, ["owned.txt"]) == 1
        # Re-attaching to a different incident must not steal the event.
        assert database.attach_events_to_incident_by_paths(2, ["owned.txt"]) == 0
        assert database.attach_events_to_incident_by_paths(2, ["free.txt"]) == 1

        # A None incident id attributes nothing.
        assert database.attach_events_to_incident_by_paths(None, ["free.txt"]) == 0
    finally:
        database.close()


def test_record_score_range_is_enforced(tmp_path):
    database = DatabaseManager(tmp_path / "range.db")
    database.initialize()

    try:
        database.record_score(0.0, "NORMAL", "min")
        database.record_score(100.0, "CRITICAL", "max")

        for invalid in (-0.1, 100.1, 1000.0):
            with pytest.raises(ValueError):
                database.record_score(invalid, "CRITICAL", "invalid")

        assert database.count_records("scores") == 2
    finally:
        database.close()


# --------------------------------------------------------------------------
# FR-05 API contract: readers must expose the keys the dashboard reads
# --------------------------------------------------------------------------


def test_every_reader_exposes_created_at(tmp_path):
    """Events, alerts and incidents expose ``created_at`` (never ``timestamp``)."""
    from rdrs.telemetry import ProcessMonitor

    database = DatabaseManager(tmp_path / "contract.db")
    database.initialize()

    try:
        monitor = ProcessMonitor()
        monitor.prime()
        database.record_processes(monitor.capture_all()[:2])

        database.record_file_event(
            {"action": "modified", "path": "/s/a.txt", "metadata": {}}
        )
        database.record_alert("CRITICAL", "m", evidence={})
        database.record_incident(
            severity="CRITICAL",
            description="d",
            threat_score=95.0,
            threat_level="CRITICAL",
            affected_files=[],
            suspect_process={},
        )
        database.record_score(10.0, "NORMAL", "probe")

        for reader in (
            database.list_events(limit=1),
            database.list_alerts(limit=1),
            database.list_incidents(limit=1),
            database.list_scores(limit=1),
            database.list_processes(limit=1),
        ):
            assert reader, "reader returned no rows"

        for row in (
            database.list_events(limit=1)[0],
            database.list_alerts(limit=1)[0],
            database.list_incidents(limit=1)[0],
        ):
            assert "created_at" in row, f"missing created_at: {sorted(row)}"
            assert "timestamp" not in row
            assert row["created_at"], "created_at must be populated"
    finally:
        database.close()


def test_dashboard_timestamp_calls_use_a_served_field(tmp_path):
    """Every ``formatTimestamp(x.field)`` must name a field the API serves.

    The dashboard called ``formatTimestamp(event.timestamp)``,
    ``formatTimestamp(alert.timestamp)`` and
    ``formatTimestamp(incident.timestamp)`` while the API sends ``created_at``,
    so the Events, Alerts and Incidents tables rendered no timestamp at all.
    """
    import re

    from rdrs.dashboard_data import DashboardData

    database = DatabaseManager(tmp_path / "ts.db")
    database.initialize()

    try:
        database.record_file_event(
            {"action": "modified", "path": "/s/a.txt", "metadata": {}}
        )
        database.record_alert("CRITICAL", "m", evidence={})
        database.record_incident(
            severity="CRITICAL",
            description="d",
            threat_score=95.0,
            threat_level="CRITICAL",
            affected_files=[],
            suspect_process={},
        )

        data = DashboardData(database)

        served = set()
        for rows in (
            data.recent_events(limit=1),
            data.recent_alerts(limit=1),
            data.recent_incidents(limit=1),
            data.recent_processes(limit=1),
        ):
            if rows:
                served |= set(rows[0].keys())

        html = Path("src/rdrs/dashboard/index.html").read_text(encoding="utf-8")

        referenced = set(
            re.findall(r"formatTimestamp\(\s*\w+\.(\w+)\s*\)", html)
        )

        assert referenced, "no formatTimestamp calls found"

        unknown = sorted(referenced - served)

        assert not unknown, (
            "the dashboard formats a timestamp from a field the API does not "
            f"serve: {unknown}"
        )
    finally:
        database.close()


def test_all_five_required_tables_round_trip_real_rows(tmp_path):
    """FSD FR-5: events, processes, alerts and incidents in SQLite."""
    from rdrs.telemetry import ProcessMonitor

    database = DatabaseManager(tmp_path / "five.db")
    database.initialize()

    try:
        monitor = ProcessMonitor()
        monitor.prime()
        database.record_processes(monitor.capture_all()[:3])

        database.record_file_event(
            {"action": "modified", "path": "/s/a.txt", "metadata": {"entropy": 7.9}}
        )
        score_id = database.record_score(95.0, "CRITICAL", "burst")
        alert_id = database.record_alert("CRITICAL", "burst", evidence={})
        incident_id = database.record_incident(
            severity="CRITICAL",
            description="burst",
            threat_score=95.0,
            threat_level="CRITICAL",
            affected_files=["/s/a.txt"],
            suspect_process={"name": "probe.exe"},
            score_id=score_id,
            alert_id=alert_id,
        )

        for table in ("events", "processes", "scores", "alerts", "incidents"):
            assert database.count_records(table) > 0, f"{table} is empty"

        incident = database.get_incident(incident_id)

        assert incident["score_id"] == score_id
        assert incident["alert_id"] == alert_id
        assert incident["threat_score"] == 95.0
        assert incident["threat_level"] == "CRITICAL"
        assert incident["affected_files"] == ["/s/a.txt"]
        assert incident["suspect_process"]["name"] == "probe.exe"
    finally:
        database.close()
