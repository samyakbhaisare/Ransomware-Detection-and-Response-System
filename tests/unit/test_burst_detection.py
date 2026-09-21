from __future__ import annotations

from pathlib import Path

import pytest

from rdrs.burst_detection import BurstDetectionEngine


class FakeClock:
    """Deterministic clock for sliding-window tests."""

    def __init__(self, value: float = 0.0):
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def make_event(
    path: str,
    action: str = "modified",
    *,
    entropy: float | None = 7.9,
    exists: bool = True,
    is_file: bool = True,
    previous_path: str | None = None,
    new_path: str | None = None,
) -> dict:
    return {
        "path": path,
        "action": action,
        "previous_path": previous_path,
        "new_path": new_path,
        "metadata": {
            "exists": exists,
            "is_file": is_file,
            "extension": Path(
                new_path or path
            ).suffix.lower(),
            "entropy": entropy,
        },
    }


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def engine(clock):
    return BurstDetectionEngine(
        clock=clock,
        thresholds={
            "window_seconds": 60,
            "modified_files_per_minute": 20,
            "renames": 10,
            "extension_changes": 5,
            "average_entropy": 7.0,
            "cpu_percent": 80,
        },
    )


def test_normal_activity_stays_below_burst_threshold(
    engine,
):
    for index in range(5):
        result = engine.add_event(
            make_event(
                f"sandbox/file_{index}.txt",
                entropy=4.0,
            )
        )

    assert result.metrics.modified_files_per_minute < 20
    assert result.rapid_encryption is False
    assert result.mass_rename is False
    assert result.high_entropy is False
    assert result.dangerous_burst is False
    assert result.score.total == 0
    assert result.level == "NORMAL"


def test_modified_files_per_minute_triggers_rapid_encryption(
    engine,
):
    result = None

    for index in range(20):
        result = engine.add_event(
            make_event(
                f"sandbox/file_{index}.txt",
                entropy=4.0,
            )
        )

    assert result is not None
    assert result.metrics.modified_files_per_minute == 20
    assert result.rapid_encryption is True
    assert result.score.rapid_encryption == 40
    assert result.dangerous_burst is True


def test_duplicate_modify_events_do_not_fake_unique_file_count(
    engine,
):
    engine.add_event(
        make_event(
            "sandbox/file.txt",
            entropy=4.0,
        )
    )

    result = engine.add_event(
        make_event(
            "sandbox/file.txt",
            entropy=4.0,
        )
    )

    assert result.metrics.modified_files_per_minute == 1
    assert result.rapid_encryption is False


def test_mass_rename_and_extension_change_are_counted(
    engine,
):
    result = None

    for index in range(10):
        old_path = f"sandbox/file_{index}.txt"
        new_path = f"sandbox/file_{index}.locked"

        result = engine.add_event(
            make_event(
                new_path,
                "renamed",
                entropy=4.0,
                previous_path=old_path,
                new_path=new_path,
            )
        )

    assert result is not None
    assert result.metrics.rename_count == 10
    assert result.metrics.extension_change_count == 10
    assert result.mass_rename is True
    assert result.score.mass_rename == 30


def test_high_average_entropy_triggers_signal(
    engine,
):
    result = engine.add_event(
        make_event(
            "sandbox/random.bin",
            entropy=7.9,
        )
    )

    assert result.metrics.average_entropy == pytest.approx(7.9)
    assert result.high_entropy is True
    assert result.score.high_entropy == 25


def test_low_average_entropy_does_not_trigger_signal(
    engine,
):
    result = engine.add_event(
        make_event(
            "sandbox/normal.txt",
            entropy=3.5,
        )
    )

    assert result.metrics.average_entropy == pytest.approx(3.5)
    assert result.high_entropy is False
    assert result.score.high_entropy == 0


def test_cpu_spike_uses_real_process_telemetry(
    engine,
):
    engine.update_process(
        {
            "pid": 1234,
            "name": "python.exe",
            "executable_path": (
                r"C:\Python\python.exe"
            ),
            "cpu_percent": 85.0,
        }
    )

    result = engine.evaluate()

    assert result.metrics.cpu_percent == 85.0
    assert result.cpu_spike is True
    assert result.score.cpu_spike == 15
    assert result.unknown_program is False


def test_missing_process_identity_is_unknown(
    engine,
):
    engine.update_process(
        {
            "pid": 1234,
            "name": "",
            "executable_path": "",
            "cpu_percent": 1.0,
        }
    )

    result = engine.evaluate()

    assert result.unknown_program is True
    assert result.score.unknown_program == 10


def test_known_process_is_not_marked_unknown(
    engine,
):
    engine.update_process(
        {
            "pid": 1234,
            "name": "python.exe",
            "executable_path": (
                r"C:\Python\python.exe"
            ),
            "cpu_percent": 1.0,
        }
    )

    result = engine.evaluate()

    assert result.unknown_program is False


def test_sliding_window_expires_old_events(
    engine,
    clock,
):
    for index in range(20):
        engine.add_event(
            make_event(
                f"sandbox/file_{index}.txt",
                entropy=4.0,
            )
        )

    before_expiry = engine.evaluate()

    assert before_expiry.rapid_encryption is True

    clock.advance(61)

    after_expiry = engine.evaluate()

    assert after_expiry.window_size if hasattr(
        after_expiry,
        "window_size",
    ) else True

    assert (
        after_expiry.metrics.modified_files_per_minute
        == 0
    )
    assert after_expiry.rapid_encryption is False
    assert after_expiry.mass_rename is False
    assert after_expiry.high_entropy is False


def test_critical_score_is_capped_at_100(
    engine,
):
    engine.update_process(
        {
            "pid": 999,
            "name": "",
            "executable_path": "",
            "cpu_percent": 95.0,
        }
    )

    result = None

    for index in range(20):
        result = engine.add_event(
            make_event(
                f"sandbox/file_{index}.txt",
                entropy=7.9,
            )
        )

    assert result is not None

    assert result.rapid_encryption is True
    assert result.high_entropy is True
    assert result.cpu_spike is True
    assert result.unknown_program is True

    assert result.score.total <= 100
    assert result.level == "CRITICAL"


def test_affected_files_contains_existing_files_only(
    engine,
):
    engine.add_event(
        make_event(
            "sandbox/file.txt",
            entropy=7.9,
            exists=True,
            is_file=True,
        )
    )

    engine.add_event(
        make_event(
            "sandbox/deleted.txt",
            "deleted",
            entropy=None,
            exists=False,
            is_file=False,
        )
    )

    result = engine.evaluate()

    assert "sandbox/file.txt" in result.affected_files
    assert "sandbox/deleted.txt" not in result.affected_files


def test_renamed_destination_is_affected_file(
    engine,
):
    result = engine.add_event(
        make_event(
            "sandbox/file.locked",
            "renamed",
            entropy=7.9,
            exists=True,
            is_file=True,
            previous_path="sandbox/file.txt",
            new_path="sandbox/file.locked",
        )
    )

    assert "sandbox/file.locked" in result.affected_files
    assert "sandbox/file.txt" not in result.affected_files


def test_configurable_thresholds_are_applied(
    clock,
):
    engine = BurstDetectionEngine(
        clock=clock,
        thresholds={
            "window_seconds": 30,
            "modified_files_per_minute": 4,
            "renames": 3,
            "extension_changes": 2,
            "average_entropy": 6.5,
            "cpu_percent": 70,
        },
    )

    result = None

    for index in range(2):
        result = engine.add_event(
            make_event(
                f"sandbox/file_{index}.txt",
                entropy=7.0,
            )
        )

    assert result is not None
    assert result.metrics.modified_files_per_minute == 4
    assert result.rapid_encryption is True
    assert result.high_entropy is True


def test_custom_scoring_weights_are_used(
    clock,
):
    engine = BurstDetectionEngine(
        clock=clock,
        scoring_weights={
            "rapid_encryption": 50,
            "mass_rename": 20,
            "high_entropy": 10,
            "cpu_spike": 10,
            "unknown_program": 10,
        },
    )

    for index in range(20):
        result = engine.add_event(
            make_event(
                f"sandbox/file_{index}.txt",
                entropy=7.9,
            )
        )

    assert result.score.rapid_encryption == 50
    assert result.score.high_entropy == 10
    assert result.score.total == 60


def test_reset_clears_window_and_process_state(
    engine,
):
    engine.update_process(
        {
            "pid": 1,
            "name": "python.exe",
            "executable_path": (
                r"C:\Python\python.exe"
            ),
            "cpu_percent": 50,
        }
    )

    engine.add_event(
        make_event(
            "sandbox/file.txt",
            entropy=7.9,
        )
    )

    assert engine.window_size == 1
    assert engine.latest_process is not None

    engine.reset()

    assert engine.window_size == 0
    assert engine.latest_process is None
    assert engine.latest_cpu_percent == 0.0