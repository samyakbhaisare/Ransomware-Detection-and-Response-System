from pathlib import Path

from rdrs.quarantine import QuarantineManager


def test_simulation_mode_copies_evidence_and_preserves_original(
    tmp_path: Path,
):
    source = tmp_path / "evidence.txt"
    source.write_text("real evidence", encoding="utf-8")

    quarantine_dir = tmp_path / "quarantine"

    manager = QuarantineManager(
        quarantine_dir,
        simulation_mode=True,
    )

    result = manager.quarantine(source)

    destination = Path(result.destination)

    assert result.simulation_mode is True
    assert result.copied is True
    assert source.exists()
    assert source.read_text(encoding="utf-8") == "real evidence"
    assert destination.exists()
    assert destination.read_text(encoding="utf-8") == "real evidence"


def test_real_quarantine_copies_and_preserves_original(
    tmp_path: Path,
):
    source = tmp_path / "evidence.txt"
    source.write_text("real evidence", encoding="utf-8")

    quarantine_dir = tmp_path / "quarantine"

    manager = QuarantineManager(
        quarantine_dir,
        simulation_mode=False,
    )

    result = manager.quarantine(source)

    destination = Path(result.destination)

    assert result.copied is True
    assert source.exists()
    assert destination.exists()
    assert destination.read_text(encoding="utf-8") == "real evidence"