"""FR-02 regression tests — real-time file monitoring.

Defects covered
---------------
1. **Exclude patterns did not work.** The Build Guide asks for "exclude
   patterns (for example, ignore temporary files and system files)", but
   ``_excluded`` compared whole path *components* for equality, so ``*.tmp``
   never matched ``skip.tmp`` and ``ignored*`` never matched
   ``ignored.txt``. Every pattern a user would naturally write was silently
   ignored.

2. **Feedback loop.** The runtime monitors ``data/`` recursively and excluded
   only the SQLite files. ``data/quarantine/`` and ``data/reports/`` are
   written *by* RDRS, so quarantining evidence or generating a report produced
   file events that fed straight back into the detector -- the system was
   observing its own output.

Timing: watchdog delivers events asynchronously, so these tests poll with a
timeout instead of sleeping a fixed amount.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from rdrs.monitoring import (
    DirectoryMonitor,
    RealtimeFileMonitor,
    is_excluded_path,
)

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def wait_for(predicate, timeout: float = 6.0, interval: float = 0.05) -> bool:
    """Poll until ``predicate`` is true or the timeout expires."""
    deadline = time.time() + timeout

    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)

    return predicate()


class Recorder:
    """Collects the events a monitor emits."""

    def __init__(self) -> None:
        self.events = []

    def __call__(self, event) -> None:
        self.events.append(event)

    @property
    def paths(self) -> list[str]:
        return [str(getattr(event, "path", "")) for event in self.events]

    @property
    def actions(self) -> set[str]:
        return {str(getattr(event, "action", "")) for event in self.events}

    def saw_path(self, fragment: str) -> bool:
        return any(fragment in path for path in self.paths)


@pytest.fixture
def monitored(tmp_path):
    """A started DirectoryMonitor over a temp directory, stopped afterwards."""
    sandbox = tmp_path / "watched"
    sandbox.mkdir()

    recorders = []

    def build(**kwargs) -> tuple[DirectoryMonitor, Recorder]:
        recorder = Recorder()
        monitor = DirectoryMonitor(sandbox, callback=recorder, **kwargs)
        monitor.start()
        recorders.append(monitor)
        # Let the observer thread come up before writing anything.
        time.sleep(0.4)
        return monitor, recorder

    yield sandbox, build

    for monitor in recorders:
        try:
            monitor.stop()
        except Exception:
            pass


# --------------------------------------------------------------------------
# exclude pattern matching
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "excludes", "expected"),
    [
        ("a/skip.tmp", ["*.tmp"], True),
        ("a/keep.txt", ["*.tmp"], False),
        ("data/rdrs.db", ["rdrs.db"], True),
        ("data/rdrs.db-wal", ["rdrs.db-wal"], True),
        ("data/quarantine/x.txt", ["quarantine"], True),
        ("data/reports/i.json", ["reports"], True),
        ("data/sandbox/real.txt", ["quarantine", "reports"], False),
        ("a/ignored.txt", ["ignored*"], True),
        ("a/kept.txt", ["ignored*"], False),
        ("a/b.txt", [], False),
        ("a/b.txt", [""], False),
        ("a/b.txt", ["   "], False),
        ("dir/sub/CONFIG.YAML", ["config.yaml"], True),
        ("a/.hidden", [".*"], True),
        ("a/one.part", ["*.part"], True),
    ],
)
def test_is_excluded_path_matches_glob_patterns(path, excludes, expected):
    assert is_excluded_path(path, excludes) is expected


def test_exclude_matching_is_case_insensitive():
    assert is_excluded_path("Data/RDRS.DB", ["rdrs.db"]) is True
    assert is_excluded_path("a/SKIP.TMP", ["*.tmp"]) is True


# --------------------------------------------------------------------------
# excludes on a live monitor
# --------------------------------------------------------------------------


def test_glob_pattern_excludes_filter_live_events(monitored):
    """`*.tmp` must actually suppress temporary files."""
    sandbox, build = monitored
    _, recorder = build(excludes=["*.tmp"])

    (sandbox / "keep.txt").write_text("k")
    (sandbox / "skip.tmp").write_text("t")

    wait_for(lambda: recorder.saw_path("keep.txt"))

    assert recorder.saw_path("keep.txt")
    assert not recorder.saw_path("skip.tmp")


def test_exact_name_excludes_filter_live_events(monitored):
    sandbox, build = monitored
    _, recorder = build(excludes=["rdrs.db"])

    (sandbox / "rdrs.db").write_text("database")
    (sandbox / "other.txt").write_text("o")

    wait_for(lambda: recorder.saw_path("other.txt"))

    assert not recorder.saw_path("rdrs.db")
    assert recorder.saw_path("other.txt")


def test_directory_name_excludes_the_whole_subtree(monitored):
    sandbox, build = monitored
    _, recorder = build(excludes=["quarantine"])

    excluded_dir = sandbox / "quarantine"
    excluded_dir.mkdir()
    (excluded_dir / "evidence.txt").write_text("copy")
    (sandbox / "normal.txt").write_text("n")

    wait_for(lambda: recorder.saw_path("normal.txt"))

    assert not recorder.saw_path("quarantine")
    assert recorder.saw_path("normal.txt")


def test_no_excludes_captures_everything(monitored):
    sandbox, build = monitored
    _, recorder = build()

    (sandbox / "anything.tmp").write_text("x")

    assert wait_for(lambda: recorder.saw_path("anything.tmp"))


# --------------------------------------------------------------------------
# the four monitored event kinds
# --------------------------------------------------------------------------


def test_all_four_event_kinds_are_reported(monitored):
    """FSD FR-2: create / modify / delete / rename, in real time."""
    sandbox, build = monitored
    _, recorder = build()

    target = sandbox / "document.txt"
    target.write_text("created")
    wait_for(lambda: recorder.saw_path("document.txt"))

    target.write_text("modified content")
    wait_for(lambda: "modified" in recorder.actions)

    renamed = sandbox / "document.locked"
    target.rename(renamed)
    wait_for(lambda: "renamed" in recorder.actions)

    renamed.unlink()
    wait_for(lambda: "deleted" in recorder.actions)

    assert {"created", "modified", "renamed", "deleted"} <= recorder.actions


def test_rename_populates_previous_and_new_paths(monitored):
    sandbox, build = monitored
    _, recorder = build()

    source = sandbox / "before.txt"
    source.write_text("x")
    wait_for(lambda: recorder.saw_path("before.txt"))

    destination = sandbox / "after.txt"
    source.rename(destination)
    wait_for(lambda: "renamed" in recorder.actions)

    rename_events = [
        event
        for event in recorder.events
        if getattr(event, "action", "") == "renamed"
    ]

    assert rename_events

    event = rename_events[0]
    assert str(getattr(event, "previous_path", "") or "").endswith("before.txt")
    assert str(getattr(event, "new_path", "") or "").endswith("after.txt")


def test_event_object_carries_the_guide_fields(monitored):
    sandbox, build = monitored
    _, recorder = build()

    (sandbox / "fields.txt").write_text("x")
    wait_for(lambda: recorder.saw_path("fields.txt"))

    event = next(
        item for item in recorder.events if "fields.txt" in str(item.path)
    )
    payload = event._as_dict()

    for field in ("action", "path", "timestamp", "extension"):
        assert field in payload, f"missing guide field: {field}"


# --------------------------------------------------------------------------
# edge cases
# --------------------------------------------------------------------------


def test_nested_directories_are_monitored_recursively(monitored):
    sandbox, build = monitored
    _, recorder = build()

    deep = sandbox / "a" / "b" / "c"
    deep.mkdir(parents=True)

    (deep / "deep.txt").write_text("deep")

    assert wait_for(lambda: recorder.saw_path("deep.txt"))


def test_unicode_filenames_are_captured(monitored):
    sandbox, build = monitored
    _, recorder = build()

    name = "\u00fcn\u00efc\u00f6d\u00e9-\u6587\u4ef6.txt"
    (sandbox / name).write_text("unicode")

    assert wait_for(lambda: recorder.saw_path(name))


def test_empty_file_is_captured(monitored):
    sandbox, build = monitored
    _, recorder = build()

    (sandbox / "empty.txt").write_bytes(b"")

    assert wait_for(lambda: recorder.saw_path("empty.txt"))


def test_file_without_extension_is_captured(monitored):
    sandbox, build = monitored
    _, recorder = build()

    (sandbox / "noextension").write_text("x")

    assert wait_for(lambda: recorder.saw_path("noextension"))


def test_dotfile_is_captured(monitored):
    sandbox, build = monitored
    _, recorder = build()

    (sandbox / ".hidden").write_text("h")

    assert wait_for(lambda: recorder.saw_path(".hidden"))


def test_rapid_burst_does_not_lose_events(monitored):
    """Thirty rapid writes must all be observed.

    watchdog delivers asynchronously and can lag behind a tight write loop, so
    this polls generously rather than asserting immediately. Measured against
    raw watchdog as a baseline: both deliver 30/30 distinct files.
    """
    sandbox, build = monitored
    _, recorder = build()

    for index in range(30):
        (sandbox / f"rapid_{index}.txt").write_text("x")

    wait_for(
        lambda: len({p for p in recorder.paths if "rapid_" in p}) >= 30,
        timeout=25.0,
    )

    captured = {p for p in recorder.paths if "rapid_" in p}
    assert len(captured) == 30


# --------------------------------------------------------------------------
# lifecycle
# --------------------------------------------------------------------------


def test_monitor_can_be_stopped_and_restarted(tmp_path):
    sandbox = tmp_path / "restart"
    sandbox.mkdir()

    first = Recorder()
    monitor = DirectoryMonitor(sandbox, callback=first)
    monitor.start()
    time.sleep(0.4)
    (sandbox / "one.txt").write_text("1")
    wait_for(lambda: first.saw_path("one.txt"))
    monitor.stop()

    assert first.saw_path("one.txt")

    second = Recorder()
    monitor2 = DirectoryMonitor(sandbox, callback=second)
    monitor2.start()
    time.sleep(0.4)
    (sandbox / "two.txt").write_text("2")
    wait_for(lambda: second.saw_path("two.txt"))
    monitor2.stop()

    assert second.saw_path("two.txt")


def test_realtime_file_monitor_counts_events(monitored):
    sandbox, build = monitored
    monitor, _ = build()

    realtime = RealtimeFileMonitor(sandbox)
    realtime.start()
    time.sleep(0.4)

    for index in range(4):
        (sandbox / f"counted_{index}.txt").write_text("x")

    assert wait_for(lambda: realtime.event_count > 0, timeout=10.0)

    realtime.stop()


# --------------------------------------------------------------------------
# the runtime must not observe its own output (feedback loop)
# --------------------------------------------------------------------------


def test_runtime_excludes_its_own_output_directories(tmp_path):
    """Quarantine and report writes must not become detection input."""
    from rdrs.runtime import RuntimeManager

    (tmp_path / "data").mkdir()
    (tmp_path / "logs").mkdir()

    config = tmp_path / "rdrs.yaml"
    config.write_text(
        Path("config.yaml")
        .read_text(encoding="utf-8")
        .replace("data_dir: data", f"data_dir: {(tmp_path / 'data').as_posix()}")
        .replace("logs_dir: logs", f"logs_dir: {(tmp_path / 'logs').as_posix()}")
        .replace(
            "db_path: data\\rdrs.db",
            f"db_path: {(tmp_path / 'data' / 'rdrs.db').as_posix()}",
        ),
        encoding="utf-8",
    )

    RuntimeManager.reset_singleton()
    runtime = RuntimeManager(config_path=config)
    runtime.start()

    try:
        time.sleep(1.2)
        before = runtime._database.count_records("events")

        quarantine = tmp_path / "data" / "quarantine"
        quarantine.mkdir(parents=True, exist_ok=True)
        (quarantine / "evidence.txt").write_text("quarantined evidence")

        reports = tmp_path / "data" / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        (reports / "incident_1.json").write_text("{}")

        time.sleep(1.5)

        after_excluded = runtime._database.count_records("events")
        assert after_excluded == before, (
            "RDRS observed its own quarantine/report output"
        )

        # Genuine activity in the monitored tree is still detected.
        sandbox = tmp_path / "data" / "sandbox"
        sandbox.mkdir(parents=True, exist_ok=True)
        (sandbox / "real_activity.txt").write_text("real activity")

        assert wait_for(
            lambda: runtime._database.count_records("events") > after_excluded,
            timeout=10.0,
        )
    finally:
        runtime.stop()
        RuntimeManager.reset_singleton()


def test_runtime_monitor_excludes_cover_own_output():
    """The configured excludes must name everything RDRS writes."""
    source = Path("src/rdrs/runtime.py").read_text(encoding="utf-8-sig")

    for expected in ("rdrs.db", "quarantine", "reports"):
        assert f'"{expected}"' in source, (
            f"the runtime does not exclude {expected!r}"
        )
