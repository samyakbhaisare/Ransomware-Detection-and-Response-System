import signal
import subprocess
import sys
from pathlib import Path

import pytest

from rdrs.runtime import RuntimeManager, RuntimeStatus


@pytest.fixture(autouse=True)
def clear_runtime_state():
    project_root = Path(__file__).resolve().parents[2]
    db_path = project_root / "data" / "rdrs.db"
    RuntimeManager.reset_singleton()
    try:
        if db_path.exists():
            db_path.unlink()
    except PermissionError:
        pass
    yield
    try:
        mgr = RuntimeManager()
        if mgr.state in {RuntimeStatus.STARTING, RuntimeStatus.RUNNING, RuntimeStatus.DEGRADED, RuntimeStatus.SHUTTING_DOWN}:
            mgr.stop()
        elif mgr.state == RuntimeStatus.FAILED:
            mgr.force_stop()
    except Exception:
        pass
    RuntimeManager.reset_singleton()
    try:
        if db_path.exists():
            db_path.unlink()
    except PermissionError:
        pass


@pytest.fixture
def runtime(tmp_path):
    RuntimeManager.reset_singleton()
    project_root = Path(__file__).resolve().parents[2]
    config_path = project_root / "config" / "rdrs.yaml"
    runtime = RuntimeManager(
        config_path=config_path,
        data_dir=tmp_path,
        logs_dir=tmp_path / "logs",
        db_path=tmp_path / "runtime.db",
    )
    yield runtime
    try:
        if runtime.state == RuntimeStatus.RUNNING:
            runtime.stop()
    except RuntimeError:
        pass
    try:
        if runtime.state in {RuntimeStatus.STARTING, RuntimeStatus.SHUTTING_DOWN}:
            runtime.force_stop()
    except RuntimeError:
        pass
    RuntimeManager.reset_singleton()


def test_runtime_lifecycle_start_stop(runtime):
    runtime.start()
    assert runtime.state == RuntimeStatus.RUNNING
    assert runtime.status()["state"] == RuntimeStatus.RUNNING.value
    runtime.stop()
    assert runtime.state == RuntimeStatus.STOPPED


def test_repeated_start_is_invalid(runtime):
    runtime.start()
    with pytest.raises(RuntimeError, match="already started|already running"):
        runtime.start()
    runtime.stop()


def test_invalid_stop_before_start(runtime):
    with pytest.raises(RuntimeError, match="not running|not started"):
        runtime.stop()


def test_startup_failure_sets_failed_state(tmp_path):
    RuntimeManager.reset_singleton()
    project_root = Path(__file__).resolve().parents[2]
    bad_config = project_root / "config" / "missing.yaml"
    runtime = RuntimeManager(config_path=bad_config, data_dir=tmp_path, logs_dir=tmp_path / "logs")
    with pytest.raises(RuntimeError, match="Failed to initialize runtime|configuration"):
        runtime.start()
    assert runtime.state == RuntimeStatus.FAILED
    RuntimeManager.reset_singleton()


def test_health_tracking_and_registry(runtime):
    runtime.start()
    runtime.register_subsystem("sensor", healthy=True, details={"ok": True})
    runtime.register_subsystem("audit", healthy=False, details={"error": "latency"})
    health = runtime.health_status()
    assert "sensor" in health["subsystems"]
    assert health["healthy"] is False
    runtime.stop()


def test_signal_shutdown(tmp_path):
    RuntimeManager.reset_singleton()
    project_root = Path(__file__).resolve().parents[2]
    runtime = RuntimeManager(
        config_path=project_root / "config" / "rdrs.yaml",
        data_dir=tmp_path / "data",
        logs_dir=tmp_path / "logs",
    )
    runtime.start()
    runtime.handle_signal(signal.SIGTERM)
    assert runtime.state in {RuntimeStatus.SHUTTING_DOWN, RuntimeStatus.STOPPED}
    RuntimeManager.reset_singleton()


def test_cli_status_and_start_stop():
    result = subprocess.run(
        [sys.executable, "-m", "rdrs.cli", "status"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert result.returncode == 0
    assert "STOPPED" in result.stdout.upper()

    start = subprocess.run(
        [sys.executable, "-m", "rdrs.cli", "start"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert start.returncode == 0
    assert "RUNNING" in start.stdout.upper()

    status = subprocess.run(
        [sys.executable, "-m", "rdrs.cli", "status"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert status.returncode == 0
    assert "RUNNING" in status.stdout.upper()

    stop = subprocess.run(
        [sys.executable, "-m", "rdrs.cli", "stop"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert stop.returncode == 0
    assert "STOPPED" in stop.stdout.upper()

    final_status = subprocess.run(
        [sys.executable, "-m", "rdrs.cli", "status"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert final_status.returncode == 0
    assert "STOPPED" in final_status.stdout.upper()


def test_runtime_tracks_database_and_cleanup(tmp_path):
    db_path = tmp_path / "runtime.db"
    runtime = RuntimeManager(
        config_path=Path(__file__).resolve().parents[2] / "config" / "rdrs.yaml",
        data_dir=tmp_path,
        db_path=db_path,
        logs_dir=tmp_path / "logs",
    )
    runtime.start()
    assert db_path.exists()
    runtime.stop()
    assert runtime._database is None or runtime._database.connection is None
    assert runtime.state == RuntimeStatus.STOPPED
    RuntimeManager.reset_singleton()
