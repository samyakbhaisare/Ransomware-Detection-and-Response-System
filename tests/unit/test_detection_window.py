"""FR-06 + Build Guide Phase 4 regression tests.

Phase 4 requires a sliding window that recomputes, on every new event:

* files modified per minute
* the number of renames
* the number of extension changes (such as .docx becoming .locked)
* the average entropy of newly written files

...and compares them against the thresholds in ``config.yaml``.

Done-when: "A rapid burst of file changes pushes these counters above their
thresholds, while normal activity stays below them."
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from rdrs.burst_detection import (
    DEFAULT_THRESHOLDS,
    BurstDetectionEngine,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def modify(path: str, *, entropy: float = 3.0, at: float | None = None, engine=None):
    return {
        "action": "modified",
        "path": path,
        "timestamp": at if at is not None else engine._window_now(),
        "extension": Path(path).suffix,
        "metadata": {"entropy": entropy, "exists": True, "is_file": True},
    }


def rename(old: str, new: str, *, at=None, engine=None):
    return {
        "action": "renamed",
        "path": new,
        "timestamp": at if at is not None else engine._window_now(),
        "extension": Path(new).suffix,
        "previous_path": old,
        "new_path": new,
        "metadata": {"exists": True, "is_file": True},
    }


# --------------------------------------------------------------------------
# the four counters
# --------------------------------------------------------------------------


def test_all_four_counters_are_recomputed(tmp_path):
    engine = BurstDetectionEngine()

    for index in range(25):
        engine.ingest(
            modify(str(tmp_path / f"f{index}.txt"), entropy=4.0, engine=engine)
        )

    for index in range(12):
        engine.ingest(
            rename(
                str(tmp_path / f"f{index}.txt"),
                str(tmp_path / f"f{index}.locked"),
                engine=engine,
            )
        )

    metrics = engine.last_analysis.metrics

    assert metrics.modified_files_per_minute > 0
    assert metrics.rename_count == 12
    assert metrics.extension_change_count == 12
    assert metrics.average_entropy > 0


def test_average_entropy_reflects_the_newly_written_files(tmp_path):
    engine = BurstDetectionEngine()

    for index in range(4):
        engine.ingest(
            modify(str(tmp_path / f"e{index}.bin"), entropy=8.0, engine=engine)
        )

    assert engine.last_analysis.metrics.average_entropy == pytest.approx(8.0)


def test_extension_change_is_detected(tmp_path):
    """.docx becoming .locked is one extension change."""
    engine = BurstDetectionEngine()

    engine.ingest(
        rename(
            str(tmp_path / "report.docx"),
            str(tmp_path / "report.locked"),
            engine=engine,
        )
    )

    assert engine.last_analysis.metrics.extension_change_count == 1


def test_same_extension_rename_is_not_an_extension_change(tmp_path):
    engine = BurstDetectionEngine()

    engine.ingest(
        rename(
            str(tmp_path / "a.txt"),
            str(tmp_path / "b.txt"),
            engine=engine,
        )
    )

    assert engine.last_analysis.metrics.rename_count == 1
    assert engine.last_analysis.metrics.extension_change_count == 0


# --------------------------------------------------------------------------
# the sliding window
# --------------------------------------------------------------------------


def test_events_fall_out_of_the_sliding_window(tmp_path):
    engine = BurstDetectionEngine()

    base = engine._window_now()

    for index in range(30):
        engine.ingest(
            modify(str(tmp_path / f"old{index}.txt"), at=base, engine=engine)
        )

    assert engine.last_analysis.metrics.modified_files_per_minute > 0

    # Move the window clock past window_seconds.
    window = engine.thresholds["window_seconds"]

    for index in range(3):
        engine.ingest(
            modify(
                str(tmp_path / f"new{index}.txt"),
                at=base + window + 5,
                engine=engine,
            )
        )

    metrics = engine.last_analysis.metrics

    assert metrics.modified_files_per_minute < 30.0, (
        "expired events were still counted"
    )


# --------------------------------------------------------------------------
# thresholds come from config.yaml
# --------------------------------------------------------------------------


def test_engine_thresholds_come_from_the_shipped_config():
    raw = yaml.safe_load(
        (PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8")
    )
    configured = raw["detection"]

    engine = BurstDetectionEngine(thresholds=configured)

    for key, value in configured.items():
        assert engine.thresholds[key] == value, key

    # The window the guide describes ("for example, the last 60 seconds").
    assert engine.thresholds["window_seconds"] == 60.0


def test_default_thresholds_match_the_guide_values():
    assert DEFAULT_THRESHOLDS == {
        "window_seconds": 60.0,
        "modified_files_per_minute": 20.0,
        "renames": 10.0,
        "extension_changes": 5.0,
        "average_entropy": 7.0,
        "cpu_percent": 80.0,
    }


def test_engine_uses_configured_thresholds_not_defaults(tmp_path):
    """A stricter threshold must change the verdict."""
    strict = BurstDetectionEngine(
        thresholds={
            "window_seconds": 60.0,
            "modified_files_per_minute": 1.0,
            "renames": 1.0,
            "extension_changes": 1.0,
            "average_entropy": 1.0,
            "cpu_percent": 80.0,
        }
    )

    for index in range(3):
        strict.ingest(
            modify(str(tmp_path / f"s{index}.txt"), entropy=2.0, engine=strict)
        )

    assert strict.last_analysis.detected is True

    lenient = BurstDetectionEngine(
        thresholds={
            "window_seconds": 60.0,
            "modified_files_per_minute": 10_000.0,
            "renames": 10_000.0,
            "extension_changes": 10_000.0,
            "average_entropy": 7.9,
            "cpu_percent": 80.0,
        }
    )

    for index in range(3):
        lenient.ingest(
            modify(str(tmp_path / f"l{index}.txt"), entropy=2.0, engine=lenient)
        )

    assert lenient.last_analysis.detected is False


# --------------------------------------------------------------------------
# Phase 4 Done-when
# --------------------------------------------------------------------------


def test_done_when_burst_exceeds_and_normal_stays_below(tmp_path):
    """A rapid burst crosses the thresholds; normal activity does not."""
    burst = BurstDetectionEngine()

    for index in range(60):
        burst.ingest(
            modify(str(tmp_path / f"b{index}.bin"), entropy=7.95, engine=burst)
        )

    for index in range(15):
        burst.ingest(
            rename(
                str(tmp_path / f"b{index}.bin"),
                str(tmp_path / f"b{index}.locked"),
                engine=burst,
            )
        )

    burst_analysis = burst.last_analysis

    assert burst_analysis.detected is True
    assert burst_analysis.metrics.modified_files_per_minute >= 20.0
    assert burst_analysis.metrics.rename_count >= 10
    assert burst_analysis.metrics.extension_change_count >= 5
    assert burst_analysis.metrics.average_entropy >= 7.0

    normal = BurstDetectionEngine()

    for index in range(2):
        normal.ingest(
            modify(str(tmp_path / f"n{index}.txt"), entropy=3.1, engine=normal)
        )

    normal_analysis = normal.last_analysis

    assert normal_analysis.detected is False
    assert normal_analysis.metrics.modified_files_per_minute < 20.0
    assert normal_analysis.metrics.rename_count == 0
    assert normal_analysis.metrics.average_entropy < 7.0
