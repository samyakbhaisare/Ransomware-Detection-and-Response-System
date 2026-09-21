"""Final gap-closure tests for the remaining uncovered branches (Task 7).

Every test here targets a branch that was still uncovered after the main
Task 7 modules: scoring-weight validation, quarantine skip semantics in
incident response (D-15), evidence-fallback paths in report building, file
type classification and the CLI error surface.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rdrs.api import build_incident_report
from rdrs.audit import AuditEngine
from rdrs.monitoring import FileTypeClassifier
from rdrs.response import IncidentResponseEngine
from rdrs.scoring import ThreatScore, ThreatScoringEngine
from rdrs.storage import DatabaseManager


# ---------------------------------------------------------------------------
# scoring weight validation
# ---------------------------------------------------------------------------


def test_scorer_rejects_unknown_weights():
    with pytest.raises(ValueError, match="Unknown scoring weights"):
        ThreatScoringEngine(weights={"not_a_signal": 10})


def test_scorer_rejects_negative_weights():
    with pytest.raises(ValueError, match="Weight must be non-negative"):
        ThreatScoringEngine(weights={"mass_rename": -1})


def test_scorer_accepts_and_applies_custom_weights():
    scorer = ThreatScoringEngine(weights={"rapid_encryption": 5})

    assert scorer.weights["rapid_encryption"] == 5

    result = scorer.calculate(rapid_encryption=True)

    assert result.total == 5


def test_scorer_accepts_string_weights_that_are_numeric():
    scorer = ThreatScoringEngine(weights={"mass_rename": "15"})

    assert scorer.weights["mass_rename"] == 15


def test_threat_score_levels_are_derived_from_the_total():
    assert ThreatScore().level.value == "NORMAL"
    assert ThreatScore(rapid_encryption=40).level.value == "WARNING"
    assert ThreatScore(rapid_encryption=40, mass_rename=30).level.value == "CRITICAL"

    # The total is capped at 100.
    assert (
        ThreatScore(
            rapid_encryption=90,
            mass_rename=90,
            high_entropy=90,
        ).total
        == 100
    )


# ---------------------------------------------------------------------------
# response: quarantine skip semantics (D-15)
# ---------------------------------------------------------------------------


def _response_env(tmp_path, *, simulation_mode=True):
    database = DatabaseManager(tmp_path / "resp" / "rdrs.db")
    database.initialize()

    engine = IncidentResponseEngine(
        database=database,
        simulation_mode=simulation_mode,
        quarantine_dir=tmp_path / "vault",
        audit_engine=AuditEngine(),
    )

    return engine, database


def _critical_score() -> ThreatScore:
    return ThreatScore(rapid_encryption=40, mass_rename=30)


def test_response_skips_missing_evidence_and_still_creates_the_incident(tmp_path):
    """A renamed/removed file must not abort incident response (D-15)."""
    engine, database = _response_env(tmp_path)

    try:
        present = tmp_path / "present.txt"
        present.write_text("evidence", encoding="utf-8")
        missing = tmp_path / "renamed-away.txt"

        result = engine.process(
            _critical_score(),
            affected_files=[str(present), str(missing)],
            suspect_process={"name": "enc.exe"},
            reason="critical burst",
        )

        assert result.incident_id is not None
        assert result.alert_id is not None

        incident = database.get_incident(result.incident_id)

        assert incident is not None
        assert incident["threat_level"] == "CRITICAL"
        assert incident["suspect_process"] == {"name": "enc.exe"}

        # The readable file was copied; the missing one was skipped.
        assert len(result.quarantined_files) == 1
        assert Path(result.quarantined_files[0]).name == present.name
        assert result.quarantined_files[0] != str(present)

        # The original evidence must be untouched (copy-only).
        assert present.read_text(encoding="utf-8") == "evidence"

        # Every affected file is still recorded on the incident.
        assert incident["affected_files"] == [str(present), str(missing)]

        # The audit trail records the incident and the skip.
        entries = [entry.to_dict() for entry in engine.audit.entries]

        incident_entries = [
            entry
            for entry in entries
            if entry.get("action") == "critical_threat_response"
        ]

        assert incident_entries

        details = incident_entries[0]["details"]

        assert "skipped_files" in details
        assert len(details["skipped_files"]) == 1
    finally:
        database.close()


def test_response_skips_a_directory_in_the_evidence_list(tmp_path):
    """A directory is rejected by quarantine and must be skipped, not fatal."""
    engine, database = _response_env(tmp_path)

    try:
        a_directory = tmp_path / "a-folder"
        a_directory.mkdir()

        result = engine.process(
            _critical_score(),
            affected_files=[str(a_directory)],
            reason="critical burst",
        )

        assert result.incident_id is not None
        assert result.quarantined_files == ()

        entries = [
            entry
            for entry in [entry.to_dict() for entry in engine.audit.entries]
            if entry.get("action") == "critical_threat_response"
        ]

        skipped = entries[0]["details"]["skipped_files"]

        assert len(skipped) == 1
        assert "is not a file" in skipped[0]["reason"]
    finally:
        database.close()


def test_response_records_an_os_error_as_skipped(tmp_path, monkeypatch):
    """An OS-level copy failure is contained, not raised."""
    engine, database = _response_env(tmp_path)

    try:
        target = tmp_path / "present.txt"
        target.write_text("evidence", encoding="utf-8")

        def _explode(source, destination):
            raise OSError("copy failed")

        monkeypatch.setattr("rdrs.quarantine.shutil.copy2", _explode)

        result = engine.process(
            _critical_score(),
            affected_files=[str(target)],
            reason="critical burst",
        )

        assert result.incident_id is not None
        assert result.quarantined_files == ()

        entries = [
            entry
            for entry in [entry.to_dict() for entry in engine.audit.entries]
            if entry.get("action") == "critical_threat_response"
        ]

        skipped = entries[0]["details"]["skipped_files"]

        assert len(skipped) == 1
        assert skipped[0]["reason"] == "OS error: copy failed"
    finally:
        database.close()


def test_response_warning_level_creates_an_alert_only(tmp_path):
    engine, database = _response_env(tmp_path)

    try:
        score = ThreatScore(rapid_encryption=40)

        result = engine.process(
            score,
            affected_files=["C:/sandbox/a.txt"],
            reason="warning burst",
        )

        assert score.level.value == "WARNING"
        assert result.alert_id is not None
        assert result.incident_id is None

        actions = {entry.get("action") for entry in [entry.to_dict() for entry in engine.audit.entries]}

        assert "warning_alert_created" in actions
    finally:
        database.close()


def test_response_normal_level_records_a_score_only(tmp_path):
    engine, database = _response_env(tmp_path)

    try:
        score = ThreatScore(cpu_spike=15)

        result = engine.process(score, affected_files=[], reason="minor")

        assert score.level.value == "NORMAL"
        assert result.alert_id is None
        assert result.incident_id is None

        actions = {entry.get("action") for entry in [entry.to_dict() for entry in engine.audit.entries]}

        assert "normal_score_recorded" in actions
    finally:
        database.close()


def test_response_normalizes_and_deduplicates_file_lists(tmp_path):
    engine, database = _response_env(tmp_path)

    try:
        target = tmp_path / "dup.txt"
        target.write_text("x", encoding="utf-8")

        result = engine.process(
            _critical_score(),
            affected_files=[str(target), str(target), "", str(target)],
            reason="duplicates",
        )

        incident = database.get_incident(result.incident_id)

        assert incident["affected_files"] == [str(target)]
        assert len(result.quarantined_files) == 1
    finally:
        database.close()


def test_response_in_non_simulation_mode_is_recorded_as_such(tmp_path):
    engine, database = _response_env(tmp_path, simulation_mode=False)

    try:
        result = engine.process(_critical_score(), reason="live mode")

        incident = database.get_incident(result.incident_id)

        assert incident["evidence"]["simulation_mode"] is False

        entries = [
            entry
            for entry in [entry.to_dict() for entry in engine.audit.entries]
            if entry.get("action") == "critical_threat_response"
        ]

        assert entries[0]["details"]["simulation_mode"] is False
    finally:
        database.close()


# ---------------------------------------------------------------------------
# report building: evidence fallback paths
# ---------------------------------------------------------------------------


def _report_database(tmp_path):
    database = DatabaseManager(tmp_path / "report" / "rdrs.db")
    database.initialize()
    return database


def test_report_falls_back_to_evidence_when_columns_are_blank(tmp_path):
    database = _report_database(tmp_path)

    try:
        incident_id = database.record_incident(
            severity="CRITICAL",
            description="legacy evidence-only row",
            evidence={
                "score": 82.5,
                "level": "CRITICAL",
                "affected_files": ["C:/a.txt", "C:/b.txt"],
                "suspect_process": {"name": "legacy.exe"},
                "simulation_mode": False,
            },
        )

        # Blank the dedicated columns so only the evidence blob remains.
        with database.engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE incidents SET threat_score = 0.0, threat_level = 'NORMAL', "
                "suspect_process_json = '{}', affected_files_json = '[]' "
                "WHERE id = ?",
                (incident_id,),
            )

        incident = database.get_incident(incident_id)

        # The dedicated columns were blanked, so the report must fall back
        # to the fields recorded in the evidence payload.
        assert incident["threat_score"] == pytest.approx(0.0)
        assert incident["threat_level"] == "NORMAL"

        report = build_incident_report(database, incident)

        assert report["threat_score"] == pytest.approx(82.5)
        assert report["threat_level"] == "CRITICAL"
        assert report["affected_files"] == ["C:/a.txt", "C:/b.txt"]
        assert report["suspect_process"] == {"name": "legacy.exe"}
        assert report["incident"]["score"] == pytest.approx(82.5)

        # A non-simulation incident must say so.
        joined = " ".join(report["recommendations"])

        assert "NOT in simulation mode" in joined
    finally:
        database.close()


def test_report_recovers_from_json_encoded_and_broken_affected_files(tmp_path):
    database = _report_database(tmp_path)

    try:
        encoded_id = database.record_incident(
            severity="CRITICAL",
            description="json encoded list",
            evidence={"score": 70.0},
        )
        broken_id = database.record_incident(
            severity="CRITICAL",
            description="broken list",
            evidence={"score": 70.0},
        )

        with database.engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE incidents SET affected_files_json = ?, "
                "threat_score = 0.0, threat_level = 'NORMAL' WHERE id = ?",
                (json.dumps(["C:/one.txt", "C:/two.txt"]), encoded_id),
            )
            connection.exec_driver_sql(
                "UPDATE incidents SET affected_files_json = ?, "
                "threat_score = 0.0, threat_level = 'NORMAL' WHERE id = ?",
                ("C:/not-json.txt", broken_id),
            )

        encoded = build_incident_report(
            database,
            database.get_incident(encoded_id),
        )

        assert encoded["affected_files"] == ["C:/one.txt", "C:/two.txt"]

        broken = build_incident_report(
            database,
            database.get_incident(broken_id),
        )

        assert broken["affected_files"] == ["C:/not-json.txt"]
    finally:
        database.close()


def test_report_without_a_score_or_level_defaults_to_normal(tmp_path):
    database = _report_database(tmp_path)

    try:
        incident_id = database.record_incident(
            severity="INFO",
            description="no score anywhere",
            evidence={},
        )

        with database.engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE incidents SET threat_score = 0.0, threat_level = 'NORMAL', "
                "suspect_process_json = '{}', affected_files_json = '[]', "
                "evidence_json = '{}' WHERE id = ?",
                (incident_id,),
            )

        report = build_incident_report(
            database,
            database.get_incident(incident_id),
        )

        assert report["threat_score"] == pytest.approx(0.0)
        assert report["threat_level"] == "Normal"
        assert report["affected_files"] == []
        assert report["suspect_process"] is None
        assert report["timeline"] == []
    finally:
        database.close()


def test_report_level_is_derived_from_a_score_when_absent(tmp_path):
    database = _report_database(tmp_path)

    try:
        warning_id = database.record_incident(
            severity="WARNING",
            description="score only, no level",
            evidence={},
        )

        with database.engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE incidents SET threat_score = 55.0, threat_level = 'NORMAL', "
                "evidence_json = '{}' WHERE id = ?",
                (warning_id,),
            )

        report = build_incident_report(
            database,
            database.get_incident(warning_id),
        )

        assert report["threat_level"] == "Warning"

        critical_id = database.record_incident(
            severity="CRITICAL",
            description="score only, critical",
            evidence={},
        )

        with database.engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE incidents SET threat_score = 91.0, threat_level = 'NORMAL', "
                "evidence_json = '{}' WHERE id = ?",
                (critical_id,),
            )

        critical = build_incident_report(
            database,
            database.get_incident(critical_id),
        )

        assert critical["threat_level"] == "Critical"
    finally:
        database.close()


def test_report_filters_alerts_and_scores_to_the_incident_references(tmp_path):
    database = _report_database(tmp_path)

    try:
        matching_alert = database.record_alert(
            severity="CRITICAL",
            message="the matching alert",
        )
        database.record_alert(severity="INFO", message="an unrelated alert")

        matching_score = database.record_score(
            score=70.0, level="Critical", reason="the matching score"
        )
        database.record_score(score=5.0, level="Normal", reason="unrelated")

        incident_id = database.record_incident(
            severity="CRITICAL",
            description="referenced rows",
            evidence={"score": 70.0, "level": "CRITICAL"},
            threat_score=70.0,
            threat_level="CRITICAL",
            score_id=matching_score,
            alert_id=matching_alert,
        )

        report = build_incident_report(
            database,
            database.get_incident(incident_id),
        )

        assert [row["id"] for row in report["alerts"]] == [matching_alert]
        assert [row["id"] for row in report["scores"]] == [matching_score]
    finally:
        database.close()


def test_report_recommends_cpu_and_unknown_program_actions(tmp_path):
    database = _report_database(tmp_path)

    try:
        incident_id = database.record_incident(
            severity="CRITICAL",
            description="cpu + unknown program",
            evidence={
                "score": 25.0,
                "level": "NORMAL",
                "signals": {"cpu_spike": True, "unknown_program": True},
                "simulation_mode": True,
            },
            threat_score=25.0,
            threat_level="NORMAL",
        )

        report = build_incident_report(
            database,
            database.get_incident(incident_id),
        )

        joined = " ".join(report["recommendations"])

        assert "abnormal CPU spike" in joined
        assert "unexpected program" in joined
        assert "25/100" in joined
    finally:
        database.close()


# ---------------------------------------------------------------------------
# file type classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("report.txt", "text"),
        ("app.log", "text"),
        ("data.json", "data"),
        ("config.yaml", "data"),
        ("script.py", "source"),
        ("page.html", "web"),
        ("notes.md", "document"),
        ("report.pdf", "document"),
        ("book.xlsx", "spreadsheet"),
        ("deck.pptx", "presentation"),
        ("photo.png", "image"),
        ("archive.zip", "archive"),
        ("setup.exe", "executable"),
        ("blob.bin", "binary"),
        ("invoice.docx.locked", "suspicious"),
        # Not in the extension map - resolved through the mime fallback.
        ("song.mp3", "audio"),
        ("clip.mp4", "video"),
        # No extension and no mime type.
        ("README", "unknown"),
    ],
)
def test_file_type_classification(filename, expected):
    assert FileTypeClassifier.classify(filename) == expected


def test_file_type_classification_handles_extensionless_paths(tmp_path):
    path = tmp_path / "no-extension"

    assert FileTypeClassifier.classify(path) in {
        "unknown",
        "application",
        "text",
    }


# ---------------------------------------------------------------------------
# CLI error surface
# ---------------------------------------------------------------------------


def test_cli_stop_returns_a_status(monkeypatch):
    from rdrs import cli as cli_module

    class FakeRuntime:
        def stop(self):
            return {"state": "STOPPED"}

        def start(self):
            return {"state": "RUNNING"}

    monkeypatch.setattr(cli_module, "RuntimeManager", lambda: FakeRuntime())

    assert cli_module.main(["stop"]) in {0, 1}


def test_cli_maps_runtime_errors_to_exit_code_one(monkeypatch, capsys):
    from rdrs import cli as cli_module

    class FailingRuntime:
        def start(self):
            raise RuntimeError("cannot start")

        def stop(self):
            raise RuntimeError("cannot stop")

    monkeypatch.setattr(cli_module, "RuntimeManager", lambda: FailingRuntime())

    assert cli_module.main(["start"]) == 1

    captured = capsys.readouterr()

    assert "ERROR" in captured.err
