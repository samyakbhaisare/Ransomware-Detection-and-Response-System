from pathlib import Path
from threading import Event
import time

from rdrs.monitoring import RealtimeFileMonitor


def wait_for_event(events, action, timeout=5):
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if any(event["action"] == action for event in events):
            return True
        time.sleep(0.05)

    return False


def test_realtime_monitor_detects_create_modify_delete_rename(tmp_path: Path):
    events = []

    def callback(event):
        events.append(event)

    monitor = RealtimeFileMonitor(
        tmp_path,
        callback=callback,
    )

    monitor.start()

    try:
        # CREATE
        test_file = tmp_path / "test.txt"
        test_file.write_text("initial", encoding="utf-8")

        assert wait_for_event(events, "created")

        # Give the Windows filesystem watcher time to finish
        # processing the create event before modifying/renaming.
        time.sleep(0.2)

        # MODIFY
        test_file.write_text("modified", encoding="utf-8")

        assert wait_for_event(events, "modified")

        time.sleep(0.2)

        # RENAME
        renamed_file = tmp_path / "renamed.txt"
        test_file.rename(renamed_file)

        assert wait_for_event(events, "renamed")
        assert any(
            event["action"] == "renamed"
            and event["previous_path"] == str(test_file)
            and event["new_path"] == str(renamed_file)
            for event in events
        )

        time.sleep(0.2)

        # DELETE
        renamed_file.unlink()

        assert wait_for_event(events, "deleted")

    finally:
        monitor.stop()

    assert not monitor.running