import signal
import subprocess
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from rdrs.runtime import RuntimeManager, RuntimeStatus


@pytest.fixture(autouse=True)
def clear_runtime_state():
    """Reset the runtime singleton between tests.

    This fixture must never touch the project's live database. It previously
    deleted ``<project>/data/rdrs.db`` before *and* after every test in this
    module, relying on the execution sandbox to make the deletion fail with
    ``OSError``. Whenever the sandbox did not intercept the call, running the
    suite silently destroyed the live database -- which is exactly what
    happened to a 12 MB database holding a real detection run.

    Test isolation is achieved by pointing at ``tmp_path`` (see the
    ``runtime`` fixture), not by deleting the operator's data.
    """
    RuntimeManager.reset_singleton()

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


@pytest.fixture
def runtime(tmp_path):
    RuntimeManager.reset_singleton()
    # Use the runtime's own resolution so the fixture follows the FSD-named
    # configuration file rather than hard-coding a path.
    config_path = RuntimeManager.default_config_path()
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


def test_runtime_detector_uses_configured_scoring_weights(tmp_path):
    """The active runtime detector must use weights loaded from YAML."""
    project_root = Path(__file__).resolve().parents[2]
    config = yaml.safe_load(
        (project_root / "config.yaml").read_text(encoding="utf-8")
    )
    data_dir = tmp_path / "data"
    logs_dir = tmp_path / "logs"
    data_dir.mkdir()
    logs_dir.mkdir()
    config["runtime"]["data_dir"] = str(data_dir)
    config["runtime"]["logs_dir"] = str(logs_dir)
    config["runtime"]["watch_paths"] = [str(data_dir)]
    config["persistence"]["db_path"] = str(data_dir / "runtime.db")
    config["scoring"]["weights"]["rapid_encryption"] = 12.5

    config_path = tmp_path / "custom-weights.yaml"
    config_path.write_text(
        yaml.safe_dump(config, sort_keys=False),
        encoding="utf-8",
    )

    runtime = RuntimeManager(config_path=config_path)

    try:
        runtime._create_detection_pipeline()

        for index in range(20):
            runtime._handle_file_event(
                {
                    "action": "modified",
                    "path": str(data_dir / f"document_{index}.txt"),
                    "timestamp": 1_800_000_000 + index,
                    "metadata": {"entropy": 3.0},
                }
            )

        assert runtime._detector.scoring_weights["rapid_encryption"] == 12.5
        assert runtime._last_analysis.score.rapid_encryption == pytest.approx(12.5)
        assert runtime._last_analysis.score.total == pytest.approx(12.5)
    finally:
        RuntimeManager.reset_singleton()


def test_configured_shutdown_timeout_is_used_for_process_join(runtime):
    class ThreadStub:
        timeout = None

        def join(self, timeout):
            self.timeout = timeout

        def is_alive(self):
            return False

    configured_timeout = 0.375
    runtime._config.shutdown_timeout = configured_timeout
    thread = ThreadStub()
    runtime._process_thread = thread
    runtime._process_monitor = object()

    assert runtime._stop_process_monitor() is True
    assert thread.timeout == pytest.approx(configured_timeout)


def test_incident_attribution_prefers_exact_event_ids_for_reused_paths(runtime):
    database = runtime._database
    old_id = database.record_file_event(
        {"path": "C:/sandbox/reused.txt", "action": "modified"}
    )
    burst_id = database.record_file_event(
        {"path": "C:/sandbox/reused.txt", "action": "modified"}
    )

    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE events SET created_at = ? WHERE id = ?",
            (datetime.now(timezone.utc) - timedelta(days=2), old_id),
        )

    attributed = runtime._attribute_events_to_incident(
        77,
        ["C:/sandbox/reused.txt"],
        event_ids=[burst_id],
        since=datetime.now(timezone.utc) - timedelta(minutes=1),
        until=datetime.now(timezone.utc),
    )

    assert attributed == 1
    assert database.get_incident(77) is None
    assert database.list_events_for_incident(77, limit=10)[0]["id"] == burst_id
    old_event = next(
        event
        for event in database.list_events(limit=10)
        if event["id"] == old_id
    )
    assert old_event["incident_id"] is None


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
        config_path=RuntimeManager.default_config_path(),
        data_dir=tmp_path / "data",
        logs_dir=tmp_path / "logs",
    )
    runtime.start()
    runtime.handle_signal(signal.SIGTERM)
    assert runtime.state in {RuntimeStatus.SHUTTING_DOWN, RuntimeStatus.STOPPED}
    RuntimeManager.reset_singleton()


def test_cli_status_and_start_stop(tmp_path):
    # Each CLI call is its own process, so the state is read back from the
    # database. That needs persistence ON, which is pinned here instead of
    # inherited from the operator's live configuration.
    config_path = _durable_config(tmp_path)
    env = {**os.environ, "RDRS_CONFIG_PATH": str(config_path)}

    def run_cli(command):
        return subprocess.run(
            [sys.executable, "-m", "rdrs.cli", command],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parents[2],
            env=env,
        )

    # Establish the precondition rather than assuming it, so the test is
    # order-independent. `cli.py` now honours the configuration's paths, so the
    # temp config above isolates the database, and `stop` is idempotent.
    reset = run_cli("stop")
    assert reset.returncode == 0

    result = run_cli("status")
    assert result.returncode == 0
    assert "STOPPED" in result.stdout.upper()

    start = run_cli("start")
    assert start.returncode == 0
    assert "RUNNING" in start.stdout.upper()

    status = run_cli("status")
    assert status.returncode == 0
    assert "RUNNING" in status.stdout.upper()

    stop = run_cli("stop")
    assert stop.returncode == 0
    assert "STOPPED" in stop.stdout.upper()

    final_status = run_cli("status")
    assert final_status.returncode == 0
    assert "STOPPED" in final_status.stdout.upper()


def _durable_config(tmp_path):
    """A config with persistence ON, so a real file-backed DB is created.

    These tests exercise the database lifecycle, so they must control the
    persistence setting rather than inherit whatever the operator last saved.
    """
    source = (
        Path(__file__).resolve().parents[2] / "config.yaml"
    ).read_text(encoding="utf-8")

    source = source.replace(
        "  enabled: false", "  enabled: true"
    )

    # Point every path at the temp directory. Without this the copied config
    # would still reference the project's real database, so the CLI would read
    # whatever state that file happens to hold.
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "logs").mkdir(exist_ok=True)

    source = (
        source.replace("data_dir: data", f"data_dir: {(tmp_path / 'data').as_posix()}")
        .replace("logs_dir: logs", f"logs_dir: {(tmp_path / 'logs').as_posix()}")
        .replace(
            "db_path: data\\rdrs.db",
            f"db_path: {(tmp_path / 'data' / 'rdrs.db').as_posix()}",
        )
        .replace(
            "  watch_paths://n    - data\n",
            f"  watch_paths://n    - {(tmp_path / 'data').as_posix()}\n",
        )
    )

    config_path = tmp_path / "rdrs.yaml"
    config_path.write_text(source, encoding="utf-8")

    return config_path


def test_runtime_tracks_database_and_cleanup(tmp_path):
    db_path = tmp_path / "runtime.db"
    runtime = RuntimeManager(
        config_path=_durable_config(tmp_path),
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

def test_runtime_apply_settings_file_not_found(monkeypatch, tmp_path):
    RuntimeManager.reset_singleton()

    def fail_load(**kwargs):
        raise FileNotFoundError("missing")

    monkeypatch.setattr(
        "rdrs.runtime.RuntimeConfig.load",
        fail_load,
    )

    mgr = RuntimeManager(
        config_path=tmp_path / "missing.yaml",
        data_dir=tmp_path,
    )

    assert mgr._config is None

    RuntimeManager.reset_singleton()


def test_runtime_load_persisted_state_values(runtime, monkeypatch):
    class Result:
        def __init__(self, value):
            self.value = value

        def fetchone(self):
            return (self.value,)

    class Connection:
        def __init__(self, value):
            self.value = value

        def exec_driver_sql(self, *_args):
            return Result(self.value)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class Engine:
        def __init__(self, value):
            self.value = value

        def connect(self):
            return Connection(self.value)

    class DB:
        def __init__(self, value):
            self.engine = Engine(value)

        def initialize(self):
            pass

    runtime._database = DB("RUNNING")
    runtime._load_persisted_state()

    assert runtime.state == RuntimeStatus.RUNNING


def test_runtime_load_persisted_state_invalid_resets(runtime):
    class Result:
        def fetchone(self):
            return ("INVALID_STATE",)

    class Connection:
        def exec_driver_sql(self, *_args):
            return Result()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class Engine:
        def connect(self):
            return Connection()

    class DB:
        engine = Engine()

        def initialize(self):
            raise RuntimeError("database failure")

    runtime._database = DB()
    runtime.state = RuntimeStatus.RUNNING

    runtime._load_persisted_state()

    assert runtime.state == RuntimeStatus.STOPPED


def test_runtime_load_persisted_state_no_database(runtime):
    runtime._database = None
    runtime.state = RuntimeStatus.RUNNING

    runtime._load_persisted_state()

    assert runtime.state == RuntimeStatus.RUNNING


def test_runtime_register_subsystem_degrades(runtime):
    runtime.state = RuntimeStatus.RUNNING

    runtime.register_subsystem(
        "critical",
        healthy=False,
        critical=True,
        details={"error": "test"},
    )

    assert runtime.state == RuntimeStatus.DEGRADED


def test_runtime_register_subsystem_recovers(runtime):
    runtime.state = RuntimeStatus.DEGRADED

    runtime.register_subsystem(
        "critical",
        healthy=True,
        critical=True,
    )

    assert runtime.state == RuntimeStatus.RUNNING


def test_runtime_init_persistence_requires_config(runtime):
    runtime._config = None

    with pytest.raises(
        RuntimeError,
        match="configuration not initialized",
    ):
        runtime._init_persistence()


def test_runtime_init_persistence_disabled(runtime):
    """Persistence off must still leave a usable database.

    The previous expectation was ``runtime._database is None``, which asserted
    the defect: with no database the pipeline could not be initialised, so
    ``start()`` raised and the runtime could never be restarted.
    """
    class Config:
        persistence_enabled = False
        db_path = None

    runtime._config = Config()
    runtime._database = None

    runtime._init_persistence()

    assert runtime._database is not None
    assert runtime._database.durable is False
    assert runtime._database.engine is not None


def test_runtime_create_pipeline_requires_config(runtime):
    runtime._config = None

    with pytest.raises(
        RuntimeError,
        match="configuration not initialized",
    ):
        runtime._create_detection_pipeline()


def test_runtime_create_pipeline_requires_database(runtime):
    class Config:
        detection_thresholds = {}
        simulation_mode = True
        data_dir = runtime._pending_data_dir

    runtime._config = Config()
    runtime._database = None

    with pytest.raises(
        RuntimeError,
        match="Database must be initialized",
    ):
        runtime._create_detection_pipeline()


def test_build_detection_reason_without_signals():
    class Score:
        def __init__(self):
            self.total = 0

    class Analysis:
        def to_dict(self):
            return {
                "signals": {
                    "rapid_encryption": False,
                    "mass_rename": False,
                }
            }

    result = RuntimeManager._build_detection_reason(
        Analysis()
    )

    assert result == (
        "Dangerous ransomware-like burst detected"
    )


def test_build_detection_reason_with_signals():
    class Analysis:
        def to_dict(self):
            return {
                "signals": {
                    "rapid_encryption": True,
                    "mass_rename": False,
                    "high_entropy": True,
                }
            }

    result = RuntimeManager._build_detection_reason(
        Analysis()
    )

    assert "rapid_encryption" in result
    assert "high_entropy" in result
    assert "mass_rename" not in result


def test_runtime_status_without_analysis(runtime):
    runtime.state = RuntimeStatus.STOPPED
    runtime._last_analysis = None

    result = runtime.status()

    assert result["state"] == "STOPPED"
    assert result["healthy"] is False
    assert result["threat"]["score"] == 0
    assert result["threat"]["level"] == "NORMAL"


def test_runtime_health_status_empty(runtime):
    runtime._subsystems = {}
    runtime.state = RuntimeStatus.RUNNING

    result = runtime.health_status()

    assert result["healthy"] is True
    assert result["subsystems"] == {}
    assert result["degraded"] is False


def test_runtime_health_status_degraded(runtime):
    runtime.state = RuntimeStatus.DEGRADED
    runtime._subsystems = {
        "test": {
            "healthy": False,
            "critical": True,
        }
    }

    result = runtime.health_status()

    assert result["healthy"] is False
    assert result["degraded"] is True


def test_runtime_force_stop(runtime):
    runtime.state = RuntimeStatus.RUNNING
    runtime.started_at = 123.0
    runtime._subsystems = {"x": {"healthy": True}}

    runtime.force_stop()

    assert runtime.state == RuntimeStatus.STOPPED
    assert runtime.started_at is None
    assert runtime._detector is None
    assert runtime._response is None
    assert runtime._last_analysis is None
    assert runtime._subsystems == {}
    assert runtime._shutdown_requested is True


def test_runtime_close_stopped(runtime):
    runtime.state = RuntimeStatus.STOPPED

    runtime.close()

    assert runtime.state == RuntimeStatus.STOPPED


def test_runtime_handle_signal_stopped(runtime):
    runtime.state = RuntimeStatus.STOPPED

    runtime.handle_signal(
        signal.SIGTERM
    )

    assert runtime.state == RuntimeStatus.STOPPED


def test_runtime_handle_unknown_signal(runtime):
    runtime.state = RuntimeStatus.STOPPED

    runtime.handle_signal(999)

    assert runtime.state == RuntimeStatus.STOPPED


def test_runtime_shutdown_delegates_to_stop(runtime, monkeypatch):
    called = []

    def fake_stop():
        called.append(True)
        return {"state": "STOPPED"}

    monkeypatch.setattr(
        runtime,
        "stop",
        fake_stop,
    )

    result = runtime.shutdown()

    assert called == [True]
    assert result["state"] == "STOPPED"


def test_runtime_repr(runtime):
    text = repr(runtime)

    assert text.startswith("RuntimeManager(")
    assert "state=STOPPED" in text

def test_runtime_init_reuses_existing_singleton_with_settings(runtime, tmp_path):
    RuntimeManager.reset_singleton()

    first = RuntimeManager(
        config_path=RuntimeManager.default_config_path(),
        data_dir=tmp_path,
    )

    second = RuntimeManager(
        config_path=RuntimeManager.default_config_path(),
        data_dir=tmp_path / "second",
        logs_dir=tmp_path / "second" / "logs",
        db_path=tmp_path / "second" / "rdrs.db",
    )

    assert first is second

    RuntimeManager.reset_singleton()


def test_runtime_apply_settings_uses_pending_values(runtime, monkeypatch):
    calls = []

    def fake_load(**kwargs):
        calls.append(kwargs)
        return runtime._config

    monkeypatch.setattr(
        "rdrs.runtime.RuntimeConfig.load",
        fake_load,
    )

    runtime._config = type(
        "Config",
        (),
        {
            "log_level": "INFO",
            "db_path": runtime._pending_db_path,
        },
    )()

    runtime._pending_data_dir = None
    runtime._pending_logs_dir = None
    runtime._pending_db_path = None

    runtime._apply_runtime_settings()

    assert calls
    assert calls[-1]["config_path"] == runtime._pending_config_path


def test_runtime_load_persisted_state_empty_row(runtime):
    class Result:
        def fetchone(self):
            return None

    class Connection:
        def exec_driver_sql(self, *_args):
            return Result()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class Engine:
        def connect(self):
            return Connection()

    class DB:
        engine = Engine()

        def initialize(self):
            pass

    runtime._database = DB()
    runtime.state = RuntimeStatus.RUNNING

    runtime._load_persisted_state()

    assert runtime.state == RuntimeStatus.RUNNING


def test_runtime_load_persisted_state_valid_value(runtime):
    class Result:
        def fetchone(self):
            return ("DEGRADED",)

    class Connection:
        def exec_driver_sql(self, *_args):
            return Result()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class Engine:
        def connect(self):
            return Connection()

    class DB:
        engine = Engine()

        def initialize(self):
            pass

    runtime._database = DB()
    runtime._load_persisted_state()

    assert runtime.state == RuntimeStatus.DEGRADED


def test_runtime_install_signal_handlers_twice(runtime, monkeypatch):
    calls = []

    monkeypatch.setattr(
        "rdrs.runtime.signal.signal",
        lambda signum, handler: calls.append(signum),
    )

    runtime._signal_handlers_installed = False
    runtime._install_signal_handlers()

    assert runtime._signal_handlers_installed is True
    assert len(calls) == 2

    runtime._install_signal_handlers()

    assert len(calls) == 2


def test_runtime_install_signal_handlers_handles_value_error(
    runtime,
    monkeypatch,
):
    runtime._signal_handlers_installed = False

    def fail_signal(*_args):
        raise ValueError("signal unavailable")

    monkeypatch.setattr(
        "rdrs.runtime.signal.signal",
        fail_signal,
    )

    runtime._install_signal_handlers()

    assert runtime._signal_handlers_installed is True


def test_runtime_register_subsystem_database_error(
    runtime,
    monkeypatch,
):
    class DB:
        def record_subsystem(self, *_args):
            raise RuntimeError("database failure")

    runtime._database = DB()

    runtime.state = RuntimeStatus.RUNNING

    runtime.register_subsystem(
        "database-test",
        healthy=True,
        critical=True,
    )

    assert runtime._subsystems["database-test"]["healthy"] is True


def test_runtime_init_persistence_creates_database(
    runtime,
    monkeypatch,
    tmp_path,
):
    class Config:
        persistence_enabled = True
        db_path = tmp_path / "new.db"

    class DB:
        def __init__(self, path):
            self.path = path
            self.initialized = False
            self.states = []

        def initialize(self):
            self.initialized = True

        def record_runtime_state(self, state):
            self.states.append(state)

    created = {}

    def factory(path):
        created["db"] = DB(path)
        return created["db"]

    monkeypatch.setattr(
        "rdrs.runtime.DatabaseManager",
        factory,
    )

    runtime._config = Config()
    runtime._database = None
    runtime.state = RuntimeStatus.STARTING

    runtime._init_persistence()

    assert runtime._database is created["db"]
    assert created["db"].initialized is True
    assert created["db"].states == ["STARTING"]


def test_runtime_init_persistence_records_state(
    runtime,
):
    class DB:
        def __init__(self):
            self.initialized = False
            self.state = None

        def initialize(self):
            self.initialized = True

        def record_runtime_state(self, state):
            self.state = state

    class Config:
        persistence_enabled = True

    db = DB()

    runtime._config = Config()
    runtime._database = db
    runtime.state = RuntimeStatus.RUNNING

    runtime._init_persistence()

    assert db.initialized is True
    assert db.state == "RUNNING"


def test_runtime_handle_file_event_without_detector(runtime):
    runtime._detector = None

    runtime._handle_file_event(
        {
            "action": "modified",
            "path": "test.txt",
        }
    )


def test_runtime_handle_file_event_database_missing(
    runtime,
    monkeypatch,
):
    class Detector:
        def ingest_event(self, event):
            return type(
                "Analysis",
                (),
                {
                    "score": type(
                        "Score",
                        (),
                        {
                            "level": type(
                                "Level",
                                (),
                                {"value": "NORMAL"},
                            )()
                        },
                    )(),
                },
            )()

    runtime._detector = Detector()
    runtime._database = None

    runtime._handle_file_event(
        {
            "action": "modified",
            "path": "test.txt",
        }
    )

    assert runtime._last_analysis is not None


def test_runtime_handle_file_event_normal(runtime):
    class Detector:
        def ingest_event(self, event):
            return type(
                "Analysis",
                (),
                {
                    "score": type(
                        "Score",
                        (),
                        {
                            "level": type(
                                "Level",
                                (),
                                {"value": "NORMAL"},
                            )()
                        },
                    )(),
                    "dangerous_burst": False,
                },
            )()

    class DB:
        pass

    runtime._detector = Detector()
    runtime._database = DB()

    runtime._handle_file_event(
        {
            "action": "modified",
            "path": "test.txt",
        }
    )

    assert runtime._last_analysis is not None


def test_runtime_handle_file_event_non_dangerous_burst(runtime):
    class Detector:
        def ingest_event(self, event):
            return type(
                "Analysis",
                (),
                {
                    "score": type(
                        "Score",
                        (),
                        {
                            "level": type(
                                "Level",
                                (),
                                {"value": "WARNING"},
                            )()
                        },
                    )(),
                    "dangerous_burst": False,
                },
            )()

    class DB:
        pass

    runtime._detector = Detector()
    runtime._database = DB()

    runtime._handle_file_event(
        {
            "action": "modified",
            "path": "test.txt",
        }
    )

    assert runtime._last_analysis is not None


def test_runtime_handle_file_event_missing_response(runtime):
    class Detector:
        def ingest_event(self, event):
            return type(
                "Analysis",
                (),
                {
                    "score": type(
                        "Score",
                        (),
                        {
                            "level": type(
                                "Level",
                                (),
                                {"value": "CRITICAL"},
                            )()
                        },
                    )(),
                    "dangerous_burst": True,
                    "affected_files": [],
                    "suspect_process": None,
                    "to_dict": lambda self: {
                        "signals": {}
                    },
                },
            )()

    class DB:
        pass

    runtime._detector = Detector()
    runtime._database = DB()
    runtime._response = None

    runtime._handle_file_event(
        {
            "action": "modified",
            "path": "test.txt",
        }
    )

    assert runtime._subsystems["detection"]["healthy"] is False


def test_runtime_process_loop_skips_when_not_running(runtime):
    runtime.state = RuntimeStatus.STOPPED

    runtime._process_stop.set()

    runtime._process_monitor_loop()

    assert runtime.state == RuntimeStatus.STOPPED


def test_runtime_process_loop_without_database(runtime):
    runtime.state = RuntimeStatus.RUNNING
    runtime._database = None
    runtime._process_stop.clear()
    runtime._process_stop.set()

    runtime._process_monitor_loop()

    assert runtime.state == RuntimeStatus.RUNNING


def test_runtime_process_loop_without_process_monitor(runtime):
    runtime.state = RuntimeStatus.RUNNING
    runtime._database = object()
    runtime._process_monitor = None
    runtime._process_stop.clear()
    runtime._process_stop.set()

    runtime._process_monitor_loop()

    assert runtime.state == RuntimeStatus.RUNNING


def test_runtime_start_process_monitor_duplicate(runtime):
    class Thread:
        def is_alive(self):
            return True

    runtime._process_thread = Thread()

    with pytest.raises(
        RuntimeError,
        match="Process monitor is already running",
    ):
        runtime._start_process_monitor()


def test_runtime_stop_when_failed(runtime):
    runtime.state = RuntimeStatus.FAILED

    with pytest.raises(
        RuntimeError,
        match="not running",
    ):
        runtime.stop()


def test_runtime_start_when_shutting_down(runtime):
    runtime.state = RuntimeStatus.SHUTTING_DOWN

    with pytest.raises(
        RuntimeError,
        match="shutting down",
    ):
        runtime.start()


def test_runtime_start_when_failed(runtime):
    runtime.state = RuntimeStatus.FAILED

    with pytest.raises(
        RuntimeError,
        match="FAILED",
    ):
        runtime.start()


def test_runtime_start_config_missing(runtime):
    runtime._config = None

    monkeypatch = pytest.MonkeyPatch()

    def fake_apply(**_kwargs):
        runtime._config = None

    monkeypatch.setattr(
        runtime,
        "_apply_runtime_settings",
        fake_apply,
    )

    with pytest.raises(
        RuntimeError,
        match="Configuration file not found",
    ):
        runtime.start()

    assert runtime.state == RuntimeStatus.FAILED

    monkeypatch.undo()


def test_runtime_start_cleanup_failure_paths(runtime, monkeypatch):
    runtime._config = None

    def fake_apply(**_kwargs):
        raise RuntimeError("config failure")

    monkeypatch.setattr(
        runtime,
        "_apply_runtime_settings",
        fake_apply,
    )

    with pytest.raises(RuntimeError, match="Failed to initialize"):
        runtime.start()

    assert runtime.state == RuntimeStatus.FAILED


def test_runtime_status_with_analysis(runtime):
    class Analysis:
        def to_dict(self):
            return {
                "score": 95,
                "level": "CRITICAL",
                "signals": {
                    "rapid_encryption": True,
                },
                "dangerous_burst": True,
                "affected_files": [
                    "a.txt",
                ],
                "suspect_process": {
                    "pid": 123,
                },
                "metrics": {
                    "renames": 10,
                },
            }

    runtime._last_analysis = Analysis()
    runtime.state = RuntimeStatus.RUNNING

    result = runtime.status()

    assert result["threat"]["score"] == 95
    assert result["threat"]["level"] == "CRITICAL"
    assert result["threat"]["dangerous_burst"] is True
    assert result["threat"]["affected_files"] == ["a.txt"]
    assert result["threat"]["suspect_process"]["pid"] == 123
    assert result["threat"]["metrics"]["renames"] == 10


def test_runtime_close_running_uses_stop(runtime, monkeypatch):
    calls = []

    def fake_stop():
        calls.append("stop")

    monkeypatch.setattr(runtime, "stop", fake_stop)

    runtime.state = RuntimeStatus.RUNNING
    runtime.close()

    assert calls == ["stop"]


def test_runtime_close_stop_failure_uses_force_stop(runtime, monkeypatch):
    calls = []

    def fake_stop():
        raise RuntimeError("stop failure")

    def fake_force_stop():
        calls.append("force")

    monkeypatch.setattr(runtime, "stop", fake_stop)
    monkeypatch.setattr(runtime, "force_stop", fake_force_stop)

    runtime.state = RuntimeStatus.RUNNING
    runtime.close()

    assert calls == ["force"]


def test_runtime_handle_signal_stop_failure(runtime, monkeypatch):
    def fake_stop():
        raise RuntimeError("stop failure")

    monkeypatch.setattr(runtime, "stop", fake_stop)

    runtime.state = RuntimeStatus.RUNNING
    runtime.handle_signal(signal.SIGTERM)

    assert runtime.state == RuntimeStatus.SHUTTING_DOWN


def test_runtime_handle_signal_non_runtime_exception(runtime, monkeypatch):
    def fake_stop():
        raise ValueError("unexpected")

    monkeypatch.setattr(runtime, "stop", fake_stop)

    runtime.state = RuntimeStatus.RUNNING

    with pytest.raises(ValueError, match="unexpected"):
        runtime.handle_signal(signal.SIGINT)

    assert runtime.state == RuntimeStatus.RUNNING

def test_runtime_load_persisted_state_invalid_value(runtime):
    class Result:
        def fetchone(self):
            return ("NOT_A_REAL_STATE",)

    class Connection:
        def exec_driver_sql(self, *_args):
            return Result()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class Engine:
        def connect(self):
            return Connection()

    class DB:
        engine = Engine()

        def initialize(self):
            pass

    runtime._database = DB()
    runtime.state = RuntimeStatus.RUNNING

    runtime._load_persisted_state()

    assert runtime.state == RuntimeStatus.STOPPED


def test_runtime_handle_file_event_critical_response(runtime):
    class Level:
        value = "CRITICAL"

    class Score:
        total = 95
        level = Level()

    class Analysis:
        score = Score()
        dangerous_burst = True
        affected_files = ["a.txt", "b.txt"]
        suspect_process = {"pid": 123}

        def to_dict(self):
            return {
                "score": 95,
                "level": "CRITICAL",
                "signals": {
                    "rapid_encryption": True,
                    "mass_rename": True,
                },
                "dangerous_burst": True,
                "affected_files": self.affected_files,
                "suspect_process": self.suspect_process,
                "metrics": {},
            }

    class Detector:
        def ingest_event(self, event):
            return Analysis()

        def reset(self):
            self.reset_called = True

    class DB:
        pass

    class Result:
        score_id = 1
        alert_id = 2
        incident_id = 3
        quarantined_files = ["a.txt", "b.txt"]

    class Response:
        def __init__(self):
            self.calls = 0

        def process(self, *args, **kwargs):
            self.calls += 1
            return Result()

    detector = Detector()
    response = Response()

    runtime._detector = detector
    runtime._database = DB()
    runtime._response = response

    runtime._handle_file_event({
        "action": "modified",
        "path": "a.txt",
    })

    assert response.calls == 1
    assert runtime._last_analysis is not None

    # The detector's window must NOT be wiped after a response. Clearing it
    # destroyed the evidence the burst had accumulated, so a burst climbing
    # towards CRITICAL was reset to zero and never reached the incident
    # threshold. The sliding window ages events out on its own.
    assert getattr(detector, "reset_called", False) is False

    # The same ongoing burst must not be responded to a second time: a burst
    # stays dangerous for every event inside its window, so without
    # deduplication one burst raised duplicate scores, alerts and incidents.
    runtime._handle_file_event({
        "action": "modified",
        "path": "b.txt",
    })

    assert response.calls == 1


def test_runtime_escalating_burst_is_responded_to_once_per_band(runtime):
    """A burst that escalates WARNING -> CRITICAL is responded to twice at
    most: once for each severity band. CRITICAL is recorded once, so at most
    one incident is created for one burst."""

    class Level:
        def __init__(self, value):
            self.value = value

    class Score:
        def __init__(self, level):
            self.total = 95 if level == "CRITICAL" else 65
            self.level = Level(level)

    class Analysis:
        def __init__(self, level):
            self._level = level
            self.score = Score(level)
            self.dangerous_burst = True
            self.affected_files = ["a.txt"]
            self.suspect_process = {"pid": 1}

        def to_dict(self):
            return {
                "score": self.score.total,
                "level": self._level,
                "signals": ["rapid_encryption"],
                "dangerous_burst": True,
                "affected_files": self.affected_files,
                "suspect_process": self.suspect_process,
                "metrics": {},
            }

    class Detector:
        def __init__(self):
            self.level = "WARNING"

        def ingest_event(self, event):
            return Analysis(self.level)

    class DB:
        pass

    class Result:
        score_id = 1
        alert_id = 2
        incident_id = 3
        quarantined_files = []

    class Response:
        def __init__(self):
            self.levels = []

        def process(self, *args, **kwargs):
            self.levels.append(kwargs.get("reason"))
            return Result()

    detector = Detector()
    response = Response()

    runtime._detector = detector
    runtime._database = DB()
    runtime._response = response

    # WARNING band: responded to once.
    runtime._handle_file_event({"action": "modified", "path": "a.txt"})
    assert len(response.levels) == 1

    # Still WARNING: must not repeat.
    runtime._handle_file_event({"action": "modified", "path": "b.txt"})
    assert len(response.levels) == 1

    # Escalation to CRITICAL is a new band and is recorded.
    detector.level = "CRITICAL"
    runtime._handle_file_event({"action": "modified", "path": "c.txt"})
    assert len(response.levels) == 2

    # Still CRITICAL: must not repeat, so no second incident.
    runtime._handle_file_event({"action": "modified", "path": "d.txt"})
    assert len(response.levels) == 2


def test_runtime_process_monitor_loop_one_cycle(runtime, monkeypatch):
    class Snapshot:
        pid = 4321
        name = "probe"

    snapshot = Snapshot()

    class ProcessMonitor:
        def capture_all(self):
            return [snapshot]

    class DB:
        def __init__(self):
            self.recorded = []

        def record_process(self, value):
            self.recorded.append(value)

    class Detector:
        def update_processes(self, snapshots):
            self.snapshots = snapshots

    # The loop captures *before* it waits, so a stop signal raised on the first
    # wait means exactly one capture cycle has run.
    waits = iter([True])

    monkeypatch.setattr(
        runtime._process_stop,
        "wait",
        lambda _timeout: next(waits),
    )

    db = DB()
    detector = Detector()

    runtime.state = RuntimeStatus.RUNNING
    runtime._database = db
    runtime._process_monitor = ProcessMonitor()
    runtime._detector = detector

    runtime._process_monitor_loop()

    assert db.recorded == [snapshot]
    assert detector.snapshots == [snapshot]


def test_runtime_process_monitor_loop_record_process_failure(
    runtime,
    monkeypatch,
):
    class Snapshot:
        pass

    snapshot = Snapshot()

    class ProcessMonitor:
        def capture_all(self):
            return [snapshot]

    class DB:
        def record_process(self, value):
            raise RuntimeError("record failed")

    class Detector:
        def update_processes(self, snapshots):
            self.snapshots = snapshots

    waits = iter([False, True])

    monkeypatch.setattr(
        runtime._process_stop,
        "wait",
        lambda _timeout: next(waits),
    )

    runtime.state = RuntimeStatus.RUNNING
    runtime._database = DB()
    runtime._process_monitor = ProcessMonitor()
    runtime._detector = Detector()

    runtime._process_monitor_loop()

    assert runtime.state == RuntimeStatus.RUNNING


def test_runtime_process_monitor_loop_exception(
    runtime,
    monkeypatch,
):
    class ProcessMonitor:
        def capture_all(self):
            raise RuntimeError("telemetry failed")

    waits = iter([False, True])

    monkeypatch.setattr(
        runtime._process_stop,
        "wait",
        lambda _timeout: next(waits),
    )

    runtime.state = RuntimeStatus.RUNNING
    runtime._database = object()
    runtime._process_monitor = ProcessMonitor()

    runtime._process_monitor_loop()

    assert runtime._subsystems["process_monitor"]["healthy"] is False


@pytest.mark.parametrize(
    "recovery", ["batch", "fallback", "empty", "partial", "write_error", "analysis_error"]
)
def test_process_monitor_health_requires_confirmed_recovery(runtime, monkeypatch, recovery):
    from types import SimpleNamespace
    from sqlalchemy.exc import OperationalError

    snapshot = SimpleNamespace(pid=123, cpu_percent=0.0)
    lock_error = OperationalError("INSERT INTO processes", {}, Exception("database is locked"))

    class Monitor:
        def capture_all(self):
            return [] if recovery == "empty" else [snapshot]

    class DB:
        attempts = 0
        persisted = []

        def record_processes(self, snapshots):
            self.attempts += 1
            if self.attempts == 1 or recovery == "write_error":
                raise lock_error
            if recovery in {"fallback", "partial"}:
                raise RuntimeError("batch unavailable")
            self.persisted.extend(snapshots)
            return len(snapshots)

        def record_process(self, snapshot):
            if recovery == "partial":
                raise RuntimeError("record failed")
            self.persisted.append(snapshot)

    class Detector:
        last_analysis = None

        def update_processes(self, snapshots):
            if recovery == "analysis_error":
                raise RuntimeError("analysis failed")

    db = DB()
    runtime.state = RuntimeStatus.RUNNING
    runtime._database = db
    runtime._process_monitor = Monitor()
    runtime._detector = Detector()
    monkeypatch.setattr(runtime._process_stop, "wait", lambda _: True)

    if recovery == "empty":
        runtime._register_subsystem("process_monitor", healthy=False, critical=False,
                                    details={"error": "database is locked"})
    else:
        runtime._process_monitor_loop()
    assert runtime._subsystems["process_monitor"]["healthy"] is False
    assert "database is locked" in runtime._subsystems["process_monitor"]["details"]["error"]

    runtime._process_monitor_loop()
    health = runtime._subsystems["process_monitor"]
    if recovery in {"batch", "fallback"}:
        assert db.persisted == [snapshot]
        assert health == {"name": "process_monitor", "healthy": True,
                          "critical": False, "details": {}}
        assert runtime.health_status()["healthy"] is True
    else:
        assert health["healthy"] is False
        assert "error" in health["details"]


def test_runtime_start_failure_cleanup_branches(runtime, monkeypatch):
    class Config:
        persistence_enabled = True
        db_path = runtime._pending_db_path
        data_dir = runtime._pending_data_dir
        log_level = "INFO"

    runtime._config = Config()

    def fail_pipeline():
        raise RuntimeError("pipeline failure")

    monkeypatch.setattr(
        runtime,
        "_create_detection_pipeline",
        fail_pipeline,
    )

    with pytest.raises(RuntimeError, match="Failed to initialize"):
        runtime.start()

    assert runtime.state == RuntimeStatus.FAILED


def test_runtime_start_raises_when_failed_after_cleanup(runtime):
    runtime.state = RuntimeStatus.FAILED

    with pytest.raises(RuntimeError, match="FAILED"):
        runtime.start()


def test_runtime_cleanup_monitor_stop_exception(runtime):
    class Monitor:
        def stop(self):
            raise RuntimeError("monitor stop failed")

    runtime._monitor = Monitor()
    runtime._database = None
    runtime._cleanup()

    assert runtime._monitor is None


def test_runtime_force_stop_monitor_exception(runtime):
    class Monitor:
        def stop(self):
            raise RuntimeError("monitor stop failed")

    runtime._monitor = Monitor()
    runtime._database = None

    runtime.force_stop()

    assert runtime.state == RuntimeStatus.STOPPED
    assert runtime._monitor is None


def test_runtime_force_stop_database_close_exception(runtime):
    class DB:
        def close(self):
            raise RuntimeError("database close failed")

    runtime._database = DB()

    runtime.force_stop()

    assert runtime._database is None
    assert runtime.state == RuntimeStatus.STOPPED


def test_runtime_close_exception_falls_back_to_force_stop(
    runtime,
    monkeypatch,
):
    called = []

    def fail_stop():
        raise RuntimeError("stop failed")

    def force_stop():
        called.append(True)

    monkeypatch.setattr(runtime, "stop", fail_stop)
    monkeypatch.setattr(runtime, "force_stop", force_stop)

    runtime.state = RuntimeStatus.RUNNING
    runtime.close()

    assert called == [True]


def test_runtime_handle_signal_runtime_error(runtime, monkeypatch):
    def fail_stop():
        raise RuntimeError("stop failed")

    monkeypatch.setattr(runtime, "stop", fail_stop)

    runtime.state = RuntimeStatus.RUNNING
    runtime.handle_signal(signal.SIGTERM)

    assert runtime.state == RuntimeStatus.SHUTTING_DOWN



def test_runtime_process_loop_skips_database_after_wait(runtime, monkeypatch):
    waits = iter([False, True])

    monkeypatch.setattr(
        runtime._process_stop,
        "wait",
        lambda _timeout: next(waits),
    )

    runtime.state = RuntimeStatus.RUNNING
    runtime._database = None
    runtime._process_monitor = object()

    runtime._process_monitor_loop()

    assert runtime.state == RuntimeStatus.RUNNING


def test_runtime_process_loop_skips_process_monitor_after_wait(
    runtime,
    monkeypatch,
):
    waits = iter([False, True])

    monkeypatch.setattr(
        runtime._process_stop,
        "wait",
        lambda _timeout: next(waits),
    )

    runtime.state = RuntimeStatus.RUNNING
    runtime._database = object()
    runtime._process_monitor = None

    runtime._process_monitor_loop()

    assert runtime.state == RuntimeStatus.RUNNING


def test_runtime_process_loop_skips_when_degraded_without_database(
    runtime,
    monkeypatch,
):
    waits = iter([False, True])

    monkeypatch.setattr(
        runtime._process_stop,
        "wait",
        lambda _timeout: next(waits),
    )

    runtime.state = RuntimeStatus.DEGRADED
    runtime._database = None
    runtime._process_monitor = object()

    runtime._process_monitor_loop()

    assert runtime.state == RuntimeStatus.DEGRADED


def test_runtime_start_failure_monitor_stop_and_db_state_errors(
    runtime,
    monkeypatch,
):
    class Config:
        persistence_enabled = True
        db_path = runtime._pending_db_path
        data_dir = runtime._pending_data_dir
        log_level = "INFO"

    class Monitor:
        def stop(self):
            raise RuntimeError("monitor cleanup failed")

    class DB:
        def record_runtime_state(self, _state):
            raise RuntimeError("state persistence failed")

    runtime._config = Config()
    runtime._monitor = Monitor()
    runtime._database = DB()

    def fail_pipeline():
        raise RuntimeError("pipeline failure")

    monkeypatch.setattr(
        runtime,
        "_create_detection_pipeline",
        fail_pipeline,
    )

    with pytest.raises(RuntimeError, match="Failed to initialize"):
        runtime.start()

    assert runtime.state == RuntimeStatus.FAILED
    assert runtime._monitor is None


def test_runtime_start_failure_process_monitor_cleanup_error(
    runtime,
    monkeypatch,
):
    class Config:
        persistence_enabled = True
        db_path = runtime._pending_db_path
        data_dir = runtime._pending_data_dir
        log_level = "INFO"

    runtime._config = Config()

    def fail_pipeline():
        raise RuntimeError("pipeline failure")

    def fail_process_cleanup():
        raise RuntimeError("process cleanup failed")

    monkeypatch.setattr(
        runtime,
        "_create_detection_pipeline",
        fail_pipeline,
    )
    monkeypatch.setattr(
        runtime,
        "_stop_process_monitor",
        fail_process_cleanup,
    )

    with pytest.raises(RuntimeError, match="Failed to initialize"):
        runtime.start()

    assert runtime.state == RuntimeStatus.FAILED


def test_runtime_start_failure_database_state_persistence_error(
    runtime,
    monkeypatch,
):
    class Config:
        persistence_enabled = True
        db_path = runtime._pending_db_path
        data_dir = runtime._pending_data_dir
        log_level = "INFO"

    class DB:
        def record_runtime_state(self, _state):
            raise RuntimeError("state write failed")

    runtime._config = Config()
    runtime._database = DB()

    def fail_pipeline():
        raise RuntimeError("pipeline failure")

    monkeypatch.setattr(
        runtime,
        "_create_detection_pipeline",
        fail_pipeline,
    )
    monkeypatch.setattr(
        runtime,
        "_stop_process_monitor",
        lambda: None,
    )

    with pytest.raises(RuntimeError, match="Failed to initialize"):
        runtime.start()

    assert runtime.state == RuntimeStatus.FAILED


def test_runtime_start_failure_raises_final_error(
    runtime,
    monkeypatch,
):
    def fail_apply(**_kwargs):
        raise RuntimeError("forced startup failure")

    monkeypatch.setattr(
        runtime,
        "_apply_runtime_settings",
        fail_apply,
    )
    runtime._config = None

    with pytest.raises(
        RuntimeError,
        match="Failed to initialize runtime",
    ):
        runtime.start()

    assert runtime.state == RuntimeStatus.FAILED


def test_runtime_handle_signal_runtime_error_branch(
    runtime,
    monkeypatch,
):
    def fail_stop():
        raise RuntimeError("stop failed")

    monkeypatch.setattr(
        runtime,
        "stop",
        fail_stop,
    )

    runtime.state = RuntimeStatus.RUNNING

    runtime.handle_signal(signal.SIGTERM)

    assert runtime.state == RuntimeStatus.SHUTTING_DOWN


def test_runtime_handle_signal_value_error_propagates(
    runtime,
    monkeypatch,
):
    def fail_stop():
        raise ValueError("unexpected")

    monkeypatch.setattr(
        runtime,
        "stop",
        fail_stop,
    )

    runtime.state = RuntimeStatus.RUNNING

    with pytest.raises(ValueError, match="unexpected"):
        runtime.handle_signal(signal.SIGINT)

    assert runtime.state == RuntimeStatus.RUNNING


def test_runtime_process_monitor_loop_skips_non_running_state(
    runtime,
    monkeypatch,
):
    waits = iter([False, True])

    monkeypatch.setattr(
        runtime._process_stop,
        "wait",
        lambda _timeout: next(waits),
    )

    runtime.state = RuntimeStatus.STOPPED
    runtime._database = object()
    runtime._process_monitor = object()

    runtime._process_monitor_loop()

    assert runtime.state == RuntimeStatus.STOPPED


def test_runtime_stop_when_shutting_down(runtime):
    runtime.state = RuntimeStatus.SHUTTING_DOWN

    with pytest.raises(
        RuntimeError,
        match="already shutting down",
    ):
        runtime.stop()


def test_runtime_status_ignores_persisted_state_load_failure(
    runtime,
    monkeypatch,
):
    runtime.state = RuntimeStatus.RUNNING

    def fail_load():
        raise RuntimeError("persisted state failure")

    monkeypatch.setattr(
        runtime,
        "_load_persisted_state",
        fail_load,
    )

    result = runtime.status()

    assert result["state"] == "RUNNING"
    assert result["healthy"] is True
    assert result["threat"]["score"] == 0

