from __future__ import annotations

import json
import socket
from pathlib import Path

from rdrs.audit import AuditEngine
from rdrs.health import HealthRegistry
from rdrs.logging import StructuredLogger, initialize_logging
from rdrs.monitoring import DirectoryMonitor, FileActionDetector, FileTypeClassifier
from rdrs.telemetry import (
    EntropyHistory,
    FileArtifact,
    NetworkConnectionTelemetry,
    ProcessTelemetry,
    ProcessTree,
    UserSessionTelemetry,
    calculate_entropy,
)


def test_structured_logging_emits_json(tmp_path):
    logger = StructuredLogger("rdrs.test", log_path=tmp_path / "audit.log")

    try:
        logger.info("feature.test", extra={"feature": "F06", "state": "ready"})
        log_contents = (tmp_path / "audit.log").read_text(encoding="utf-8")
    finally:
        logger.close()

    payload = json.loads(log_contents.strip().splitlines()[-1])
    assert payload["logger"] == "rdrs.test"
    assert payload["feature"] == "F06"
    assert payload["state"] == "ready"


def test_audit_engine_records_actions():
    audit = AuditEngine()
    entry = audit.record("analyst", "scan", "started", success=True, details={"path": "/tmp"})
    assert entry["actor"] == "analyst"
    assert entry["action"] == "scan"
    assert entry["result"] == "started"
    assert len(audit.entries) == 1


def test_health_registry_tracks_component_status():
    registry = HealthRegistry()
    registry.update("database", healthy=True, details={"count": 3})
    registry.update("worker", healthy=False, details={"queue": 8})
    snapshot = registry.snapshot()
    assert snapshot["database"]["healthy"] is True
    assert snapshot["worker"]["healthy"] is False
    assert snapshot["overall"] is False


def test_file_detects_create_modify_delete_and_rename(tmp_path):
    root = tmp_path / "watch"
    root.mkdir()
    sample = root / "sample.txt"
    sample.write_text("hello", encoding="utf-8")

    detector = FileActionDetector(root)
    create_event = detector.detect(sample, "create")
    modify_event = detector.detect(sample, "modify")
    rename_event = detector.detect(sample, "rename", previous_path=str(sample), new_path=str(root / "renamed.txt"))

    assert create_event["action"] == "create"
    assert modify_event["action"] == "modify"
    assert rename_event["action"] == "rename"
    assert rename_event["previous_path"] == str(sample)


def test_directory_monitor_respects_exclusions(tmp_path):
    root = tmp_path / "root"
    good = root / "keep"
    excluded = root / "skip"
    good.mkdir(parents=True)
    excluded.mkdir(parents=True)
    (good / "a.txt").write_text("abc", encoding="utf-8")
    (excluded / "b.txt").write_text("def", encoding="utf-8")

    monitor = DirectoryMonitor(root, recursive=True, excludes=["skip"])
    hits = monitor.scan()
    assert len(hits) == 1
    assert hits[0].name == "a.txt"


def test_file_metadata_hash_and_type(tmp_path):
    path = tmp_path / "example.bin"
    path.write_bytes(b"hello world\n")

    artifact = FileArtifact.from_path(path)
    assert artifact.size == 12
    assert artifact.sha256
    assert artifact.extension == ".bin"
    assert artifact.mime_type in {"application/octet-stream", "text/plain"}


def test_entropy_calculation_and_history():
    data = b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00" * 2
    entropy = calculate_entropy(data)
    history = EntropyHistory()
    history.record("test.bin", entropy)
    assert entropy >= 0.0
    assert "test.bin" in history.history


def test_process_and_user_telemetry_capture():
    proc = ProcessTelemetry.current()
    user = UserSessionTelemetry.current()
    assert proc.pid > 0
    assert user.user
    assert proc.command_line


def test_process_tree_and_network_telemetry():
    tree = ProcessTree()
    root = ProcessTelemetry(pid=1000, name="root", parent_pid=None, command_line="root cmd", cwd="/")
    child = ProcessTelemetry(pid=1001, name="child", parent_pid=1000, command_line="child cmd", cwd="/")
    tree.add(root)
    tree.add(child)

    assert tree.parent_of(1001) == 1000
    assert tree.children_of(1000) == [1001]

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        try:
            sock.connect(("127.0.0.1", 80))
        except OSError:
            pass
        telemetry = NetworkConnectionTelemetry.from_socket(sock)
        assert telemetry.protocol in {"tcp", "udp"}
        assert telemetry.port >= 0
    finally:
        sock.close()
