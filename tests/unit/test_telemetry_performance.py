"""Regression tests for the process-telemetry performance fix.

The defect: ``ProcessTelemetry.from_pid`` called
``psutil.Process.cpu_percent(interval=0.05)``, which *sleeps* 50 ms per
process. A full capture of ~385 processes therefore took ~26 seconds, so the
process-monitor cycle could not keep pace and short monitoring runs persisted
no process rows at all.

The fix has three parts, each tested here:

1. CPU percentages are derived by :class:`CpuSampler` from the delta between
   consecutive captures, so nothing sleeps.
2. Attributes are read in one batched ``process_iter(attrs=...)`` pass.
3. The monitor persists the whole capture in a single transaction.

These tests assert behaviour, not wall-clock timings, so they do not become
flaky on a slow machine.
"""

from __future__ import annotations

import os

import psutil
import pytest

from rdrs.telemetry import (
    CpuSampler,
    ProcessMonitor,
    ProcessTelemetry,
)


# --------------------------------------------------------------------------
# CpuSampler
# --------------------------------------------------------------------------


def test_cpu_sampler_first_observation_is_not_measurable():
    """With no baseline there is nothing to compare against: report 0.0.

    0.0 means "not yet measurable" -- it must never be a fabricated guess.
    """
    sampler = CpuSampler()

    assert sampler.observe(1234, 10.0) == 0.0


def test_cpu_sampler_computes_real_delta(monkeypatch):
    """The percentage is derived from the CPU-seconds delta between samples."""
    sampler = CpuSampler()

    clock = iter([100.0, 100.5])
    monkeypatch.setattr(
        "rdrs.telemetry.time.monotonic",
        lambda: next(clock),
    )

    # First observation establishes the baseline.
    assert sampler.observe(1, 10.0) == 0.0

    # 0.25 CPU-seconds over 0.5 wall seconds = 50% of one core.
    assert sampler.observe(1, 10.25) == pytest.approx(50.0)


def test_cpu_sampler_caps_at_one_hundred(monkeypatch):
    """Values are capped at 100 to match the configured 0-100 threshold range."""
    sampler = CpuSampler()

    clock = iter([0.0, 0.1])
    monkeypatch.setattr(
        "rdrs.telemetry.time.monotonic",
        lambda: next(clock),
    )

    sampler.observe(1, 0.0)

    # 4 CPU-seconds in 0.1 wall seconds would be 4000%.
    assert sampler.observe(1, 4.0) == 100.0


def test_cpu_sampler_excludes_system_idle_process():
    """pid 0 must never report CPU usage.

    On Windows pid 0 is the "System Idle Process": its ``cpu_times`` track
    *idle* time, so a delta-based reading is ~100%. Reporting that as CPU usage
    would make the idle process the highest-CPU process every cycle and fire
    the ``cpu_spike`` signal continuously.
    """
    sampler = CpuSampler()

    assert 0 in CpuSampler.NON_MEASURABLE_PIDS
    assert sampler.observe(0, 5_000_000.0) == 0.0
    assert sampler.sample(0) == 0.0


def test_cpu_sampler_retain_prunes_dead_processes():
    sampler = CpuSampler()

    sampler.observe(1, 1.0)
    sampler.observe(2, 2.0)
    sampler.observe(3, 3.0)

    sampler.retain({1, 3})

    assert set(sampler._baseline) == {1, 3}


# --------------------------------------------------------------------------
# the blocking call is gone
# --------------------------------------------------------------------------


def test_from_pid_does_not_call_the_blocking_cpu_accessor(monkeypatch):
    """With a supplied CPU value, psutil's blocking accessor must not be used.

    This is the deterministic proof that the 50 ms-per-process sleep has been
    removed from the monitor's path -- no timing assertion required.
    """
    def _explode(self, interval=None):
        raise AssertionError(
            "cpu_percent() must not be called when the caller supplies a value"
        )

    monkeypatch.setattr(psutil.Process, "cpu_percent", _explode)

    telemetry = ProcessTelemetry.from_pid(
        os.getpid(),
        cpu_percent=42.5,
    )

    assert telemetry.cpu_percent == 42.5
    assert telemetry.pid == os.getpid()


def test_capture_all_uses_the_batched_attr_pass(monkeypatch):
    """capture_all must read attributes in one batched pass."""
    import rdrs.telemetry as telemetry_module

    seen: dict[str, object] = {}

    original = telemetry_module.psutil.process_iter

    def _spy(attrs=None):
        seen["attrs"] = attrs
        return original(attrs=attrs)

    monkeypatch.setattr(telemetry_module.psutil, "process_iter", _spy)

    ProcessMonitor().capture_all()

    assert seen["attrs"] == ProcessMonitor.CAPTURE_ATTRS
    assert "cpu_times" in ProcessMonitor.CAPTURE_ATTRS
    assert "memory_info" in ProcessMonitor.CAPTURE_ATTRS
    assert "io_counters" in ProcessMonitor.CAPTURE_ATTRS


# --------------------------------------------------------------------------
# capture behaviour
# --------------------------------------------------------------------------


def test_capture_all_returns_guide_required_fields():
    """A real capture must carry every field the Build Guide Phase 3 lists."""
    monitor = ProcessMonitor()
    monitor.prime()

    captured = monitor.capture_all()

    assert captured, "a real capture returned no processes"

    sample = captured[0]

    for field in (
        "pid",
        "name",
        "cpu_percent",
        "memory_percent",
        "disk_write_bytes",
        "executable_path",
        "parent_pid",
    ):
        assert hasattr(sample, field), f"missing guide field: {field}"

    # memory_percent is derived from rss against total memory.
    assert sample.memory_percent >= 0.0
    assert sample.cpu_percent >= 0.0


def test_prime_establishes_baselines_then_real_values_appear():
    """After priming, a later capture reports genuine CPU percentages."""
    import time

    monitor = ProcessMonitor()
    assert monitor.prime() > 0

    time.sleep(0.3)
    first = monitor.capture_all()

    # Something on a busy machine is using CPU; the sampler must be able to
    # observe a non-zero value rather than reporting a constant zero.
    time.sleep(0.3)
    second = monitor.capture_all()

    assert len(second) > 0
    assert all(process.cpu_percent >= 0.0 for process in second)
    assert max(process.cpu_percent for process in second) > 0.0


def test_capture_all_skips_processes_that_vanish(monkeypatch):
    """A process that disappears mid-iteration must not abort the capture."""
    import rdrs.telemetry as telemetry_module

    class Vanishing:
        pid = 999_999

        @property
        def info(self):
            raise psutil.NoSuchProcess(pid=999_999)

    class Alive:
        pid = 4242

        @property
        def info(self):
            return {
                "pid": 4242,
                "name": "alive",
                "ppid": 1,
                "cpu_times": None,
                "memory_info": None,
                "create_time": None,
                "exe": "",
                "cmdline": [],
                "cwd": "",
                "io_counters": None,
            }

    monkeypatch.setattr(
        telemetry_module.psutil,
        "process_iter",
        lambda attrs=None: [Vanishing(), Alive()],
    )

    captured = ProcessMonitor().capture_all()

    assert [process.pid for process in captured] == [4242]


# --------------------------------------------------------------------------
# suspect selection must ignore kernel pseudo-processes
# --------------------------------------------------------------------------


def test_suspect_selection_excludes_pid_zero():
    from rdrs.runtime import RuntimeManager

    class Snapshot:
        def __init__(self, pid, cpu):
            self.pid = pid
            self.cpu_percent = cpu

    assert RuntimeManager._is_measurable_process(Snapshot(0, 100.0)) is False
    assert RuntimeManager._is_measurable_process(Snapshot(4, 10.0)) is True
    assert RuntimeManager._is_measurable_process(Snapshot(4242, 1.0)) is True


def test_suspect_selection_keeps_snapshots_without_a_pid():
    """An unreadable pid must not silently discard evidence."""
    from rdrs.runtime import RuntimeManager

    class NoPid:
        cpu_percent = 50.0

    assert RuntimeManager._is_measurable_process(NoPid()) is True


# --------------------------------------------------------------------------
# bulk persistence
# --------------------------------------------------------------------------


def test_record_processes_persists_a_whole_capture_in_one_call(tmp_path):
    """One transaction for the whole capture, not one per process."""
    from rdrs.storage import DatabaseManager

    database = DatabaseManager(tmp_path / "bulk.db")
    database.initialize()

    monitor = ProcessMonitor()
    monitor.prime()
    snapshots = monitor.capture_all()

    assert len(snapshots) > 10

    written = database.record_processes(snapshots)

    assert written == len(snapshots)
    assert database.count_records("processes") == len(snapshots)

    # Spot-check that real field values survived the bulk write.
    with database.engine.connect() as connection:
        from sqlalchemy import text

        row = connection.execute(
            text(
                "SELECT pid, name, cpu_percent, memory_percent, "
                "executable_path, parent_pid, disk_write_bytes "
                "FROM processes ORDER BY id LIMIT 1"
            )
        ).fetchone()

    assert row is not None
    assert row[1]
    assert float(row[2]) >= 0.0
    assert float(row[3]) >= 0.0

    database.close()


def test_record_processes_handles_an_empty_capture(tmp_path):
    from rdrs.storage import DatabaseManager

    database = DatabaseManager(tmp_path / "empty.db")
    database.initialize()

    assert database.record_processes([]) == 0
    assert database.count_records("processes") == 0

    database.close()
