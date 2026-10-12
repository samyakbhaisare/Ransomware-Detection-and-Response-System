from pathlib import Path
from datetime import datetime
import time

from rdrs.monitoring import RealtimeFileMonitor
from rdrs.storage import DatabaseManager


def wait_for_count(
    db: DatabaseManager,
    expected: int,
) -> bool:
    deadline = time.monotonic() + 5

    while time.monotonic() < deadline:
        if db.count_records("file_events") >= expected:
            return True

        time.sleep(0.05)

    return False


def test_real_file_event_is_automatically_persisted(
    tmp_path: Path,
):
    db_path = tmp_path / "rdrs.db"

    database = DatabaseManager(db_path)
    database.initialize()

    watch_dir = tmp_path / "watched"
    watch_dir.mkdir()
    observed = []

    monitor = RealtimeFileMonitor(
        watch_dir,
        callback=observed.append,
        database=database,
    )

    monitor.start()

    try:
        # Real filesystem operation.
        test_file = watch_dir / "evidence.txt"

        test_file.write_text(
            "real monitoring evidence",
            encoding="utf-8",
        )

        # Wait until the real watchdog event reaches SQLite.
        assert wait_for_count(database, 1)

        with database.engine.connect() as connection:
            rows = connection.exec_driver_sql(
                """
                SELECT path, action, metadata_json
                FROM events
                WHERE path = ?
                ORDER BY id ASC
                """,
                (str(test_file),),
            ).fetchall()

        assert rows
        observed_event = next(
            event for event in observed if event.path == str(test_file)
        )

        actions = [row[1] for row in rows]

        # Windows watchdog can emit both created and modified.
        assert "created" in actions

        created_row = next(
            row for row in rows
            if row[1] == "created"
        )

        assert created_row[0] == str(test_file)
        assert "sha256" in created_row[2]
        assert observed_event.event_id is not None
        assert any(row[0] == str(test_file) for row in rows)

        stored_event = next(
            event
            for event in database.list_events(limit=20)
            if event["id"] == observed_event.event_id
        )
        observed_time = time.time() - observed_event.timestamp
        stored_timestamp = datetime.fromisoformat(
            stored_event["created_at"].replace("Z", "+00:00")
        ).timestamp()
        stored_time = time.time() - stored_timestamp
        assert abs(observed_time - stored_time) < 2.0

    finally:
        monitor.stop()
        database.close()

    assert db_path.exists()
