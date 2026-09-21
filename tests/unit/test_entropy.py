from pathlib import Path

import pytest

from rdrs.entropy import calculate_entropy, calculate_file_entropy


def test_empty_data_has_zero_entropy():
    assert calculate_entropy(b"") == 0.0


def test_repeated_data_has_zero_entropy():
    assert calculate_entropy(b"A" * 1000) == 0.0


def test_random_like_data_has_high_entropy():
    data = bytes(range(256)) * 100
    entropy = calculate_entropy(data)
    assert 7.9 <= entropy <= 8.0


def test_file_entropy_uses_file_contents(tmp_path: Path):
    path = tmp_path / "normal.txt"
    path.write_text("normal document content " * 100)

    entropy = calculate_file_entropy(path)

    assert 0.0 <= entropy <= 8.0


def test_file_entropy_respects_sample_size(tmp_path: Path):
    path = tmp_path / "sample.bin"
    path.write_bytes(bytes(range(256)) * 1000)

    entropy = calculate_file_entropy(path, sample_size=1024)

    assert 7.9 <= entropy <= 8.0

# ---------------------------------------------------------------------------
# FR-03: the entropy API must not obstruct the activity it observes
# ---------------------------------------------------------------------------


def test_calculate_file_entropy_does_not_block_renames(tmp_path):
    """Inspecting a file must never prevent it being renamed.

    ``calculate_file_entropy`` used a plain ``Path.open("rb")``, which requests
    no ``FILE_SHARE_DELETE`` on Windows, so holding the handle refused any other
    process permission to rename the file (``WinError 32``). A detector must not
    obstruct the activity it exists to observe.
    """
    from rdrs.fileaccess import open_read_shared

    target = tmp_path / "held.txt"
    target.write_bytes(b"payload" * 200)

    renamed = tmp_path / "held.locked"

    with open_read_shared(target) as handle:
        handle.read(32)
        target.rename(renamed)

    assert renamed.is_file()
    assert not target.exists()


def test_calculate_file_entropy_returns_correct_values(tmp_path):
    uniform = tmp_path / "uniform.bin"
    uniform.write_bytes(bytes(range(256)) * 500)
    assert calculate_file_entropy(uniform) == pytest.approx(8.0)

    text = tmp_path / "text.txt"
    text.write_bytes(b"normal document content " * 200)
    assert 3.0 <= calculate_file_entropy(text) <= 3.6


def test_calculate_file_entropy_samples_only_the_first_bytes(tmp_path):
    """A large low-entropy file must be sampled, not read whole."""
    import time

    path = tmp_path / "big.bin"
    path.write_bytes(b"A" * (5 * 1024 * 1024))

    started = time.perf_counter()
    value = calculate_file_entropy(path)
    elapsed = time.perf_counter() - started

    assert value == pytest.approx(0.0)
    assert elapsed < 1.0


def test_calculate_file_entropy_rejects_non_positive_sample_size(tmp_path):
    path = tmp_path / "any.txt"
    path.write_bytes(b"data")

    with pytest.raises(ValueError):
        calculate_file_entropy(path, sample_size=0)

    with pytest.raises(ValueError):
        calculate_file_entropy(path, sample_size=-1)


def test_calculate_file_entropy_raises_for_a_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        calculate_file_entropy(tmp_path / "missing.txt")


def test_empty_and_single_byte_files_have_zero_entropy(tmp_path):
    empty = tmp_path / "empty.txt"
    empty.write_bytes(b"")
    assert calculate_file_entropy(empty) == 0.0

    single = tmp_path / "single.txt"
    single.write_bytes(b"a")
    assert calculate_file_entropy(single) == 0.0


# ---------------------------------------------------------------------------
# FR-03 end-to-end: entropy of a MODIFIED file drives the high_entropy signal
# ---------------------------------------------------------------------------


def test_entropy_of_a_modified_file_reaches_the_event_metadata(tmp_path):
    """FSD FR-3: entropy is calculated for modified files."""
    import os

    from rdrs.monitoring import calculate_file_metadata

    encrypted = tmp_path / "encrypted.bin"
    encrypted.write_bytes(os.urandom(64 * 1024))

    metadata = calculate_file_metadata(encrypted)

    assert metadata["entropy"] is not None
    assert metadata["entropy"] >= 7.9
    assert metadata["size"] == 64 * 1024
    assert metadata["sha256"]


def test_phase1_done_when_text_low_random_high(tmp_path):
    """Verbatim Phase 1 Done-when condition."""
    import os

    text = tmp_path / "normal.txt"
    text.write_text("normal document content " * 300, encoding="utf-8")

    random_file = tmp_path / "random.bin"
    random_file.write_bytes(os.urandom(64 * 1024))

    assert 0.0 <= calculate_file_entropy(text) <= 5.0
    assert calculate_file_entropy(random_file) >= 7.9


def test_high_entropy_signal_fires_only_above_the_threshold(tmp_path):
    """average_entropy >= 7.0 raises high_entropy; a quiet set does not."""
    from rdrs.burst_detection import BurstDetectionEngine

    loud = BurstDetectionEngine()

    for index in range(10):
        loud.ingest(
            {
                "action": "modified",
                "path": str(tmp_path / f"encrypted_{index}.bin"),
                "timestamp": loud._window_now(),
                "extension": ".bin",
                "metadata": {"entropy": 7.95, "exists": True, "is_file": True},
            }
        )

    analysis = loud.last_analysis

    assert analysis.metrics.average_entropy >= 7.0
    assert "high_entropy" in analysis.signals
    assert loud.thresholds["average_entropy"] == 7.0

    quiet = BurstDetectionEngine()

    for index in range(10):
        quiet.ingest(
            {
                "action": "modified",
                "path": str(tmp_path / f"document_{index}.txt"),
                "timestamp": quiet._window_now(),
                "extension": ".txt",
                "metadata": {"entropy": 3.2, "exists": True, "is_file": True},
            }
        )

    quiet_analysis = quiet.last_analysis

    assert quiet_analysis.metrics.average_entropy < 7.0
    assert "high_entropy" not in quiet_analysis.signals


def test_entropy_sample_size_is_64kb():
    from rdrs.entropy import MAX_SAMPLE_SIZE
    from rdrs.monitoring import MAX_ENTROPY_SAMPLE_SIZE

    assert MAX_SAMPLE_SIZE == 64 * 1024
    assert MAX_ENTROPY_SAMPLE_SIZE == 64 * 1024
