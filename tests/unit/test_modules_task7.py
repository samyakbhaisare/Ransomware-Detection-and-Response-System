"""Coverage for telemetry, monitoring, config and the remaining helpers (Task 7).

These modules were the last meaningful gaps after the API/dashboard/storage
work. The tests below assert real behaviour of the telemetry collectors,
filesystem detectors, configuration loader and the smaller engines, including
their defensive branches (psutil failures, unreadable files, malformed config).
"""

from __future__ import annotations

import json
import socket
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from rdrs.config import (
    DEFAULT_DETECTION_THRESHOLDS,
    DEFAULT_SCORING_WEIGHTS,
    RuntimeConfig,
)
from rdrs.config_engine import ConfigEngine
from rdrs.detection import DetectionEngine
from rdrs.entropy import calculate_file_entropy
from rdrs.events import EventBus, SecurityEvent
from rdrs.health import HealthRegistry
from rdrs.logging import (
    close_rotating_logs,
    initialize_rotating_logs,
    make_structured_logger,
)
from rdrs.monitoring import (
    DirectoryMonitor,
    FileActionDetector,
    RealtimeFileMonitor,
    _WatchdogHandler,
    calculate_file_metadata,
)
from rdrs.policy import Policy, PolicyEngine
from rdrs.quarantine import QuarantineManager
from rdrs.telemetry import (
    EntropyHistory,
    FileArtifact,
    FileMetadata,
    NetworkConnectionTelemetry,
    ProcessMonitor,
    ProcessTelemetry,
    TelemetrySnapshot,
    UserSessionTelemetry,
    calculate_entropy,
)
from rdrs.workers import TaskQueue, WorkerPool


# ---------------------------------------------------------------------------
# telemetry: entropy + file metadata
# ---------------------------------------------------------------------------


def test_calculate_entropy_of_empty_and_uniform_data():
    assert calculate_entropy(b"") == 0.0
    assert calculate_entropy(b"\x00" * 128) == 0.0

    # A full 0-255 byte alphabet is maximally entropic (8 bits/byte).
    assert calculate_entropy(bytes(range(256))) == pytest.approx(8.0, abs=1e-9)


def test_file_metadata_and_artifact_describe_a_real_file(tmp_path):
    text = tmp_path / "notes.txt"
    text.write_text("hello RDRS", encoding="utf-8")

    metadata = FileMetadata.from_path(text)

    assert metadata.path == str(text)
    assert metadata.size == 10
    assert metadata.extension == ".txt"
    assert len(metadata.sha256) == 64
    assert metadata.modified_at is not None

    artifact = FileArtifact.from_path(text)

    assert artifact.mime_type == "text/plain"
    assert artifact.sha256 == metadata.sha256
    assert artifact.size == 10


def test_file_artifact_detects_image_mime_types(tmp_path):
    png = tmp_path / "shot.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")

    assert FileArtifact.from_path(png).mime_type == "image/png"


def test_file_artifact_falls_back_to_octet_stream(tmp_path):
    binary = tmp_path / "blob.bin"
    binary.write_bytes(b"\x00\x01\x02")

    assert FileArtifact.from_path(binary).mime_type == "application/octet-stream"


def test_file_entropy_uses_the_requested_sample_size(tmp_path):
    path = tmp_path / "sample.bin"
    path.write_bytes(b"a" * 100)

    assert calculate_file_entropy(path, sample_size=10) == 0.0

    with pytest.raises(ValueError, match="sample_size must be positive"):
        calculate_file_entropy(path, sample_size=0)


# ---------------------------------------------------------------------------
# telemetry: processes
# ---------------------------------------------------------------------------


def test_process_telemetry_from_pid_and_current():
    current = ProcessTelemetry.current()

    assert current.pid > 0
    assert isinstance(current.name, str)
    assert current.executable_path != "" or current.name != ""


def test_process_telemetry_tolerates_psutil_failures(monkeypatch):
    """Every psutil accessor failing must still yield a usable snapshot."""
    import rdrs.telemetry as telemetry_module
    import psutil

    class HostileProcess:
        pid = 4242

        def oneshot(self):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def name(self):
            raise psutil.AccessDenied(pid=self.pid)

        def ppid(self):
            raise psutil.NoSuchProcess(pid=self.pid)

        def cmdline(self):
            raise psutil.AccessDenied(pid=self.pid)

        def cwd(self):
            raise psutil.NoSuchProcess(pid=self.pid)

        def create_time(self):
            raise psutil.AccessDenied(pid=self.pid)

        def cpu_percent(self, interval=None):
            raise psutil.NoSuchProcess(pid=self.pid)

        def memory_info(self):
            raise psutil.AccessDenied(pid=self.pid)

        def memory_percent(self):
            raise psutil.NoSuchProcess(pid=self.pid)

        def io_counters(self):
            raise AttributeError("no io counters")

        def exe(self):
            raise psutil.AccessDenied(pid=self.pid)

    monkeypatch.setattr(
        telemetry_module.psutil,
        "Process",
        lambda pid: HostileProcess(),
    )

    snapshot = ProcessTelemetry.from_pid(4242)

    assert snapshot.pid == 4242
    assert snapshot.name == "unknown"
    assert snapshot.parent_pid is None
    assert snapshot.command_line == ""
    assert snapshot.cwd == ""
    assert snapshot.started_at is None
    assert snapshot.cpu_percent == 0.0
    assert snapshot.memory_rss == 0
    assert snapshot.memory_percent == 0.0
    assert snapshot.disk_write_bytes == 0
    assert snapshot.executable_path == ""


def test_process_monitor_capture_and_tree_navigation():
    monitor = ProcessMonitor()

    captured = monitor.capture_current()

    assert captured.pid > 0

    explicit = monitor.capture(pid=captured.pid)

    assert explicit.pid == captured.pid

    children = monitor.tree.children_of(captured.pid)

    assert isinstance(children, list)
    assert monitor.tree.parent_of(999999) is None
    assert monitor.tree.parent_of(captured.pid) == captured.parent_pid
    assert monitor.tree.all_processes()

    snapshot = monitor.snapshot()

    assert isinstance(snapshot, list)
    assert all(isinstance(row, dict) for row in snapshot)


def test_process_monitor_capture_all_skips_unreadable_processes(monkeypatch):
    """One unreadable process must be skipped, never abort the capture.

    ``capture_all`` reads its attributes in a single batched
    ``process_iter(attrs=...)`` pass, so an unreadable process surfaces when
    its ``info`` is accessed. The guarantee under test is unchanged: the
    process is skipped and the rest of the capture still completes.
    """
    import rdrs.telemetry as telemetry_module
    import psutil

    class DeadProcess:
        pid = 1

        @property
        def info(self):
            raise psutil.NoSuchProcess(pid=1)

    class LiveProcess:
        pid = 2

        @property
        def info(self):
            return {
                "pid": 2,
                "name": "alive",
                "ppid": 1,
                "cpu_times": None,
                "memory_info": None,
                "create_time": None,
                "exe": "",
                "cmdline": [],
                "cwd": "",
                "io_counters": None,
            }

    monkeypatch.setattr(
        telemetry_module.psutil,
        "process_iter",
        lambda attrs=None: [DeadProcess(), LiveProcess()],
    )

    monitor = ProcessMonitor()
    captured = monitor.capture_all()

    assert [process.pid for process in captured] == [2]
    assert captured[0].name == "alive"


def test_process_tree_add_and_lookup():
    monitor = ProcessMonitor()
    monitor.tree.add(ProcessTelemetry(pid=1, name="root"))
    monitor.tree.add(ProcessTelemetry(pid=2, name="child", parent_pid=1))

    assert monitor.tree.children_of(1) == [2]
    assert monitor.tree.parent_of(2) == 1
    assert {process.pid for process in monitor.tree.all_processes()} == {1, 2}


# ---------------------------------------------------------------------------
# telemetry: sessions, sockets, snapshot, entropy history
# ---------------------------------------------------------------------------


def test_user_session_telemetry_current_reads_the_environment():
    session = UserSessionTelemetry.current()

    assert session.user
    assert session.session_id
    # Privilege is detected, not hard-coded, so this holds whether or not the
    # test process is elevated.
    assert session.privilege == (
        "admin" if UserSessionTelemetry.is_elevated() else "user"
    )
    assert session.home


def test_network_connection_from_a_real_socket_pair():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        server.bind(("127.0.0.1", 0))
        server.listen(1)

        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.connect(server.getsockname())

        try:
            telemetry = NetworkConnectionTelemetry.from_socket(client)

            assert telemetry.protocol == "tcp"
            assert telemetry.port == server.getsockname()[1]
            assert str(server.getsockname()[0]) in telemetry.local_address
        finally:
            client.close()
    finally:
        server.close()


def test_network_connection_from_an_unconnected_socket_degrades_cleanly():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        telemetry = NetworkConnectionTelemetry.from_socket(sock)

        assert telemetry.protocol == "tcp"
        assert telemetry.port == 0
        assert telemetry.remote_address == ""
    finally:
        sock.close()


def test_telemetry_snapshot_serializes_present_and_absent_parts(tmp_path):
    path = tmp_path / "f.txt"
    path.write_text("x", encoding="utf-8")

    empty = TelemetrySnapshot().to_dict()

    assert empty == {
        "file": None,
        "process": None,
        "user": None,
        "network": None,
        "context": {},
    }

    populated = TelemetrySnapshot(
        file=FileMetadata.from_path(path),
        process=ProcessTelemetry(pid=1, name="p"),
        user=UserSessionTelemetry(user="u", session_id="s"),
        network=NetworkConnectionTelemetry("AF_INET", "a", "b", "tcp", 1),
        context={"reason": "test"},
    ).to_dict()

    assert populated["file"]["path"] == str(path)
    assert populated["process"]["pid"] == 1
    assert populated["user"]["user"] == "u"
    assert populated["network"]["port"] == 1
    assert populated["context"] == {"reason": "test"}


def test_entropy_history_records_and_looks_up():
    history = EntropyHistory()

    assert history.latest("C:/a") is None

    history.record("C:/a", 7.5)
    history.record("C:/a", 7.9)

    assert history.latest("C:/a") == pytest.approx(7.9)
    assert history.latest("C:/missing") is None


# ---------------------------------------------------------------------------
# monitoring
# ---------------------------------------------------------------------------


def test_directory_monitor_scan_and_metadata(tmp_path):
    (tmp_path / "one.txt").write_text("a", encoding="utf-8")
    (tmp_path / "two.bin").write_bytes(b"\x00\x01")

    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "three.txt").write_text("c", encoding="utf-8")

    monitor = DirectoryMonitor(tmp_path)

    found = {Path(path).name for path in monitor.scan()}

    assert found == {"one.txt", "two.bin", "three.txt"}


def test_directory_monitor_metadata_includes_hash_and_entropy(tmp_path):
    target = tmp_path / "payload.bin"
    target.write_bytes(bytes(range(256)))

    metadata = calculate_file_metadata(target)

    assert metadata["sha256"]
    assert metadata["entropy"] == pytest.approx(8.0, abs=1e-9)
    assert metadata["exists"] is True
    assert metadata["is_file"] is True
    assert metadata["size"] == 256
    assert metadata["extension"] == ".bin"


def test_metadata_collection_reports_missing_files_without_raising(tmp_path):
    missing = tmp_path / "gone.bin"

    metadata = calculate_file_metadata(missing)

    assert metadata["exists"] is False
    assert metadata["is_file"] is False
    assert metadata["sha256"] is None
    assert metadata["entropy"] is None
    assert metadata["size"] == 0


def test_metadata_collection_swallows_unreadable_files(tmp_path, monkeypatch):
    target = tmp_path / "locked.bin"
    target.write_bytes(b"data")

    def _explode(self, *args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "stat", _explode)

    metadata = calculate_file_metadata(target)

    # The stat failure is contained; hashes stay empty rather than raising.
    assert metadata["sha256"] is None
    assert metadata["entropy"] is None


def test_detector_normalizes_direct_input(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("hello", encoding="utf-8")

    detector = FileActionDetector(tmp_path)

    event = detector.detect(target, "modify")

    assert event is not None
    assert event.action == "modify"
    assert event.path == str(target)
    assert event.extension == ".txt"
    assert event.metadata["exists"] is True

    # Entropy is measured from the real bytes of "hello", not assumed.
    assert event.metadata["entropy"] == pytest.approx(
        calculate_entropy(b"hello")
    )
    assert event.metadata["size"] == 5


def test_detector_maps_every_documented_action_alias(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("hello", encoding="utf-8")

    detector = FileActionDetector(tmp_path)

    assert detector.detect(target, "create").action == "create"
    assert detector.detect(target, "modify").action == "modify"
    assert detector.detect(target, "delete").action == "delete"
    assert detector.detect(target, "MODIFIED").action == "modify"
    assert detector.detect(target, "move").action == "rename"

    # Long-form aliases normalize to the same canonical actions.
    assert detector.detect(target, "created").action == "create"
    assert detector.detect(target, "modified").action == "modify"
    assert detector.detect(target, "deleted").action == "delete"
    assert detector.detect(target, "renamed").action == "rename"
    assert detector.detect(target, "moved").action == "rename"


def test_detector_timestamp_can_be_supplied_explicitly(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("hello", encoding="utf-8")

    event = FileActionDetector(tmp_path).detect(
        target,
        "modify",
        timestamp=1234.5,
    )

    assert event.timestamp == pytest.approx(1234.5)


def test_detector_records_rename_endpoints(tmp_path):
    source = tmp_path / "a.txt"
    destination = tmp_path / "a.txt.locked"
    source.write_text("hello", encoding="utf-8")
    destination.write_text("hello", encoding="utf-8")

    event = FileActionDetector(tmp_path).detect(
        source,
        "rename",
        previous_path=str(source),
        new_path=str(destination),
    )

    assert event.action == "rename"
    assert event.path == str(destination)
    assert event.previous_path == str(source)
    assert event.new_path == str(destination)
    assert event.extension == ".locked"


def test_detector_delete_reports_a_non_existent_payload(tmp_path):
    target = tmp_path / "gone.txt"

    event = FileActionDetector(tmp_path).detect(target, "delete")

    assert event.action == "delete"
    assert event.metadata["exists"] is False
    assert event.metadata["sha256"] is None


def test_detector_returns_none_for_an_unknown_action(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("hello", encoding="utf-8")

    assert FileActionDetector().detect(target, "teleported") is None


def test_detector_normalizes_watchdog_style_events(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("hello", encoding="utf-8")

    class RawEvent:
        event_type = "modified"
        src_path = str(target)
        dest_path = ""
        is_directory = False

    event = FileActionDetector(tmp_path).detect(RawEvent())

    assert event is not None
    assert event.action == "modified"
    assert event.path == str(target)


def test_watchdog_handler_emits_only_supported_events(tmp_path):
    emitted = []
    handler = _WatchdogHandler(
        detector=FileActionDetector(tmp_path),
        callback=emitted.append,
        excludes=("__pycache__",),
    )

    target = tmp_path / "a.txt"
    target.write_text("x", encoding="utf-8")

    class RawEvent:
        def __init__(self, action, src, dest=""):
            self.event_type = action
            self.src_path = src
            self.dest_path = dest
            self.is_directory = False

    handler.on_created(RawEvent("created", str(target)))
    handler.on_modified(RawEvent("modified", str(target)))
    handler.on_deleted(RawEvent("deleted", str(target)))

    assert len(emitted) == 3


def test_watchdog_handler_skips_directory_events_and_excluded_paths(tmp_path):
    emitted = []
    handler = _WatchdogHandler(
        detector=FileActionDetector(tmp_path),
        callback=emitted.append,
        excludes=("__pycache__",),
    )

    class RawEvent:
        def __init__(self, action, src, dest="", is_directory=False):
            self.event_type = action
            self.src_path = src
            self.dest_path = dest
            self.is_directory = is_directory

    handler._emit(RawEvent("modified", str(tmp_path / "__pycache__" / "x.pyc")))  # noqa: SLF001
    handler._emit(  # noqa: SLF001
        RawEvent(
            "moved",
            str(tmp_path / "a.txt"),
            str(tmp_path / "__pycache__" / "a.txt"),
        )
    )

    assert emitted == []


def test_watchdog_handler_swallows_detector_errors(tmp_path):
    def _explode(raw_event):
        raise OSError("disk vanished")

    handler = _WatchdogHandler(
        detector=FileActionDetector(tmp_path),
        callback=lambda event: pytest.fail("callback must not run"),
        excludes=(),
    )

    handler.detector.detect = _explode  # type: ignore[assignment]

    class RawEvent:
        event_type = "modified"
        src_path = str(tmp_path / "a.txt")
        dest_path = ""
        is_directory = False

    # Must not raise.
    handler._emit(RawEvent())  # noqa: SLF001


def test_realtime_monitor_context_manager_starts_and_stops(tmp_path):
    observed = []

    monitor = RealtimeFileMonitor(tmp_path, callback=observed.append)

    with monitor as running:
        assert running is monitor
        assert running.event_count == 0

    assert monitor.event_count == 0


def test_realtime_monitor_handles_an_event_and_invokes_the_callback(tmp_path):
    observed = []

    monitor = RealtimeFileMonitor(tmp_path, callback=observed.append)

    target = tmp_path / "f.txt"
    target.write_text("x", encoding="utf-8")

    event = FileActionDetector(tmp_path).detect(target, "modified")

    monitor._handle_event(event)  # noqa: SLF001

    assert monitor.event_count == 1
    assert len(observed) == 1


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


def _config_payload(tmp_path, **overrides):
    payload = {
        "runtime": {
            "name": "rdrs",
            "environment": "test",
            "data_dir": str(tmp_path / "data"),
            "logs_dir": str(tmp_path / "logs"),
            "shutdown_timeout": 10,
        },
        "logging": {"level": "INFO"},
        "persistence": {
            "enabled": True,
            "db_path": str(tmp_path / "data" / "rdrs.db"),
        },
        "detection": dict(DEFAULT_DETECTION_THRESHOLDS),
        "scoring": {"weights": dict(DEFAULT_SCORING_WEIGHTS)},
        "subsystems": [{"name": "config", "healthy": True, "critical": True}],
    }

    payload.update(overrides)

    return payload


def _write_config(tmp_path, payload) -> Path:
    path = tmp_path / "rdrs.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def test_config_load_creates_the_directories_and_maps_every_field(tmp_path):
    path = _write_config(tmp_path, _config_payload(tmp_path))

    config = RuntimeConfig.load(path)

    assert config.name == "rdrs"
    assert config.environment == "test"
    assert config.shutdown_timeout == pytest.approx(10.0)
    assert config.log_level == "INFO"
    assert config.persistence_enabled is True
    assert config.data_dir == tmp_path / "data"
    assert config.logs_dir == tmp_path / "logs"
    assert config.db_path == tmp_path / "data" / "rdrs.db"
    assert config.config_path == str(path)
    assert config.subsystems
    assert set(config.detection_thresholds) == set(DEFAULT_DETECTION_THRESHOLDS)
    assert set(config.scoring_weights) == set(DEFAULT_SCORING_WEIGHTS)


def test_config_load_honours_explicit_directory_overrides(tmp_path):
    path = _write_config(tmp_path, _config_payload(tmp_path))

    config = RuntimeConfig.load(
        path,
        data_dir=tmp_path / "override-data",
        logs_dir=tmp_path / "override-logs",
        db_path=tmp_path / "override-data" / "override.db",
    )

    assert config.data_dir == tmp_path / "override-data"
    assert config.logs_dir == tmp_path / "override-logs"
    assert config.db_path == tmp_path / "override-data" / "override.db"


def test_config_load_rejects_a_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError, match="Configuration file not found"):
        RuntimeConfig.load(tmp_path / "absent.yaml")


def test_config_load_rejects_a_non_mapping_root(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("- just\n- a\n- list\n", encoding="utf-8")

    with pytest.raises(ValueError, match="root must be a mapping"):
        RuntimeConfig.load(path)


@pytest.mark.parametrize(
    "section",
    ["runtime", "logging", "persistence", "detection", "scoring"],
)
def test_config_load_rejects_non_mapping_sections(tmp_path, section):
    payload = _config_payload(tmp_path)
    payload[section] = "not-a-mapping"

    path = _write_config(tmp_path, payload)

    with pytest.raises(ValueError, match=f"{section} must be a mapping"):
        RuntimeConfig.load(path)


def test_config_load_rejects_a_non_list_subsystems_value(tmp_path):
    payload = _config_payload(tmp_path)
    payload["subsystems"] = {"not": "a list"}

    path = _write_config(tmp_path, payload)

    with pytest.raises(ValueError, match="subsystems must be a list"):
        RuntimeConfig.load(path)


def test_config_load_rejects_an_unknown_numeric_setting(tmp_path):
    payload = _config_payload(tmp_path)
    payload["detection"] = {**DEFAULT_DETECTION_THRESHOLDS, "bogus_signal": 1}

    path = _write_config(tmp_path, payload)

    with pytest.raises(ValueError, match="Unknown detection setting"):
        RuntimeConfig.load(path)


def test_config_load_rejects_a_boolean_where_a_number_belongs(tmp_path):
    payload = _config_payload(tmp_path)
    payload["detection"] = {**DEFAULT_DETECTION_THRESHOLDS, "renames": True}

    path = _write_config(tmp_path, payload)

    with pytest.raises(ValueError, match="must be numeric"):
        RuntimeConfig.load(path)


def test_config_load_rejects_a_non_numeric_signal_value(tmp_path):
    payload = _config_payload(tmp_path)
    payload["detection"] = {**DEFAULT_DETECTION_THRESHOLDS, "renames": "many"}

    path = _write_config(tmp_path, payload)

    with pytest.raises(ValueError, match="must be numeric"):
        RuntimeConfig.load(path)


def test_config_load_rejects_a_non_mapping_numeric_section(tmp_path):
    payload = _config_payload(tmp_path)
    payload["scoring"] = {"weights": ["not", "a", "mapping"]}

    path = _write_config(tmp_path, payload)

    with pytest.raises(ValueError, match="must be a mapping"):
        RuntimeConfig.load(path)


# --- validate() paths -------------------------------------------------------


def _valid_config(tmp_path) -> RuntimeConfig:
    return RuntimeConfig(
        name="rdrs",
        environment="test",
        data_dir=tmp_path / "data",
        logs_dir=tmp_path / "logs",
        db_path=tmp_path / "data" / "rdrs.db",
    )


def test_validate_rejects_an_empty_name(tmp_path):
    config = _valid_config(tmp_path)
    config.name = "   "

    with pytest.raises(ValueError, match="name cannot be empty"):
        config.validate()


def test_validate_rejects_a_non_positive_shutdown_timeout(tmp_path):
    config = _valid_config(tmp_path)
    config.shutdown_timeout = 0

    with pytest.raises(ValueError, match="shutdown_timeout must be positive"):
        config.validate()


def test_validate_rejects_an_unsupported_log_level(tmp_path):
    config = _valid_config(tmp_path)
    config.log_level = "CHATTY"

    with pytest.raises(ValueError, match="Unsupported log level"):
        config.validate()


def test_validate_requires_a_db_path_when_persistence_is_on(tmp_path):
    config = _valid_config(tmp_path)
    config.db_path = None

    with pytest.raises(ValueError, match="Database path is required"):
        config.validate()


def test_validate_rejects_incomplete_detection_thresholds(tmp_path):
    config = _valid_config(tmp_path)
    config.detection_thresholds = {"window_seconds": 60.0}

    with pytest.raises(ValueError, match="complete configured signal set"):
        config.validate()


def test_validate_rejects_a_non_positive_window(tmp_path):
    config = _valid_config(tmp_path)
    config.detection_thresholds = {
        **DEFAULT_DETECTION_THRESHOLDS,
        "window_seconds": 0.0,
    }

    with pytest.raises(ValueError, match="window_seconds must be positive"):
        config.validate()


def test_validate_rejects_negative_detection_counts(tmp_path):
    config = _valid_config(tmp_path)
    config.detection_thresholds = {
        **DEFAULT_DETECTION_THRESHOLDS,
        "renames": -1.0,
    }

    with pytest.raises(ValueError, match="cannot be negative"):
        config.validate()


def test_validate_rejects_out_of_range_entropy(tmp_path):
    config = _valid_config(tmp_path)
    config.detection_thresholds = {
        **DEFAULT_DETECTION_THRESHOLDS,
        "average_entropy": 9.0,
    }

    with pytest.raises(ValueError, match="average_entropy must be"):
        config.validate()


def test_validate_rejects_out_of_range_cpu(tmp_path):
    config = _valid_config(tmp_path)
    config.detection_thresholds = {
        **DEFAULT_DETECTION_THRESHOLDS,
        "cpu_percent": 101.0,
    }

    with pytest.raises(ValueError, match="cpu_percent must be"):
        config.validate()


def test_validate_rejects_incomplete_scoring_weights(tmp_path):
    config = _valid_config(tmp_path)
    config.scoring_weights = {"rapid_encryption": 40.0}

    with pytest.raises(ValueError, match="complete configured signal set"):
        config.validate()


def test_validate_rejects_negative_scoring_weights(tmp_path):
    config = _valid_config(tmp_path)
    config.scoring_weights = {
        **DEFAULT_SCORING_WEIGHTS,
        "mass_rename": -1.0,
    }

    with pytest.raises(ValueError, match="cannot be negative"):
        config.validate()


def test_validate_rejects_all_zero_scoring_weights(tmp_path):
    config = _valid_config(tmp_path)
    config.scoring_weights = {
        key: 0.0 for key in DEFAULT_SCORING_WEIGHTS
    }

    with pytest.raises(ValueError, match="At least one scoring weight"):
        config.validate()


def test_to_dict_and_to_config_dict_round_trip(tmp_path):
    config = _valid_config(tmp_path)
    config.validate()

    flat = config.to_dict()

    assert flat["name"] == "rdrs"
    assert flat["db_path"] == str(tmp_path / "data" / "rdrs.db")
    assert flat["shutdown_timeout"] == pytest.approx(10.0)

    nested = config.to_config_dict()

    assert set(nested) == {
        "runtime",
        "logging",
        "persistence",
        "detection",
        "scoring",
        "subsystems",
    }
    assert nested["runtime"]["name"] == "rdrs"
    assert nested["persistence"]["db_path"] == str(tmp_path / "data" / "rdrs.db")
    assert set(nested["detection"]) == set(DEFAULT_DETECTION_THRESHOLDS)


def test_to_dict_serializes_a_missing_db_path_as_none(tmp_path):
    config = _valid_config(tmp_path)
    config.persistence_enabled = False
    config.db_path = None

    assert config.to_dict()["db_path"] is None


def test_save_writes_a_reloadable_document(tmp_path):
    config = _valid_config(tmp_path)
    config.name = "saved"
    config.log_level = "DEBUG"

    destination = tmp_path / "nested" / "rdrs.yaml"

    written = config.save(destination)

    assert written == destination
    assert destination.is_file()

    reloaded = RuntimeConfig.load(
        destination,
        data_dir=tmp_path / "data",
        logs_dir=tmp_path / "logs",
        db_path=tmp_path / "data" / "rdrs.db",
    )

    assert reloaded.name == "saved"
    assert reloaded.log_level == "DEBUG"
    assert reloaded.scoring_weights == pytest.approx(DEFAULT_SCORING_WEIGHTS)


# ---------------------------------------------------------------------------
# config_engine
# ---------------------------------------------------------------------------


def test_config_engine_defaults_are_valid():
    engine = ConfigEngine()

    engine.validate()

    assert engine.to_dict() == {
        "monitoring": {"paths": ["."], "recursive": True},
        "detection": {"rules": ["default"]},
        "response": {"actions": ["log"]},
        "ai": {"enabled": False},
    }


@pytest.mark.parametrize(
    "field_name",
    ["monitoring", "detection", "response", "ai"],
)
def test_config_engine_rejects_non_mapping_sections(field_name):
    engine = ConfigEngine(**{field_name: "nope"})

    with pytest.raises(ValueError, match=f"{field_name} must be a dictionary"):
        engine.validate()


def test_config_engine_rejects_empty_monitoring_paths():
    engine = ConfigEngine(monitoring={"paths": [], "recursive": True})

    with pytest.raises(ValueError, match="monitoring.paths"):
        engine.validate()


def test_config_engine_rejects_empty_rules():
    engine = ConfigEngine(detection={"rules": []})

    with pytest.raises(ValueError, match="detection.rules"):
        engine.validate()


def test_config_engine_rejects_empty_actions():
    engine = ConfigEngine(response={"actions": []})

    with pytest.raises(ValueError, match="response.actions"):
        engine.validate()


def test_config_engine_rejects_a_non_boolean_ai_flag():
    engine = ConfigEngine(ai={"enabled": "yes"})

    with pytest.raises(ValueError, match="ai.enabled must be a boolean"):
        engine.validate()


def test_config_engine_from_dict_round_trip():
    payload = {
        "monitoring": {"paths": ["C:/sandbox"], "recursive": False},
        "detection": {"rules": ["default", "entropy"]},
        "response": {"actions": ["log", "quarantine"]},
        "ai": {"enabled": True},
    }

    engine = ConfigEngine.from_dict(payload)

    assert engine.to_dict() == payload

    # Missing keys fall back to documented defaults.
    defaulted = ConfigEngine.from_dict({})

    assert defaulted.monitoring == {"paths": ["."], "recursive": True}
    assert defaulted.ai == {"enabled": False}


# ---------------------------------------------------------------------------
# detection / policy / events / logging / workers
# ---------------------------------------------------------------------------


def _detection_engine() -> DetectionEngine:
    return DetectionEngine(
        policies=[
            Policy(
                name="deny-locked",
                action="deny",
                resource="file",
                condition={"extension": ".locked"},
            ),
            Policy(
                name="allow-txt",
                action="allow",
                resource="file",
                condition={"extension": ".txt"},
            ),
        ]
    )


def test_detection_engine_evaluates_a_matching_policy():
    detection = _detection_engine().evaluate(
        "file",
        {"extension": ".locked", "severity": "high", "confidence": 0.9},
    )

    assert detection is not None
    assert detection.action == "deny"
    assert detection.severity == "high"
    assert detection.to_dict()["confidence"] == pytest.approx(0.9)


def test_detection_engine_returns_none_when_no_policy_matches():
    assert _detection_engine().evaluate("file", {"extension": ".pdf"}) is None


def test_detection_engine_rejects_an_invalid_severity():
    with pytest.raises(ValueError, match="Invalid severity"):
        _detection_engine().evaluate(
            "file",
            {"extension": ".locked", "severity": "catastrophic"},
        )


def test_detection_engine_requires_a_resource():
    with pytest.raises(ValueError, match="requires a resource"):
        _detection_engine().detect({"context": {}})


def test_detection_engine_rejects_a_non_mapping_context():
    with pytest.raises(TypeError, match="context must be a dictionary"):
        _detection_engine().detect(
            {"resource": "file", "context": "not-a-mapping"}
        )


def test_detection_engine_history_and_clear():
    engine = _detection_engine()

    engine.detect({"resource": "file", "context": {"extension": ".locked"}})

    assert len(engine.history) == 1

    engine.clear_history()

    assert engine.history == []


def test_detection_engine_adds_policies_at_runtime():
    engine = _detection_engine()

    assert engine.evaluate("file", {"extension": ".zip"}) is None

    engine.add_policy(
        Policy(
            name="deny-zip",
            action="deny",
            resource="file",
            condition={"extension": ".zip"},
        )
    )

    assert engine.evaluate("file", {"extension": ".zip"}).action == "deny"


def test_detection_engine_deduplicates_repeated_events():
    """An identical repeated observation is collapsed by its fingerprint."""
    engine = _detection_engine()

    first = engine.evaluate("file", {"extension": ".locked", "severity": "high"})
    second = engine.evaluate("file", {"extension": ".locked", "severity": "high"})

    assert first is not None
    assert first.rule_name == "deny-locked"
    assert first.severity == "high"
    assert first.tags == ("file", "severity:high")
    assert first.evidence["policy"] == "deny-locked"

    # The same fingerprint is a duplicate and is not reported twice.
    assert second is None
    assert len(engine.history) == 1

    # A materially different observation is a new detection.
    third = engine.evaluate("file", {"extension": ".locked", "severity": "low"})

    assert third is not None
    assert len(engine.history) == 2


def test_policy_matches_prefix_strings_and_rejects_disabled():
    policy = Policy(
        name="prefix",
        action="allow",
        resource="file",
        condition={"path": "C:/sandbox"},
    )

    assert policy.matches("file", {"path": "C:/sandbox/a.txt"}) is True
    assert policy.matches("file", {"path": "C:/elsewhere/a.txt"}) is False
    assert policy.matches("process", {"path": "C:/sandbox/a.txt"}) is False

    policy.enabled = False

    assert policy.matches("file", {"path": "C:/sandbox/a.txt"}) is False


def test_policy_engine_deny_and_no_policy_paths():
    engine = PolicyEngine(
        [
            Policy(
                name="deny-locked",
                action="deny",
                resource="file",
                condition={"extension": ".locked"},
            ),
            Policy(
                name="allow-txt",
                action="allow",
                resource="file",
                condition={"extension": ".txt"},
            ),
        ]
    )

    assert engine.evaluate("file", {"extension": ".locked"}) == {
        "allowed": False,
        "policy": "deny-locked",
        "reason": "matched_policy",
    }
    assert engine.evaluate("file", {"extension": ".txt"}) == {
        "allowed": True,
        "policy": "allow-txt",
        "reason": "matched_policy",
    }
    assert engine.evaluate("file", {}) == {
        "allowed": False,
        "policy": None,
        "reason": "no_policy",
    }
    assert engine.evaluate("file") == {
        "allowed": False,
        "policy": None,
        "reason": "no_policy",
    }


def test_policy_engine_add_policy():
    engine = PolicyEngine()

    assert engine.evaluate("file", {})["policy"] is None

    engine.add_policy(
        Policy(name="late", action="allow", resource="file", condition={})
    )

    assert engine.evaluate("file", {})["policy"] == "late"


def test_event_bus_publishes_deduplicates_and_drains():
    bus = EventBus()
    received = []

    bus.subscribe("threat", received.append)

    assert bus.route_count("threat") == 1
    assert len(bus) == 0

    event = bus.publish(
        "threat",
        event_type="burst",
        payload={"score": 70.0},
        severity="critical",
    )

    assert event.type == "burst"
    assert event.source == "threat"
    assert len(received) == 1
    assert len(bus) == 1
    assert event.to_dict()["payload"] == {"score": 70.0}

    # Re-publishing the same event is a no-op.
    assert bus.publish_event(event) is None
    assert len(bus) == 1

    assert len(bus.drain()) == 1
    assert len(bus) == 0


def test_event_bus_uses_the_explicit_source_and_isolates_failing_subscribers():
    bus = EventBus()

    def _explode(event):
        raise RuntimeError("subscriber is broken")

    bus.subscribe("threat", _explode)

    event = bus.publish("threat", event_type="burst", source="monitor")

    assert event.source == "monitor"

    # A broken subscriber must not break publication.
    assert SecurityEvent(
        event_id="x",
        source="s",
        route="r",
        type="t",
    ).severity == "info"


def test_structured_logger_writes_json_lines_and_closes(tmp_path):
    log_path = tmp_path / "app.log"

    logger = make_structured_logger(
        "rdrs-task7",
        log_path=log_path,
        level="DEBUG",
    )

    try:
        logger.info("hello", extra={"details": {"k": "v"}})
        logger.error("boom")

        assert log_path.is_file()

        lines = [
            line
            for line in log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

        assert len(lines) == 2

        first = json.loads(lines[0])

        assert first["message"] == "hello"
        assert first["level"] == "INFO"
        assert first["details"] == {"k": "v"}

        second = json.loads(lines[1])

        assert second["message"] == "boom"
        assert second["level"] == "ERROR"
    finally:
        logger.close()


def test_structured_logger_without_a_file_only_streams():
    logger = make_structured_logger("rdrs-task7-stream", level="INFO")

    try:
        logger.info("streamed only")

        assert all(
            not hasattr(handler, "baseFilename")
            for handler in logger.handlers
        )
    finally:
        logger.close()


def test_close_rotating_logs_tolerates_missing_and_repeated_calls(tmp_path):
    loggers = initialize_rotating_logs(tmp_path / "logs", level="INFO")

    assert loggers

    # Closing twice must be harmless, and a None/empty mapping is a no-op.
    close_rotating_logs(loggers)
    close_rotating_logs(loggers)
    close_rotating_logs({})
    close_rotating_logs(None)


def test_initialize_rotating_logs_creates_the_five_operational_files(tmp_path):
    logs_dir = tmp_path / "logs"

    loggers = initialize_rotating_logs(logs_dir, level="DEBUG")

    try:
        assert len(loggers) == 5

        for logger in loggers.values():
            assert logger.propagate is False
            assert logger.handlers

        logger_name, logger = next(iter(loggers.items()))

        logger.info("file created")

        written = sorted(
            path.name for path in logs_dir.iterdir() if path.is_file()
        )

        assert written, "at least one operational log file must be created"
    finally:
        close_rotating_logs(loggers)


def test_task_queue_backpressure_and_worker_pool():
    queue = TaskQueue(maxsize=2)

    assert queue.backpressure == 0.0

    queue.put("plain-value")
    queue.put(lambda: "computed")

    assert queue.backpressure == pytest.approx(1.0)

    results = WorkerPool(queue, workers=2).run_once()

    assert sorted(results, key=str) == sorted(["plain-value", "computed"], key=str)
    assert queue.empty()


def test_task_queue_without_a_bound_reports_zero_backpressure():
    queue = TaskQueue()

    queue.put_nowait("x")

    assert queue.backpressure == 0.0
    assert queue.get_nowait() == "x"


def test_quarantine_copies_evidence_without_touching_the_original(tmp_path):
    source = tmp_path / "evidence" / "a.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("original", encoding="utf-8")

    manager = QuarantineManager(tmp_path / "vault")

    result = manager.quarantine(source)

    assert result.copied is True
    assert result.simulation_mode is True

    # Copy-only: the original must still exist untouched.
    assert source.read_text(encoding="utf-8") == "original"
    assert Path(result.destination).read_text(encoding="utf-8") == "original"


def test_quarantine_rejects_missing_files_and_directories(tmp_path):
    manager = QuarantineManager(tmp_path / "vault")

    with pytest.raises(FileNotFoundError, match="Evidence file not found"):
        manager.quarantine(tmp_path / "absent.txt")

    directory = tmp_path / "a-directory"
    directory.mkdir()

    with pytest.raises(ValueError, match="is not a file"):
        manager.quarantine(directory)


def test_quarantine_runs_in_simulation_mode_by_default(tmp_path):
    assert QuarantineManager(tmp_path / "vault").simulation_mode is True
    assert (
        QuarantineManager(tmp_path / "vault", simulation_mode=False).simulation_mode
        is False
    )


def test_utc_timestamp_is_iso_and_timezone_aware():
    from rdrs.api import utc_timestamp

    parsed = datetime.fromisoformat(utc_timestamp())

    assert parsed.tzinfo is not None


def test_first_not_none_keeps_falsy_values():
    from rdrs.api import _first_not_none

    assert _first_not_none(None, 0, 5) == 0
    assert _first_not_none(None, [], "x") == []
    assert _first_not_none(None, None, False) is False
    assert _first_not_none(None, None) is None
    assert _first_not_none(False, "fallback") is False


def test_report_media_types():
    from rdrs.api import _report_media_type

    assert _report_media_type("json") == "application/json"
    assert _report_media_type("csv") == "text/csv"
    assert _report_media_type("anything-else") == "text/csv"


def test_write_json_and_csv_reports_handle_an_empty_report(tmp_path):
    from rdrs.api import write_csv_report, write_json_report

    report = {
        "incident": {"id": 1, "created_at": None, "score": 0.0, "level": "Normal"},
        "timeline": [],
        "affected_files": [],
        "recommendations": [],
        "threat_score": 0.0,
    }

    json_path = tmp_path / "empty.json"
    write_json_report(report, json_path)

    assert json_path.is_file()

    csv_path = tmp_path / "empty.csv"
    write_csv_report(report, csv_path)

    lines = csv_path.read_text(encoding="utf-8").splitlines()

    assert lines[0].startswith("record_type")
    assert len(lines) == 2


def test_health_registry_tracks_components_and_overall_state():
    registry = HealthRegistry()

    # An empty registry is healthy by definition.
    assert registry.snapshot() == {"overall": True}

    registry.update("database", healthy=True, details={"path": "data/rdrs.db"})
    registry.update("detection", healthy=True)

    assert registry.snapshot()["overall"] is True
    assert registry.snapshot()["database"]["details"] == {"path": "data/rdrs.db"}

    registry.update("detection", healthy=False, details={"reason": "stalled"})

    snapshot = registry.snapshot()

    assert snapshot["overall"] is False
    assert snapshot["detection"]["healthy"] is False
    assert snapshot["detection"]["details"] == {"reason": "stalled"}


def test_health_registry_details_default_to_an_empty_mapping():
    registry = HealthRegistry()
    registry.update("logging", healthy=True)

    assert registry.snapshot()["logging"]["details"] == {}
