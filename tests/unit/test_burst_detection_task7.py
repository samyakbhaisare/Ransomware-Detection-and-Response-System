"""Burst-detection coverage for the mapping/property surface (Task 7).

``test_burst_detection.py`` already covers the detection mathematics. These
tests cover the parts that were still unexercised: timestamp coercion, the
public convenience API, validation error paths, window bookkeeping and the
window-start property used for incident attribution (D-04).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from rdrs.burst_detection import (
    BurstAnalysis,
    BurstDetectionEngine,
    BurstMetrics,
    ThreatScoreBreakdown,
    DEFAULT_SCORING_WEIGHTS,
    DEFAULT_THRESHOLDS,
)


class FakeClock:
    def __init__(self, value: float = 0.0):
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def make_event(
    path: str = "C:/sandbox/a.txt",
    action: str = "modified",
    *,
    entropy: float | None = 7.9,
    timestamp: object = None,
) -> dict:
    event = {
        "path": path,
        "action": action,
        "metadata": {"entropy": entropy, "exists": True, "is_file": True},
    }

    if timestamp is not None:
        event["timestamp"] = timestamp

    return event


# ---------------------------------------------------------------------------
# Timestamp coercion
# ---------------------------------------------------------------------------


def test_mapping_timestamps_use_the_timestamp_key():
    engine = BurstDetectionEngine(clock=FakeClock(1000.0))

    analysis = engine.add_event(
        {"path": "C:/a", "action": "modified", "timestamp": {"timestamp": 500.0}}
    )

    assert analysis.window_seconds == pytest.approx(60.0)


def test_naive_datetime_timestamps_are_treated_as_utc():
    engine = BurstDetectionEngine(clock=FakeClock(100000.0))

    naive = datetime(2026, 1, 1, 12, 0, 0)

    analysis = engine.add_event(
        {"path": "C:/a", "action": "modified", "timestamp": naive}
    )

    assert analysis.window_seconds == pytest.approx(60.0)


def test_numeric_and_iso_string_timestamps_are_accepted():
    clock = FakeClock(2000.0)
    engine = BurstDetectionEngine(clock=clock)

    engine.add_event({"path": "C:/num", "action": "modified", "timestamp": 1500.5})
    engine.add_event(
        {"path": "C:/int", "action": "modified", "timestamp": "1501"}
    )
    engine.add_event(
        {
            "path": "C:/iso",
            "action": "modified",
            "timestamp": "2026-01-01T00:00:00Z",
        }
    )
    engine.add_event(
        {
            "path": "C:/naive-iso",
            "action": "modified",
            "timestamp": "2026-01-01T00:00:00",
        }
    )

    assert engine.window_size >= 1


def test_unsupported_timestamp_type_is_rejected():
    engine = BurstDetectionEngine(clock=FakeClock(10.0))

    with pytest.raises(TypeError, match="Unsupported timestamp type"):
        engine.add_event(
            {"path": "C:/a", "action": "modified", "timestamp": ["nope"]}
        )


def test_a_future_event_timestamp_advances_the_evaluation_clock():
    """A clock that lags behind real events must not expire them."""
    clock = FakeClock(0.0)
    engine = BurstDetectionEngine(clock=clock)

    analysis = engine.add_event(
        {"path": "C:/a", "action": "modified", "timestamp": 500.0}
    )

    assert analysis.metrics.event_count == 1


# ---------------------------------------------------------------------------
# add_event validation and aliases
# ---------------------------------------------------------------------------


def test_add_event_rejects_non_mapping_input():
    engine = BurstDetectionEngine(clock=FakeClock(0.0))

    with pytest.raises(TypeError, match="event must be a mapping"):
        engine.add_event(["not", "a", "mapping"])


def test_event_metadata_is_copied_not_aliased():
    engine = BurstDetectionEngine(clock=FakeClock(0.0))

    payload = {
        "path": "C:/a",
        "action": "modified",
        "metadata": {"entropy": 7.9},
    }

    engine.add_event(payload)

    payload["metadata"]["entropy"] = 0.0

    stored = engine.events[0]

    assert stored["metadata"]["entropy"] == pytest.approx(7.9)


def test_ingest_process_event_and_evaluate_are_equivalent():
    clock = FakeClock(0.0)
    engine = BurstDetectionEngine(clock=clock)

    assert engine.ingest(make_event(path="C:/1")).metrics.event_count == 1
    assert engine.process_event(make_event(path="C:/2")).metrics.event_count == 2
    assert engine.evaluate().metrics.event_count == 2
    assert engine.analyze().metrics.event_count == 2
    assert engine.analyse().metrics.event_count == 2


# ---------------------------------------------------------------------------
# Process handling
# ---------------------------------------------------------------------------


def test_update_process_rejects_non_mapping():
    engine = BurstDetectionEngine(clock=FakeClock(0.0))

    with pytest.raises(TypeError, match="process must be a mapping or None"):
        engine.update_process("powershell")


def test_update_process_and_set_process_alias():
    engine = BurstDetectionEngine(clock=FakeClock(0.0))

    analysis = engine.set_process({"name": "python.exe", "cpu_percent": 12.0})

    assert engine.process == {"name": "python.exe", "cpu_percent": 12.0}
    assert engine.latest_process == engine.process
    assert engine.latest_cpu_percent == pytest.approx(12.0)
    assert analysis.metrics.cpu_percent == pytest.approx(12.0)

    engine.update_process(None)

    assert engine.process is None
    assert engine.latest_process is None
    assert engine.latest_cpu_percent == 0.0


def test_latest_cpu_percent_ignores_non_numeric_values():
    engine = BurstDetectionEngine(clock=FakeClock(0.0))

    engine.update_process({"name": "x", "cpu_percent": "busy"})

    assert engine.latest_cpu_percent == 0.0


# ---------------------------------------------------------------------------
# Window reset / bookkeeping
# ---------------------------------------------------------------------------


def test_reset_and_clear_and_event_count_aliases():
    clock = FakeClock(0.0)
    engine = BurstDetectionEngine(clock=clock)

    engine.add_event(make_event())
    engine.add_event(make_event(path="C:/b"))

    assert engine.event_count == 2
    assert engine.window_size == 2

    engine.clear()

    assert engine.event_count == 0
    assert engine.window_size == 0
    assert engine.process is None

    engine.add_event(make_event())
    engine.reset()

    assert engine.window_size == 0


def test_window_started_at_is_none_for_an_empty_window():
    engine = BurstDetectionEngine(clock=FakeClock(100.0))

    assert engine.window_started_at is None


def test_window_started_at_returns_the_earliest_tz_aware_timestamp():
    """D-04: the burst window start is used for incident attribution."""
    clock = FakeClock(1000.0)
    engine = BurstDetectionEngine(
        clock=clock,
        thresholds={**DEFAULT_THRESHOLDS, "window_seconds": 3600},
    )

    engine.add_event(
        {"path": "C:/late", "action": "modified", "timestamp": 800.0}
    )
    engine.add_event(
        {"path": "C:/early", "action": "modified", "timestamp": 200.0}
    )

    started = engine.window_started_at

    assert started is not None
    assert started.tzinfo is not None
    assert started == datetime.fromtimestamp(200.0, tz=timezone.utc)


def test_window_started_at_advances_as_events_expire():
    clock = FakeClock(0.0)
    engine = BurstDetectionEngine(
        clock=clock,
        thresholds={**DEFAULT_THRESHOLDS, "window_seconds": 10},
    )

    engine.add_event({"path": "C:/first", "action": "modified", "timestamp": 0.0})
    clock.advance(20.0)
    engine.add_event({"path": "C:/second", "action": "modified", "timestamp": 20.0})

    started = engine.window_started_at

    assert started == datetime.fromtimestamp(20.0, tz=timezone.utc)


# ---------------------------------------------------------------------------
# Threshold / weight validation
# ---------------------------------------------------------------------------


def test_thresholds_and_weights_round_trip():
    clock = FakeClock(0.0)
    engine = BurstDetectionEngine(clock=clock)

    analysis = engine.update_thresholds(
        {**DEFAULT_THRESHOLDS, "window_seconds": 120.0}
    )

    assert engine.thresholds["window_seconds"] == pytest.approx(120.0)
    assert analysis.window_seconds == pytest.approx(120.0)

    engine.update_scoring_weights(
        {**DEFAULT_SCORING_WEIGHTS, "rapid_encryption": 50.0}
    )

    assert engine.scoring_weights["rapid_encryption"] == pytest.approx(50.0)

    snapshot = engine.to_dict()

    assert snapshot["thresholds"]["window_seconds"] == pytest.approx(120.0)
    assert snapshot["scoring_weights"]["rapid_encryption"] == pytest.approx(50.0)
    assert "analysis" in snapshot
    assert "process" in snapshot


def test_invalid_thresholds_are_rejected():
    engine = BurstDetectionEngine(clock=FakeClock(0.0))

    with pytest.raises(ValueError, match="Invalid detection threshold"):
        engine.update_thresholds(
            {**DEFAULT_THRESHOLDS, "window_seconds": "soon"}
        )

    with pytest.raises(ValueError, match="cannot be negative"):
        engine.update_thresholds(
            {**DEFAULT_THRESHOLDS, "renames": -1.0}
        )

    with pytest.raises(ValueError, match="window_seconds must be greater than zero"):
        engine.update_thresholds(
            {**DEFAULT_THRESHOLDS, "window_seconds": 0.0}
        )


def test_invalid_weights_are_rejected():
    engine = BurstDetectionEngine(clock=FakeClock(0.0))

    with pytest.raises(ValueError, match="Invalid scoring weight"):
        engine.update_scoring_weights(
            {**DEFAULT_SCORING_WEIGHTS, "mass_rename": "lots"}
        )

    with pytest.raises(ValueError, match="cannot be negative"):
        engine.update_scoring_weights(
            {**DEFAULT_SCORING_WEIGHTS, "cpu_spike": -5.0}
        )


def test_partial_threshold_and_weight_updates_keep_the_other_defaults():
    engine = BurstDetectionEngine(clock=FakeClock(0.0))

    engine.update_thresholds({"window_seconds": 30.0})
    engine.update_scoring_weights({"rapid_encryption": 55.0})

    assert engine.thresholds["window_seconds"] == pytest.approx(30.0)
    assert engine.thresholds["renames"] == pytest.approx(
        DEFAULT_THRESHOLDS["renames"]
    )
    assert engine.scoring_weights["rapid_encryption"] == pytest.approx(55.0)
    assert engine.scoring_weights["mass_rename"] == pytest.approx(
        DEFAULT_SCORING_WEIGHTS["mass_rename"]
    )


# ---------------------------------------------------------------------------
# Duplicate / degenerate events
# ---------------------------------------------------------------------------


def test_duplicate_modifications_of_one_file_do_not_inflate_the_rate():
    clock = FakeClock(0.0)
    engine = BurstDetectionEngine(clock=clock)

    for _ in range(30):
        engine.add_event(make_event(path="C:/same.docx"))

    analysis = engine.last_analysis

    # Threshold is 20 *unique* modified files per minute; one file cannot trip it.
    assert analysis.metrics.event_count == 30
    assert analysis.metrics.modified_files_per_minute <= 1.0
    assert "rapid_encryption" not in analysis.signals


def test_extension_change_is_counted_through_previous_and_new_paths():
    clock = FakeClock(0.0)
    engine = BurstDetectionEngine(
        clock=clock,
        thresholds={
            **DEFAULT_THRESHOLDS,
            "extension_changes": 1,
            "renames": 1,
            "modified_files_per_minute": 1000,
            "average_entropy": 8.0,
        },
    )

    analysis = engine.add_event(
        {
            "path": "C:/a.locked",
            "action": "renamed",
            "previous_path": "C:/a.txt",
            "new_path": "C:/a.locked",
            "metadata": {},
        }
    )

    assert analysis.metrics.extension_change_count == 1
    assert "mass_rename" in analysis.signals


def test_events_without_paths_do_not_count_as_modified_files():
    clock = FakeClock(0.0)
    engine = BurstDetectionEngine(clock=clock)

    engine.add_event({"path": "", "action": "modified", "metadata": {}})

    assert engine.window_size == 1
    assert engine.last_analysis.metrics.modified_files_per_minute == 0.0
    assert engine.last_analysis.affected_files == ()


# ---------------------------------------------------------------------------
# Data-class surface
# ---------------------------------------------------------------------------


def test_metrics_expose_every_field_and_serialize():
    metrics = BurstMetrics(
        modified_files_per_minute=25.0,
        rename_count=11,
        extension_change_count=6,
        average_entropy=7.8,
        event_count=7,
        cpu_percent=88.0,
    )

    assert metrics.event_count == 7
    assert metrics.to_dict() == {
        "modified_files_per_minute": 25.0,
        "rename_count": 11,
        "extension_change_count": 6,
        "average_entropy": 7.8,
        "event_count": 7,
        "cpu_percent": 88.0,
    }
    assert metrics.to_dict() is not metrics.to_dict()


def test_breakdown_exposes_total_score_and_serialization():
    breakdown = ThreatScoreBreakdown(
        rapid_encryption=40.0,
        mass_rename=30.0,
        high_entropy=25.0,
        cpu_spike=15.0,
        unknown_program=10.0,
    )

    assert breakdown.total == pytest.approx(100.0)
    assert breakdown.score == pytest.approx(100.0)

    as_dict = breakdown.to_dict()

    assert as_dict["total"] == pytest.approx(100.0)
    assert as_dict["rapid_encryption"] == pytest.approx(40.0)


def test_breakdown_is_capped_at_one_hundred_and_floored_at_zero():
    breakdown = ThreatScoreBreakdown(
        rapid_encryption=90.0,
        mass_rename=90.0,
        high_entropy=90.0,
        cpu_spike=90.0,
        unknown_program=90.0,
    )

    assert breakdown.total == pytest.approx(100.0)
    assert ThreatScoreBreakdown().total == pytest.approx(0.0)


def test_analysis_to_dict_and_dict_are_equivalent():
    clock = FakeClock(0.0)
    engine = BurstDetectionEngine(clock=clock)

    analysis = engine.add_event(make_event())

    assert isinstance(analysis, BurstAnalysis)
    assert analysis.to_dict() == analysis.dict()
    assert "affected_files" in analysis.to_dict()


def test_analysis_signal_properties_and_dangerous_burst():
    clock = FakeClock(0.0)
    engine = BurstDetectionEngine(
        clock=clock,
        thresholds={
            **DEFAULT_THRESHOLDS,
            "modified_files_per_minute": 3,
            "average_entropy": 7.0,
            "renames": 100,
            "extension_changes": 100,
        },
    )

    for index in range(4):
        engine.add_event(make_event(path=f"C:/f{index}.docx", entropy=7.9))

    analysis = engine.last_analysis

    assert analysis.rapid_encryption is True
    assert analysis.high_entropy is True
    assert analysis.mass_rename is False
    assert analysis.cpu_spike is False
    assert analysis.suspect_process is None
    assert analysis.threat_score == analysis.score.total
    assert analysis.event_count == analysis.metrics.event_count


def test_dangerous_burst_requires_a_threshold_breaking_score():
    clock = FakeClock(0.0)
    engine = BurstDetectionEngine(clock=clock)

    # A quiet window is never a dangerous burst.
    analysis = engine.add_event(make_event(entropy=1.0))

    assert analysis.detected is False
    assert analysis.dangerous_burst is False


def test_analysis_carries_timestamp_window_and_process():
    clock = FakeClock(1234.0)
    engine = BurstDetectionEngine(clock=clock)

    engine.update_process({"name": "proc.exe"})
    engine.add_event(make_event())

    analysis = engine.last_analysis

    assert analysis.timestamp == pytest.approx(1234.0)
    assert analysis.window_seconds == pytest.approx(60.0)
    assert analysis.process == {"name": "proc.exe"}
    assert analysis.suspect_process == {"name": "proc.exe"}
    assert analysis.events
