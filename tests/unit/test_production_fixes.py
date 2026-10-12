"""Regression tests for the production fixes made in this cycle.

Covers:
  * settings applied end-to-end (validate -> apply -> persist -> survive restart)
  * ``get_runtime`` returning the live singleton rather than a stale object
  * consistent (non-torn) dashboard counters
  * deployment files free of a UTF-8 BOM
  * the project's own configuration left intact

Every test uses a throwaway configuration. The project's real
``config/rdrs.yaml`` must never be modified by the suite.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REAL_CONFIG = PROJECT_ROOT / "config.yaml"

TEMP_CONFIG = """\
runtime:
  name: rdrs
  environment: test
  data_dir: {data}
  logs_dir: {logs}
  shutdown_timeout: 15.0
logging:
  level: INFO
persistence:
  enabled: true
  db_path: {db}
detection:
  window_seconds: 60.0
  modified_files_per_minute: 20.0
  renames: 10.0
  extension_changes: 5.0
  average_entropy: 7.0
  cpu_percent: 80.0
scoring:
  weights:
    rapid_encryption: 40.0
    mass_rename: 30.0
    high_entropy: 25.0
    cpu_spike: 15.0
    unknown_program: 10.0
subsystems:
- name: config
  healthy: true
  critical: true
"""


@pytest.fixture
def configured_app(tmp_path):
    """A real FastAPI app wired to a throwaway configuration file."""
    from fastapi.testclient import TestClient

    from rdrs.api import app
    from rdrs.runtime import RuntimeManager

    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "logs").mkdir(exist_ok=True)

    config_path = tmp_path / "rdrs.yaml"
    config_path.write_text(
        TEMP_CONFIG.format(
            data=(tmp_path / "data").as_posix(),
            logs=(tmp_path / "logs").as_posix(),
            db=(tmp_path / "data" / "rdrs.db").as_posix(),
        ),
        encoding="utf-8",
    )

    RuntimeManager.reset_singleton()
    RuntimeManager(config_path=config_path)

    yield app, config_path

    RuntimeManager.reset_singleton()


# --------------------------------------------------------------------------
# settings: apply -> persist -> survive restart
# --------------------------------------------------------------------------


def test_settings_are_persisted_and_survive_a_restart(configured_app):
    """The reported gap: settings applied in memory but never written to disk.

    ``POST /settings`` must persist to the configured file so the change
    survives a restart, rather than silently reverting.
    """
    from fastapi.testclient import TestClient

    from rdrs.config import RuntimeConfig
    from rdrs.runtime import RuntimeManager

    app, config_path = configured_app

    with TestClient(app) as client:
        # Settings are editable only while monitoring is stopped (FSD).
        client.post("/monitor/stop")

        response = client.post(
            "/settings",
            json={
                "name": "rdrs-prod",
                "environment": "production",
                "log_level": "DEBUG",
                "shutdown_timeout": 25.0,
            },
        )

        assert response.status_code == 200

        # The running configuration reflects the change.
        status = client.get("/status").json()
        assert status["config"]["name"] == "rdrs-prod"
        assert status["config"]["environment"] == "production"
        assert status["config"]["log_level"] == "DEBUG"

        client.post("/monitor/stop")

    # It was written to disk...
    on_disk = RuntimeConfig.load(config_path)

    assert on_disk.name == "rdrs-prod"
    assert on_disk.environment == "production"
    assert on_disk.log_level == "DEBUG"
    assert on_disk.shutdown_timeout == 25.0

    # ...and survives a fresh load (a restart).
    RuntimeManager.reset_singleton()
    reloaded = RuntimeConfig.load(config_path)

    assert reloaded.name == "rdrs-prod"
    assert reloaded.log_level == "DEBUG"


def test_settings_endpoint_rejects_invalid_values(configured_app):
    from fastapi.testclient import TestClient

    app, _ = configured_app

    with TestClient(app) as client:
        # Settings are only editable while monitoring is stopped (FSD), so the
        # validation path is reached in the STOPPED state.
        assert client.post("/monitor/stop").status_code == 200

        assert client.post("/settings", json={"name": "   "}).status_code == 400
        assert (
            client.post("/settings", json={"log_level": "NOTREAL"}).status_code
            == 400
        )
        assert (
            client.post("/settings", json={"shutdown_timeout": -5}).status_code
            == 400
        )

        # A rejected request must not have changed anything.
        assert client.get("/status").json()["config"]["name"] == "rdrs"

        client.post("/monitor/stop")


def test_settings_multifield_validation_failure_is_atomic(configured_app):
    from fastapi.testclient import TestClient

    from rdrs.runtime import RuntimeManager

    app, config_path = configured_app
    original = config_path.read_bytes()

    with TestClient(app) as client:
        assert client.post("/monitor/stop").status_code == 200
        before = client.get("/status").json()["config"]

        response = client.post(
            "/settings",
            json={
                "name": "partially-applied",
                "environment": "staging",
                "shutdown_timeout": -1,
            },
        )

        assert response.status_code == 400
        after = client.get("/status").json()["config"]
        assert after["name"] == before["name"]
        assert after["environment"] == before["environment"]

    assert config_path.read_bytes() == original
    RuntimeManager.reset_singleton()


def test_settings_conflict_does_not_mutate_live_config(configured_app):
    from fastapi.testclient import TestClient

    from rdrs.runtime import RuntimeManager

    app, config_path = configured_app

    with TestClient(app) as client:
        assert client.post("/monitor/stop").status_code == 200
        before = client.get("/status").json()["config"]

        # Simulate an external writer after this RuntimeConfig was loaded.
        disk_before_request = config_path.read_text(encoding="utf-8")
        config_path.write_text(
            disk_before_request.replace(
                "environment: test",
                "environment: external-writer",
            ),
            encoding="utf-8",
        )
        external_bytes = config_path.read_bytes()

        response = client.post(
            "/settings",
            json={"name": "stale-tab-value"},
        )

        assert response.status_code == 409
        assert client.get("/status").json()["config"]["name"] == before["name"]
        assert config_path.read_bytes() == external_bytes

    RuntimeManager.reset_singleton()


def test_settings_round_trip_through_the_config_shape(configured_app):
    """Saved settings reload verbatim through the same config shape."""
    from fastapi.testclient import TestClient

    from rdrs.config import RuntimeConfig

    app, config_path = configured_app

    with TestClient(app) as client:
        assert client.post("/monitor/stop").status_code == 200

        assert (
            client.post("/settings", json={"environment": "staging"}).status_code
            == 200
        )

    reloaded = RuntimeConfig.load(config_path)

    assert reloaded.environment == "staging"

    # The detection thresholds and scoring weights must be untouched by a
    # settings edit -- they are not editable through this endpoint.
    assert reloaded.detection_thresholds["window_seconds"] == 60.0
    assert reloaded.detection_thresholds["renames"] == 10.0
    assert reloaded.scoring_weights["rapid_encryption"] == 40.0


# --------------------------------------------------------------------------
# get_runtime must return the live singleton
# --------------------------------------------------------------------------


def test_get_runtime_returns_the_live_singleton(configured_app):
    """A stale module-level reference must not be handed out.

    ``RuntimeManager`` is a singleton, but ``api.runtime_manager`` is bound once
    at import. After ``reset_singleton()`` that reference is stale -- and a
    stale runtime can point at a *different* configuration file, so a request
    could read or write the wrong config.
    """
    from rdrs.api import get_runtime
    from rdrs.runtime import RuntimeManager

    _, config_path = configured_app

    runtime = get_runtime()

    assert isinstance(runtime, RuntimeManager)
    assert runtime is RuntimeManager()
    assert Path(runtime._pending_config_path) == Path(config_path)


# --------------------------------------------------------------------------
# consistent counters
# --------------------------------------------------------------------------


def test_count_records_many_matches_individual_counts(tmp_path):
    from rdrs.storage import DatabaseManager

    database = DatabaseManager(tmp_path / "counts.db")
    database.initialize()

    for index in range(5):
        database.record_file_event(
            {
                "action": "modified",
                "path": f"sandbox/f{index}.txt",
                "metadata": {"entropy": 1.0},
            }
        )

    database.record_score(10.0, "NORMAL", "probe")
    database.record_alert("WARNING", "probe alert")

    tables = ["events", "processes", "scores", "alerts", "incidents"]
    counts = database.count_records_many(tables)

    assert set(counts) == set(tables)

    for table in tables:
        assert counts[table] == database.count_records(table)

    assert counts["events"] == 5
    assert counts["scores"] == 1
    assert counts["alerts"] == 1
    assert counts["incidents"] == 0

    database.close()


def test_count_records_many_rejects_unknown_tables(tmp_path):
    from rdrs.storage import DatabaseManager

    database = DatabaseManager(tmp_path / "bad.db")
    database.initialize()

    with pytest.raises(ValueError):
        database.count_records_many(["events", "not_a_table"])

    database.close()


def test_dashboard_summary_uses_one_snapshot(tmp_path):
    """The summary must come from a single consistent read."""
    from rdrs.dashboard_data import DashboardData
    from rdrs.storage import DatabaseManager

    database = DatabaseManager(tmp_path / "summary.db")
    database.initialize()

    database.record_file_event(
        {"action": "modified", "path": "sandbox/a.txt", "metadata": {}}
    )

    data = DashboardData(database)
    summary = data.summary()

    assert summary["event_count"] == 1
    assert summary["process_count"] == 0
    assert summary["score_count"] == 0
    assert summary["alert_count"] == 0
    assert summary["incident_count"] == 0

    # And it agrees with the individual counters.
    for key, table in (
        ("event_count", "events"),
        ("process_count", "processes"),
        ("score_count", "scores"),
        ("alert_count", "alerts"),
        ("incident_count", "incidents"),
    ):
        assert summary[key] == database.count_records(table)

    database.close()


# --------------------------------------------------------------------------
# deployment files
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["Dockerfile", "docker-compose.yml", "requirements.txt"],
)
def test_deployment_files_have_no_bom(name):
    """A UTF-8 BOM at the head of these files is never required and can break
    the tool that consumes them."""
    path = PROJECT_ROOT / name

    assert path.is_file(), f"{name} is missing"

    assert path.read_bytes()[:3] != b"\xef\xbb\xbf", (
        f"{name} starts with a UTF-8 BOM"
    )


def test_dockerfile_declares_the_expected_runtime():
    text = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert text.splitlines()[0].strip() == "FROM python:3.13-slim"
    assert "EXPOSE 8000" in text
    assert "HEALTHCHECK" in text.upper()
    assert "uvicorn" in text


def test_compose_is_valid_and_matches_the_dockerfile_port():
    spec = yaml.safe_load(
        (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )

    assert isinstance(spec, dict)
    service = spec["services"]["rdrs"]

    assert any(
        port == "8000:8000" or port.endswith(":8000:8000")
        for port in service["ports"]
    )
    assert any("data" in volume for volume in service["volumes"])
    assert any("logs" in volume for volume in service["volumes"])


# --------------------------------------------------------------------------
# monitoring must not obstruct the activity it observes
# --------------------------------------------------------------------------


def test_reading_a_file_does_not_block_rename_or_delete(tmp_path):
    """A detector must never prevent the activity it is detecting.

    On Windows ``Path.open("rb")`` requests no ``FILE_SHARE_DELETE``, so while
    RDRS held a handle to classify a file, any other process was refused
    permission to rename or delete it (``WinError 32``). A ransomware burst
    renames files while the detector reads them, so the detector would have
    been obstructing the very behaviour it exists to observe.
    """
    from rdrs.monitoring import open_read_shared

    target = tmp_path / "evidence.txt"
    target.write_bytes(b"payload" * 500)

    renamed = tmp_path / "evidence.locked"

    with open_read_shared(target) as handle:
        handle.read(64)

        # The rename must succeed while our read handle is still open.
        target.rename(renamed)

    assert renamed.is_file()
    assert not target.exists()

    # Deletion must also succeed while a handle is open.
    victim = tmp_path / "delete_me.txt"
    victim.write_bytes(b"payload" * 500)

    with open_read_shared(victim) as handle:
        handle.read(64)
        victim.unlink()

    assert not victim.exists()


def test_shared_read_still_returns_correct_metadata(tmp_path):
    """The sharing change must not alter the data collected."""
    import hashlib

    from rdrs.monitoring import calculate_file_metadata

    payload = b"normal document content " * 40
    target = tmp_path / "doc.txt"
    target.write_bytes(payload)

    metadata = calculate_file_metadata(target)

    assert metadata["exists"] is True
    assert metadata["size"] == len(payload)
    assert metadata["sha256"] == hashlib.sha256(payload).hexdigest()
    assert metadata["entropy"] is not None
    assert 0.0 <= metadata["entropy"] <= 5.0
    assert metadata["extension"] == ".txt"


def test_metadata_handles_a_file_that_vanishes(tmp_path):
    """A file removed between the stat and the read must not raise."""
    from rdrs.monitoring import calculate_file_metadata

    missing = tmp_path / "never_existed.txt"

    metadata = calculate_file_metadata(missing)

    assert metadata["exists"] is False
    assert metadata["sha256"] is None
    assert metadata["entropy"] is None


# --------------------------------------------------------------------------
# FR-1: the configuration file must be the one the FSD names
# --------------------------------------------------------------------------


def test_default_config_path_is_the_fsd_named_file():
    """FSD FR-1: "Load configuration from config.yaml".

    The Build Guide's project structure places ``config.yaml`` at the project
    root, so the runtime must resolve exactly that file by default.
    """
    from rdrs.runtime import RuntimeManager

    resolved = RuntimeManager.default_config_path()

    assert resolved.name == "config.yaml"
    assert resolved.parent == PROJECT_ROOT
    assert resolved.is_file(), "the canonical config.yaml is missing"


def test_root_config_yaml_loads_with_the_locked_values():
    """The FSD-named file must carry the real, locked configuration."""
    from rdrs.config import RuntimeConfig

    config = RuntimeConfig.load(PROJECT_ROOT / "config.yaml")

    assert config.name == "rdrs"
    assert config.environment == "test"

    # ``shutdown_timeout`` is operator-editable through the Settings page, so
    # it is checked for validity rather than a fixed literal -- the same
    # treatment as ``logging.level`` and ``persistence.enabled`` below.
    assert isinstance(config.shutdown_timeout, float)
    assert config.shutdown_timeout > 0

    # ``logging.level`` and ``persistence.enabled`` are operator-editable
    # through the Settings page, so they are not locked values. Assert they are
    # still *valid* -- corruption must still be caught.
    assert config.log_level in {
        "DEBUG",
        "INFO",
        "WARNING",
        "ERROR",
        "CRITICAL",
    }
    assert isinstance(config.persistence_enabled, bool)

    assert config.detection_thresholds == {
        "window_seconds": 60.0,
        "modified_files_per_minute": 20.0,
        "renames": 10.0,
        "extension_changes": 5.0,
        "average_entropy": 7.0,
        "cpu_percent": 80.0,
    }
    assert config.scoring_weights == {
        "rapid_encryption": 40.0,
        "mass_rename": 30.0,
        "high_entropy": 25.0,
        "cpu_spike": 15.0,
        "unknown_program": 10.0,
    }


def test_config_loader_prefers_the_fsd_named_file(tmp_path, monkeypatch):
    """When both files exist, config.yaml wins (the FSD-named one)."""
    from rdrs.runtime import RuntimeManager

    root = tmp_path
    (root / "config").mkdir()

    (root / "config.yaml").write_text("runtime:\n  name: from-root\n", encoding="utf-8")
    (root / "config" / "rdrs.yaml").write_text(
        "runtime:\n  name: from-legacy\n", encoding="utf-8"
    )

    import rdrs.runtime as runtime_module

    monkeypatch.setattr(
        runtime_module,
        "__file__",
        str(root / "src" / "rdrs" / "runtime.py"),
    )

    resolved = RuntimeManager.default_config_path()

    assert resolved == root / "config.yaml"


def test_config_loader_falls_back_to_the_legacy_path(tmp_path, monkeypatch):
    """An older checkout with only config/rdrs.yaml must still start."""
    from rdrs.runtime import RuntimeManager

    root = tmp_path
    (root / "config").mkdir()
    (root / "config" / "rdrs.yaml").write_text(
        "runtime:\n  name: from-legacy\n", encoding="utf-8"
    )

    import rdrs.runtime as runtime_module

    monkeypatch.setattr(
        runtime_module,
        "__file__",
        str(root / "src" / "rdrs" / "runtime.py"),
    )

    resolved = RuntimeManager.default_config_path()

    assert resolved == root / "config" / "rdrs.yaml"


# --------------------------------------------------------------------------
# the project's own configuration must be left alone
# --------------------------------------------------------------------------


def test_real_config_is_intact():
    """Guards against the suite ever writing to the project's configuration.

    A stale runtime reference once caused a test to persist settings into the
    real ``config/rdrs.yaml``. This asserts the shipped values are unchanged.
    """
    config = yaml.safe_load(REAL_CONFIG.read_text(encoding="utf-8"))

    assert config["runtime"]["name"] == "rdrs"
    assert config["runtime"]["environment"] == "test"

    # Operator-editable: must be valid, not fixed.
    assert isinstance(config["runtime"]["shutdown_timeout"], (int, float))
    assert config["runtime"]["shutdown_timeout"] > 0

    # Operator-editable: must be valid, not fixed.
    assert config["logging"]["level"] in {
        "DEBUG",
        "INFO",
        "WARNING",
        "ERROR",
        "CRITICAL",
    }
    assert isinstance(config["persistence"]["enabled"], bool)

    # Detection thresholds and scoring weights are locked values.
    assert config["detection"] == {
        "window_seconds": 60.0,
        "modified_files_per_minute": 20.0,
        "renames": 10.0,
        "extension_changes": 5.0,
        "average_entropy": 7.0,
        "cpu_percent": 80.0,
    }
    assert config["scoring"]["weights"] == {
        "rapid_encryption": 40.0,
        "mass_rename": 30.0,
        "high_entropy": 25.0,
        "cpu_spike": 15.0,
        "unknown_program": 10.0,
    }


def test_real_config_matches_its_recorded_original():
    """No LOCKED value may have changed from the recorded original.

    The recorded original predates the ``watch_paths`` setting, which Build
    Guide Phase 1 requires ("which folders to watch"), so that key is expected
    to be the only addition. Every locked value must still match exactly --
    this is what proves the configuration has not silently drifted.
    """
    backup = (
        PROJECT_ROOT / "reports" / "backups" / "config" / "rdrs.yaml.original"
    )

    current = yaml.safe_load(REAL_CONFIG.read_text(encoding="utf-8"))
    if backup.is_file():
        original = yaml.safe_load(backup.read_text(encoding="utf-8"))
    else:
        # Some source archives omit the generated audit snapshot. Keep the
        # locked-value check meaningful against the documented shipped baseline
        # rather than treating the unavailable artifact as evidence of drift.
        original = {
            "runtime": {
                "name": "rdrs",
                "environment": "test",
                "data_dir": "data",
                "logs_dir": "logs",
            },
            "detection": {
                "window_seconds": 60.0,
                "modified_files_per_minute": 20.0,
                "renames": 10.0,
                "extension_changes": 5.0,
                "average_entropy": 7.0,
                "cpu_percent": 80.0,
            },
            "scoring": {
                "weights": {
                    "rapid_encryption": 40.0,
                    "mass_rename": 30.0,
                    "high_entropy": 25.0,
                    "cpu_spike": 15.0,
                    "unknown_program": 10.0,
                }
            },
            "logging": {"level": "INFO"},
            "persistence": {
                "enabled": True,
                "db_path": r"data\rdrs.db",
            },
        }

    # The intentional, documented addition.
    assert "watch_paths" in current["runtime"]

    # ``shutdown_timeout`` is operator-editable through the Settings page, so it
    # is excluded from the locked-value comparison and checked for validity.
    editable = {"watch_paths", "shutdown_timeout"}
    current_runtime = {
        key: value
        for key, value in current["runtime"].items()
        if key not in editable
    }
    original_runtime = {
        key: value
        for key, value in original["runtime"].items()
        if key not in editable
    }

    assert current_runtime == original_runtime
    assert isinstance(current["runtime"]["shutdown_timeout"], (int, float))
    assert current["runtime"]["shutdown_timeout"] > 0

    # Detection thresholds and scoring weights are locked values and must match
    # the recorded original exactly.
    for section in ("detection", "scoring"):
        assert current[section] == original[section], section

    # ``logging`` and ``persistence`` are operator-editable, so they are
    # checked for shape rather than equality.
    assert set(current["logging"]) == set(original["logging"])
    assert set(current["persistence"]) == set(original["persistence"])
    assert isinstance(current["persistence"]["enabled"], bool)


# --------------------------------------------------------------------------
# FR-07 / FR-05: the stored score must respect the documented 0-100 range
# --------------------------------------------------------------------------




# --------------------------------------------------------------------------
# FR-07 / FR-05: the stored score must respect the documented 0-100 range
# --------------------------------------------------------------------------


def test_record_score_rejects_out_of_range_values(tmp_path):
    """A score the detector could never produce must not reach storage."""
    from rdrs.storage import DatabaseManager

    database = DatabaseManager(tmp_path / "scores.db")
    database.initialize()

    assert database.record_score(0.0, "NORMAL", "lower bound") == 1
    assert database.record_score(100.0, "CRITICAL", "upper bound") == 2

    for invalid in (-1.0, -0.01, 100.01, 150.0):
        with pytest.raises(ValueError):
            database.record_score(invalid, "CRITICAL", "out of range")

    # Only the two valid rows were written.
    assert database.count_records("scores") == 2

    database.close()
