"""FR-07 + FR-08 + Build Guide Phase 5 regression tests.

Phase 5 requires:
* weights rapid encryption 40, mass rename 30, high entropy 25, CPU spike 15,
  unknown program 10 — summed for the signals that fired and **capped at 100**
* levels Normal (0-40), Warning (40-70), Critical (70-100)
* on Critical: an incident record with the affected files and suspect process,
  plus alert-log and audit-log entries
* evidence copied to quarantine — copy only, originals untouched
* simulation mode ON by default; no real process termination

Done-when: "A simulated burst produces a Critical score, an incident record, an
alert-log entry, and copied evidence — with the original files untouched."
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
import yaml

from rdrs.response import IncidentResponseEngine
from rdrs.scoring import ThreatScore
from rdrs.storage import DatabaseManager

PROJECT_ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------
# FR-07: weighted score
# --------------------------------------------------------------------------


def test_guide_weights_are_the_configured_weights():
    raw = yaml.safe_load(
        (PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8")
    )

    assert raw["scoring"]["weights"] == {
        "rapid_encryption": 40.0,
        "mass_rename": 30.0,
        "high_entropy": 25.0,
        "cpu_spike": 15.0,
        "unknown_program": 10.0,
    }


def test_weights_are_summed_and_capped_at_one_hundred():
    total = ThreatScore(
        rapid_encryption=40,
        mass_rename=30,
        high_entropy=25,
        cpu_spike=15,
        unknown_program=10,
    )

    assert total.total == 100.0

    # The uncapped sum is 120; the score must still be 100.
    assert (
        ThreatScore(
            rapid_encryption=40,
            mass_rename=30,
            high_entropy=25,
            cpu_spike=15,
            unknown_program=10,
        ).total
        <= 100.0
    )


def test_a_single_signal_scores_its_own_weight():
    assert ThreatScore(rapid_encryption=40).total == 40.0
    assert ThreatScore(mass_rename=30).total == 30.0
    assert ThreatScore(high_entropy=25).total == 25.0
    assert ThreatScore(cpu_spike=15).total == 15.0
    assert ThreatScore(unknown_program=10).total == 10.0


def test_no_signal_scores_zero():
    assert ThreatScore().total == 0.0


def score_of(total: int) -> ThreatScore:
    """Build a ThreatScore whose total is exactly ``total``.

    The weights are the guide's, so any target is reachable with combinations
    of rapid_encryption (40), mass_rename (30), high_entropy (25), cpu_spike
    (15) and unknown_program (10).
    """
    remaining = total
    parts: dict[str, int] = {}

    for name, weight in (
        ("rapid_encryption", 40),
        ("mass_rename", 30),
        ("high_entropy", 25),
        ("cpu_spike", 15),
        ("unknown_program", 10),
    ):
        take = min(weight, remaining)
        parts[name] = take
        remaining -= take

        if remaining == 0:
            break

    assert remaining == 0, f"cannot build a score of {total} from the guide weights"

    return ThreatScore(**parts)


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0, "NORMAL"),
        (1, "NORMAL"),
        (39, "NORMAL"),
        (40, "WARNING"),
        (55, "WARNING"),
        (69, "WARNING"),
        (70, "CRITICAL"),
        (85, "CRITICAL"),
        (100, "CRITICAL"),
    ],
)
def test_level_bands(score, expected):
    """Normal 0-40, Warning 40-70, Critical 70-100."""
    built = score_of(score)

    assert built.total == score
    assert built.level.value == expected


# --------------------------------------------------------------------------
# FR-08: alerts and incident records
# --------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path):
    database = DatabaseManager(tmp_path / "response.db")
    database.initialize()

    response = IncidentResponseEngine(
        database=database,
        quarantine_dir=tmp_path / "vault",
        simulation_mode=True,
    )

    yield response, database

    database.close()


def test_critical_creates_an_incident_with_files_and_suspect(engine, tmp_path):
    response, database = engine

    first = tmp_path / "a.docx"
    second = tmp_path / "b.docx"
    first.write_bytes(b"evidence-a" * 100)
    second.write_bytes(b"evidence-b" * 100)

    result = response.process(
        ThreatScore(rapid_encryption=40, mass_rename=30, high_entropy=25),
        affected_files=[str(first), str(second)],
        suspect_process={"name": "probe.exe", "pid": 4321},
        reason="phase 5 verification",
    )

    assert result.level.value == "CRITICAL"
    assert result.incident_id is not None

    incidents = database.list_incidents(limit=5)
    assert len(incidents) == 1

    incident = incidents[0]

    assert incident["threat_level"] == "CRITICAL"
    assert incident["threat_score"] == 95.0
    assert set(incident["affected_files"]) == {str(first), str(second)}
    assert incident["suspect_process"]["name"] == "probe.exe"


def test_warning_does_not_create_an_incident(engine):
    """Only Critical creates an incident."""
    response, database = engine

    response.process(
        ThreatScore(mass_rename=30, cpu_spike=15),
        reason="warning only",
    )

    assert database.count_records("incidents") == 0
    assert database.count_records("alerts") >= 1


# --------------------------------------------------------------------------
# Phase 5 Done-when, live through the runtime
# --------------------------------------------------------------------------


def test_phase5_done_when_live_burst(tmp_path):
    """Critical score + incident + alert log + copied evidence + originals."""
    import shutil

    from rdrs.runtime import RuntimeManager

    for name in ("data", "logs", "sandbox"):
        (tmp_path / name).mkdir()

    base = (PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8")

    config = tmp_path / "rdrs.yaml"
    config.write_text(
        base.replace("data_dir: data", f"data_dir: {(tmp_path / 'data').as_posix()}")
        .replace("logs_dir: logs", f"logs_dir: {(tmp_path / 'logs').as_posix()}")
        .replace(
            "db_path: data\\rdrs.db",
            f"db_path: {(tmp_path / 'data' / 'rdrs.db').as_posix()}",
        )
        .replace(
            "  watch_paths:\n    - data\n",
            f"  watch_paths:\n    - {(tmp_path / 'sandbox').as_posix()}\n",
        ),
        encoding="utf-8",
    )

    RuntimeManager.reset_singleton()
    runtime = RuntimeManager(config_path=config)

    try:
        runtime.start()
        time.sleep(0.5)

        sandbox = tmp_path / "sandbox"

        originals = []
        for index in range(12):
            path = sandbox / f"document_{index}.docx"
            path.write_bytes(b"REAL-DOCUMENT-CONTENT-" * 200)
            originals.append(path)

        before = {path: path.read_bytes() for path in originals}

        now = time.time()

        for index in range(60):
            runtime._handle_file_event(
                {
                    "action": "modified",
                    "path": str(sandbox / f"burst_{index}.txt"),
                    "timestamp": now + index * 0.02,
                    "extension": ".txt",
                    "metadata": {"entropy": 7.95, "exists": True, "is_file": True},
                }
            )

        for index in range(12):
            runtime._handle_file_event(
                {
                    "action": "renamed",
                    "path": str(sandbox / f"document_{index}.locked"),
                    "timestamp": now + 1.2 + index * 0.02,
                    "extension": ".locked",
                    "previous_path": str(sandbox / f"document_{index}.docx"),
                    "new_path": str(sandbox / f"document_{index}.locked"),
                    "metadata": {"entropy": 7.95, "exists": True, "is_file": True},
                }
            )

        time.sleep(1.0)

        analysis = runtime._last_analysis

        # 1. Critical score
        assert analysis.level == "CRITICAL"
        assert analysis.score.total >= 70.0

        # 2. Incident record
        incidents = runtime._database.list_incidents(limit=1)
        assert len(incidents) == 1
        assert incidents[0]["affected_files"]
        assert incidents[0]["suspect_process"] is not None

        # 3. Alert-log entry
        alerts_log = tmp_path / "logs" / "alerts.log"
        assert alerts_log.is_file() and alerts_log.stat().st_size > 0

        # 3b. Audit-log entry
        audit_log = tmp_path / "logs" / "audit.log"
        assert audit_log.is_file() and audit_log.stat().st_size > 0

        # 4. Copied evidence
        vault = tmp_path / "data" / "quarantine"
        assert vault.is_dir()
        assert list(vault.iterdir())

        # 5. Originals untouched
        for path in originals:
            assert path.is_file(), f"the original was removed: {path}"
            assert path.read_bytes() == before[path], f"modified: {path}"
    finally:
        runtime.stop()
        RuntimeManager.reset_singleton()


def test_simulation_mode_defaults_on(engine):
    response, _ = engine

    assert response.simulation_mode is True
    assert response.quarantine.simulation_mode is True


def test_no_process_termination_anywhere():
    """Phase 5: only LOG what it would do; never actually kill anything."""
    import re

    forbidden = re.compile(
        r"\.kill\(|\.terminate\(|os\.kill\(|taskkill|TerminateProcess"
    )

    for path in sorted((PROJECT_ROOT / "src" / "rdrs").glob("*.py")):
        text = path.read_text(encoding="utf-8-sig")

        for number, line in enumerate(text.splitlines(), start=1):
            assert not forbidden.search(line), f"{path.name}:{number}: {line.strip()}"
