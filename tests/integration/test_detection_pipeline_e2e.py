"""End-to-end integration tests for the RDRS detection pipeline.

These tests deliberately wire the *real* components together rather than
stubbing them: a real :class:`BurstDetectionEngine` is fed real file events,
its genuine :class:`BurstAnalysis` drives a real
:class:`~rdrs.response.IncidentResponseEngine` writing to a real SQLite
database, and the resulting incident is rendered into a Phase 8 report.

The point of this module is to catch integration defects that unit tests
cannot see - for example a score that is computed correctly but never
persisted, or an incident whose events are never attributed to it, which is
what made the original Phase 8 report timeline unstable.

All file activity happens inside ``tmp_path``. Nothing outside the sandbox is
read, written, renamed, or deleted, and no real process is ever signalled.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rdrs.api import build_incident_report
from rdrs.audit import AuditEngine
from rdrs.burst_detection import BurstDetectionEngine
from rdrs.response import IncidentResponseEngine
from rdrs.scoring import ThreatLevel, ThreatScore
from rdrs.storage import DatabaseManager


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _score(total: int) -> ThreatScore:
    """Build a real :class:`ThreatScore` whose ``total`` is ``total``.

    ``ThreatScore`` is immutable and exposes only signal weights, so a score
    is constructed by assigning the whole total to ``rapid_encryption``. This
    keeps the level derivation (Normal/Warning/Critical) genuinely exercised
    rather than monkeypatched.
    """
    assert 0 <= total <= 100

    return ThreatScore(rapid_encryption=total)


def _to_response_score(analysis) -> ThreatScore:
    """Translate a detector breakdown into the response engine's score type.

    ``BurstDetectionEngine`` emits a float ``ThreatScoreBreakdown`` while
    ``IncidentResponseEngine`` consumes the integer ``ThreatScore``. The
    runtime bridges the two by mapping each *fired* signal onto its own
    breakdown weight; this helper reproduces that bridge so the integration
    test exercises the genuine conversion rather than bypassing it.
    """
    breakdown = analysis.score

    def weight(signal: str) -> int:
        if signal not in analysis.signals:
            return 0

        return int(getattr(breakdown, signal, 0) or 0)

    return ThreatScore(
        rapid_encryption=weight("rapid_encryption"),
        mass_rename=weight("mass_rename"),
        high_entropy=weight("high_entropy"),
        cpu_spike=weight("cpu_spike"),
        unknown_program=weight("unknown_program"),
    )


def _database(tmp_path: Path) -> DatabaseManager:
    database = DatabaseManager(tmp_path / "integration" / "rdrs.db")
    database.initialize()
    return database


def _burst_engine(*, clock=None, window_seconds: float = 60.0):
    """Build a detector whose thresholds are reachable in a short test."""
    return BurstDetectionEngine(
        clock=clock,
        thresholds={
            "window_seconds": window_seconds,
            "modified_files_per_minute": 10.0,
            "renames": 4.0,
            "extension_changes": 2.0,
            "average_entropy": 7.0,
            "cpu_percent": 80.0,
        },
    )


class FakeClock:
    """A monotonic clock the test advances explicitly.

    The detector's sliding window is measured against ``clock()``, so events
    stamped with a fixed historical epoch would immediately fall outside the
    window. Driving the clock keeps the burst genuinely inside one window
    without weakening any threshold.
    """

    def __init__(self, start: float = 1_000_000.0) -> None:
        self._now = float(start)

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> float:
        self._now += float(seconds)
        return self._now


def _modify(
    path: Path,
    *,
    timestamp: float,
    entropy: float = 7.9,
) -> dict:
    return {
        "action": "modify",
        "path": str(path),
        "timestamp": timestamp,
        "metadata": {"entropy": entropy},
    }


def _rename(
    previous: Path,
    new: Path,
    *,
    timestamp: float,
) -> dict:
    return {
        "action": "rename",
        "path": str(new),
        "previous_path": str(previous),
        "new_path": str(new),
        "timestamp": timestamp,
        "metadata": {},
    }


# ---------------------------------------------------------------------------
# burst detection -> response -> report
# ---------------------------------------------------------------------------


def test_ransomware_burst_flows_through_to_a_critical_incident_and_report(
    tmp_path: Path,
):
    """The complete E2E path: burst detected -> incident -> report written.

    This is the headline integration guarantee. A burst of rapid modifications
    plus extension changes must (a) be scored Critical by the detector,
    (b) create a Critical incident through the response engine, and
    (c) produce a report whose score, level and affected files match the
    analysed burst - not placeholder values.
    """
    work = tmp_path / "sandbox"
    work.mkdir()

    database = _database(tmp_path)

    try:
        clock = FakeClock()
        engine = _burst_engine(clock=clock)

        # Ten distinct files rewritten by a burst, all inside the sandbox.
        targets = []

        for index in range(12):
            target = work / f"document_{index:02d}.txt"
            target.write_text(f"original content {index}", encoding="utf-8")
            targets.append(target)

            clock.advance(1.0)
            engine.add_event(_modify(target, timestamp=clock()))

        # Five files renamed to a ransomware extension in the same window,
        # which is enough to cross the ``renames`` threshold of 4.
        for index in range(5):
            previous = work / f"photo_{index}.jpg"
            previous.write_text("jpeg bytes", encoding="utf-8")
            new = work / f"photo_{index}.jpg.locked"
            new.write_text("encrypted", encoding="utf-8")

            clock.advance(1.0)
            engine.add_event(
                _rename(previous, new, timestamp=clock())
            )

        analysis = engine.evaluate()

        # --- the detector really did see a dangerous burst ------------------
        assert analysis.dangerous_burst is True
        assert analysis.score.total >= 70.0
        assert analysis.level == ThreatLevel.CRITICAL
        assert "rapid_encryption" in analysis.signals
        assert "mass_rename" in analysis.signals

        affected = sorted(str(path) for path in analysis.affected_files)
        assert len(affected) >= 12

        # --- the response engine created a real incident --------------------
        responder = IncidentResponseEngine(
            database,
            simulation_mode=True,
            quarantine_dir=tmp_path / "vault",
            audit_engine=AuditEngine(),
        )

        result = responder.process(
            _to_response_score(analysis),
            affected_files=affected,
            suspect_process={"name": "malware.exe", "pid": 4321},
            reason="integration burst",
        )

        assert result.level == ThreatLevel.CRITICAL
        assert result.incident_id is not None
        assert result.alert_id is not None

        # --- the incident carries the real score, not an evidence-only blob -
        incident = database.get_incident(result.incident_id)

        assert incident["threat_score"] == pytest.approx(
            analysis.score.total
        )
        assert incident["threat_level"] == "CRITICAL"
        assert incident["suspect_process"]["name"] == "malware.exe"
        assert len(incident["affected_files"]) == len(affected)

        # --- the generated report agrees with what was actually detected ----
        report = build_incident_report(database, incident)

        assert report["threat_score"] == pytest.approx(
            analysis.score.total
        )
        assert report["threat_level"] == "CRITICAL"
        assert report["affected_file_count"] == len(affected)
        assert report["suspect_process"]["name"] == "malware.exe"

        # The recommendations must be derived from the signals that fired.
        joined = " ".join(report["recommendations"]).lower()

        assert "rapid encryption" in joined
        assert "mass rename" in joined
        assert "high-entropy" in joined
        assert "simulation mode" in joined
    finally:
        database.close()


def test_moderate_burst_produces_a_warning_alert_but_no_incident(
    tmp_path: Path,
):
    """A Warning-scoring burst must alert without escalating to an incident."""
    work = tmp_path / "sandbox"
    work.mkdir()

    database = _database(tmp_path)

    try:
        responder = IncidentResponseEngine(
            database,
            simulation_mode=True,
            quarantine_dir=tmp_path / "vault",
        )

        warning = _score(int(55.0))
        assert warning.level == ThreatLevel.WARNING

        result = responder.process(
            warning,
            affected_files=[str(work / "a.txt")],
            reason="moderate burst",
        )

        assert result.alert_id is not None
        assert result.incident_id is None
        assert result.quarantined_files == ()

        assert [row["severity"] for row in database.list_alerts()] == [
            "WARNING"
        ]
        assert database.list_incidents() == []
    finally:
        database.close()


def test_normal_score_is_recorded_without_alerts_or_incidents(
    tmp_path: Path,
):
    """Baseline activity must not create noise in the alert stream."""
    database = _database(tmp_path)

    try:
        responder = IncidentResponseEngine(
            database,
            simulation_mode=True,
            quarantine_dir=tmp_path / "vault",
        )

        normal = _score(int(5.0))

        result = responder.process(normal, reason="baseline")

        assert result.alert_id is None
        assert result.incident_id is None
        assert database.list_alerts() == []
        assert database.list_incidents() == []
        assert len(database.list_scores()) == 1
    finally:
        database.close()


# ---------------------------------------------------------------------------
# event attribution - the Phase 8 report timeline guarantee
# ---------------------------------------------------------------------------


def test_burst_events_are_attributed_to_the_incident_and_scoped_in_the_report(
    tmp_path: Path,
):
    """Events that produced the burst must end up inside the report timeline.

    The events are persisted before the incident exists, so they carry no
    ``incident_id`` at write time. This test proves the attribution step
    links them, and that the report timeline therefore shows the real burst
    rather than an empty list.
    """
    work = tmp_path / "sandbox"
    work.mkdir()

    database = _database(tmp_path)

    try:
        clock = FakeClock()
        engine = _burst_engine(clock=clock)

        persisted_paths = []

        for index in range(12):
            target = work / f"ledger_{index:02d}.txt"
            target.write_text("account data", encoding="utf-8")
            persisted_paths.append(str(target))

            clock.advance(1.0)
            event = _modify(target, timestamp=clock())

            # Detection sees the event...
            engine.add_event(event)

            # ...and the monitor would have persisted it at the same moment.
            database.record_file_event(event)

        # Renames to a ransomware extension in the same window: without the
        # ``mass_rename`` signal the burst only reaches Warning, and a Warning
        # incident is never created (by design - see the WARNING test above).
        renamed_paths = []

        for index in range(5):
            previous = work / f"scan_{index}.pdf"
            previous.write_text("pdf bytes", encoding="utf-8")
            new = work / f"scan_{index}.pdf.locked"
            new.write_text("encrypted", encoding="utf-8")
            renamed_paths.append(str(new))

            clock.advance(1.0)
            event = _rename(previous, new, timestamp=clock())

            engine.add_event(event)
            database.record_file_event(event)

        analysed_paths = persisted_paths + renamed_paths

        analysis = engine.evaluate()
        assert analysis.dangerous_burst is True
        assert analysis.score.total >= 70.0
        assert analysis.level == ThreatLevel.CRITICAL

        responder = IncidentResponseEngine(
            database,
            simulation_mode=True,
            quarantine_dir=tmp_path / "vault",
        )

        result = responder.process(
            _to_response_score(analysis),
            affected_files=sorted(analysed_paths),
            reason="attribution test",
        )

        assert result.incident_id is not None

        # Nothing was attributed yet - the runtime does this after the fact.
        assert database.list_events_for_incident(result.incident_id) == []

        attributed = database.attach_events_to_incident_by_paths(
            result.incident_id,
            analysed_paths,
        )

        assert attributed == len(analysed_paths)

        incident = database.get_incident(result.incident_id)
        report = build_incident_report(database, incident)

        timeline_paths = {entry["path"] for entry in report["timeline"]}
        timeline_actions = {
            entry["action"] for entry in report["timeline"]
        }

        # The whole burst - modifications and renames - is inside the report.
        assert timeline_paths == set(analysed_paths)
        assert timeline_actions == {"modify", "rename"}

        # A freshly-persisted incident must not need the evidence fallback:
        # its dedicated columns already carry the real score and level.
        assert report["threat_score"] == pytest.approx(
            analysis.score.total
        )
        assert report["threat_level"] == "CRITICAL"
    finally:
        database.close()


def test_report_timeline_stays_scoped_to_one_incident(tmp_path: Path):
    """Two incidents must never leak events into each other's report."""
    work = tmp_path / "sandbox"
    work.mkdir()

    database = _database(tmp_path)

    try:
        responder = IncidentResponseEngine(
            database,
            simulation_mode=True,
            quarantine_dir=tmp_path / "vault",
        )

        first_paths = []
        second_paths = []

        for index in range(4):
            first = work / f"first_{index}.txt"
            first.write_text("one", encoding="utf-8")
            first_paths.append(str(first))

            database.record_file_event(
                _modify(first, timestamp=3000.0 + index)
            )

        for index in range(4):
            second = work / f"second_{index}.txt"
            second.write_text("two", encoding="utf-8")
            second_paths.append(str(second))

            database.record_file_event(
                _modify(second, timestamp=4000.0 + index)
            )

        first_incident = responder.process(
            _score(int(95.0)),
            affected_files=sorted(first_paths),
            reason="first incident",
        )

        # Give the second incident a strictly later JSON timestamp boundary.
        second_incident = responder.process(
            _score(int(85.0)),
            affected_files=sorted(second_paths),
            reason="second incident",
        )

        database.attach_events_to_incident_by_paths(
            first_incident.incident_id, first_paths
        )
        database.attach_events_to_incident_by_paths(
            second_incident.incident_id, second_paths
        )

        first_report = build_incident_report(
            database, database.get_incident(first_incident.incident_id)
        )
        second_report = build_incident_report(
            database, database.get_incident(second_incident.incident_id)
        )

        first_timeline = {e["path"] for e in first_report["timeline"]}
        second_timeline = {e["path"] for e in second_report["timeline"]}

        assert first_timeline == set(first_paths)
        assert second_timeline == set(second_paths)
        assert first_timeline.isdisjoint(second_timeline)
    finally:
        database.close()


# ---------------------------------------------------------------------------
# quarantine / evidence handling
# ---------------------------------------------------------------------------


def test_critical_response_quarantines_a_copy_and_keeps_the_original(
    tmp_path: Path,
):
    """Quarantine is a *copy*: the original evidence must remain untouched."""
    work = tmp_path / "sandbox"
    work.mkdir()

    database = _database(tmp_path)
    vault = tmp_path / "vault"

    try:
        responder = IncidentResponseEngine(
            database,
            simulation_mode=True,
            quarantine_dir=vault,
        )

        evidence_file = work / "evidence.bin"
        payload = b"original evidence bytes" * 8
        evidence_file.write_bytes(payload)

        result = responder.process(
            _score(int(91.0)),
            affected_files=[str(evidence_file)],
            reason="evidence test",
        )

        assert len(result.quarantined_files) == 1

        copy = Path(result.quarantined_files[0])

        # The copy exists in the vault and matches byte for byte.
        assert copy.exists()
        assert copy.read_bytes() == payload
        assert copy.parent == vault

        # ...and the original was never moved, renamed or modified.
        assert evidence_file.exists()
        assert evidence_file.read_bytes() == payload
    finally:
        database.close()


def test_missing_evidence_does_not_abort_the_incident(tmp_path: Path):
    """A file already encrypted/removed by the burst must not break response."""
    work = tmp_path / "sandbox"
    work.mkdir()

    database = _database(tmp_path)

    try:
        responder = IncidentResponseEngine(
            database,
            simulation_mode=True,
            quarantine_dir=tmp_path / "vault",
        )

        present = work / "still_here.txt"
        present.write_text("survived", encoding="utf-8")

        missing = work / "already_encrypted.txt"

        result = responder.process(
            _score(int(88.0)),
            affected_files=[str(present), str(missing)],
            reason="partial evidence",
        )

        # The incident still stands.
        assert result.incident_id is not None
        assert len(result.quarantined_files) == 1

        entries = [
            entry.to_dict() for entry in responder.audit.entries
        ]
        details = entries[0]["details"]

        assert details["skipped_files"][0]["path"] == str(missing)

        incident = database.get_incident(result.incident_id)
        assert len(incident["affected_files"]) == 2
    finally:
        database.close()


def test_integration_report_is_persisted_as_valid_json_and_csv(
    tmp_path: Path,
):
    """The report writers must emit parseable JSON and a real CSV."""
    from rdrs.api import write_csv_report, write_json_report

    database = _database(tmp_path)

    try:
        responder = IncidentResponseEngine(
            database,
            simulation_mode=True,
            quarantine_dir=tmp_path / "vault",
        )

        result = responder.process(
            _score(int(93.0)),
            affected_files=[],
            reason="writer test",
        )

        report = build_incident_report(
            database, database.get_incident(result.incident_id)
        )

        json_path = tmp_path / "out" / "report.json"
        csv_path = tmp_path / "out" / "report.csv"

        # The API creates the reports directory before writing (see
        # ``report_directory``); the writer itself expects a real parent.
        json_path.parent.mkdir(parents=True, exist_ok=True)

        write_json_report(report, json_path)
        write_csv_report(report, csv_path)

        decoded = json.loads(json_path.read_text(encoding="utf-8"))

        assert decoded["threat_score"] == pytest.approx(93.0)
        assert decoded["threat_level"] == "CRITICAL"

        csv_text = csv_path.read_text(encoding="utf-8")
        assert csv_text.strip()
        # The CSV header is the report's flattened record schema.
        assert csv_text.splitlines()[0].startswith("record_type,")
        assert "incident" in csv_text
        assert "93.0" in csv_text
    finally:
        database.close()


# ---------------------------------------------------------------------------
# database lifecycle
# ---------------------------------------------------------------------------


def test_database_survives_close_and_reopen_with_data_intact(tmp_path: Path):
    """Restarting the service must not lose the incident record."""
    db_path = tmp_path / "persist" / "rdrs.db"

    first = DatabaseManager(db_path)
    first.initialize()

    responder = IncidentResponseEngine(
        first,
        simulation_mode=True,
        quarantine_dir=tmp_path / "vault",
    )

    result = responder.process(
        _score(int(99.0)),
        affected_files=[],
        reason="persistence test",
    )

    incident_id = result.incident_id
    first.close()

    second = DatabaseManager(db_path)
    second.initialize()

    try:
        restored = second.get_incident(incident_id)

        assert restored is not None
        assert restored["threat_score"] == pytest.approx(99.0)
        assert restored["threat_level"] == "CRITICAL"
        assert restored["description"] == "persistence test"
    finally:
        second.close()
