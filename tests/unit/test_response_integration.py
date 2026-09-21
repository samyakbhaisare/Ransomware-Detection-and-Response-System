from pathlib import Path

from rdrs.quarantine import QuarantineManager
from rdrs.response import IncidentResponseEngine
from rdrs.scoring import ThreatLevel, ThreatScoringEngine
from rdrs.storage import DatabaseManager


def test_critical_response_end_to_end(tmp_path: Path):
    db = DatabaseManager(tmp_path / "rdrs.db")
    db.initialize()

    evidence_file = tmp_path / "evidence.txt"
    evidence_file.write_text(
        "real ransomware-test evidence",
        encoding="utf-8",
    )

    quarantine_dir = tmp_path / "quarantine"

    score = ThreatScoringEngine().calculate(
        rapid_encryption=True,
        mass_rename=True,
    )

    assert score.total == 70
    assert score.level == ThreatLevel.CRITICAL

    response = IncidentResponseEngine(
        db,
        simulation_mode=False,
        quarantine_dir=quarantine_dir,
    )

    result = response.process(
        score,
        affected_files=[str(evidence_file)],
        suspect_process={
            "pid": 12345,
            "name": "safe-test-process",
        },
        reason="Controlled ransomware response test",
    )

    assert result.level == ThreatLevel.CRITICAL
    assert result.score_id > 0
    assert result.alert_id is not None
    assert result.incident_id is not None
    assert len(result.quarantined_files) == 1

    destination = Path(result.quarantined_files[0])

    assert evidence_file.exists()
    assert destination.exists()
    assert destination.read_text(encoding="utf-8") == (
        "real ransomware-test evidence"
    )

    assert db.count_records("scores") == 1
    assert db.count_records("alerts") == 1
    assert db.count_records("incidents") == 1

    db.close()