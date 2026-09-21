"""FR-04 regression tests — process and host telemetry metadata.

Defects covered
---------------
1. **Fabricated privilege.** ``UserSessionTelemetry.current()`` hard-coded
   ``privilege="user"``, so an elevated session was misreported. The value is
   persisted to the ``user_sessions`` table, so the fabrication became stored
   evidence (Locked Rules §4 forbids fake/hard-coded status).

2. **Wrong MIME types.** ``FileArtifact.from_path`` used a hand-written table
   that labelled ``.jpg``/``.jpeg`` as ``image/png`` and fell back to
   ``application/octet-stream`` for common types such as ``.json`` and ``.pdf``.

3. **Divergent duplicate entropy.** ``telemetry.py`` carried its own
   ``calculate_entropy`` alongside ``rdrs.entropy.calculate_entropy`` -- two
   implementations of one algorithm, free to drift (and the telemetry copy
   lacked the ``[0, 8]`` clamp).

4. **Rename-blocking read.** ``FileMetadata.from_path`` used a plain
   ``Path.open("rb")``, which on Windows requests no ``FILE_SHARE_DELETE``, so
   hashing a file refused every other process permission to rename it.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import socket
from pathlib import Path

import pytest

from rdrs.telemetry import (
    CpuSampler,
    EntropyHistory,
    FileArtifact,
    FileMetadata,
    NetworkConnectionTelemetry,
    ProcessMonitor,
    ProcessTelemetry,
    ProcessTree,
    TelemetrySnapshot,
    UserSessionTelemetry,
    calculate_entropy,
)


# --------------------------------------------------------------------------
# privilege is detected, never assumed
# --------------------------------------------------------------------------


def test_privilege_is_detected_not_hard_coded():
    session = UserSessionTelemetry.current()

    assert session.privilege in {"admin", "user"}

    expected = "admin" if UserSessionTelemetry.is_elevated() else "user"
    assert session.privilege == expected


def test_is_elevated_returns_a_boolean():
    assert isinstance(UserSessionTelemetry.is_elevated(), bool)


def test_current_session_reports_real_host_details():
    session = UserSessionTelemetry.current()

    assert session.user
    assert session.user != ""
    assert session.home == os.path.expanduser("~")
    assert session.session_id


# --------------------------------------------------------------------------
# file metadata
# --------------------------------------------------------------------------


def test_file_metadata_reports_real_values(tmp_path):
    payload = b"normal document content " * 50
    path = tmp_path / "doc.txt"
    path.write_bytes(payload)

    metadata = FileMetadata.from_path(path)

    assert metadata.path == str(path)
    assert metadata.size == len(payload)
    assert metadata.extension == ".txt"
    assert metadata.sha256 == hashlib.sha256(payload).hexdigest()
    assert metadata.modified_at is not None
    assert metadata.created_at is not None


def test_file_metadata_hash_matches_for_a_large_file(tmp_path):
    payload = os.urandom(2 * 1024 * 1024)
    path = tmp_path / "large.bin"
    path.write_bytes(payload)

    metadata = FileMetadata.from_path(path)

    assert metadata.sha256 == hashlib.sha256(payload).hexdigest()


def test_file_metadata_raises_for_a_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        FileMetadata.from_path(tmp_path / "missing.txt")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("a.txt", "text/plain"),
        ("b.jpg", "image/jpeg"),
        ("c.jpeg", "image/jpeg"),
        ("d.png", "image/png"),
        ("e.json", "application/json"),
        ("f.pdf", "application/pdf"),
        ("g.md", "text/markdown"),
        ("noextension", "application/octet-stream"),
    ],
)
def test_file_artifact_mime_types_are_correct(tmp_path, name, expected):
    """The hand-written MIME table got four of these wrong."""
    path = tmp_path / name
    path.write_bytes(b"data" * 10)

    artifact = FileArtifact.from_path(path)

    truth = mimetypes.guess_type(str(path))[0] or "application/octet-stream"

    assert artifact.mime_type == truth
    assert artifact.mime_type == expected


def test_file_artifact_carries_the_file_metadata(tmp_path):
    payload = b"payload" * 100
    path = tmp_path / "thing.json"
    path.write_bytes(payload)

    artifact = FileArtifact.from_path(path)

    assert artifact.path == str(path)
    assert artifact.size == len(payload)
    assert artifact.extension == ".json"
    assert artifact.sha256 == hashlib.sha256(payload).hexdigest()


def test_reading_a_file_does_not_block_rename(tmp_path):
    """Hashing must not obstruct the activity being observed."""
    from rdrs.fileaccess import open_read_shared

    target = tmp_path / "held.txt"
    target.write_bytes(b"payload" * 500)

    renamed = tmp_path / "held.locked"

    with open_read_shared(target) as handle:
        handle.read(32)
        target.rename(renamed)

    assert renamed.is_file()
    assert not target.exists()


def test_hashing_a_large_file_does_not_block_a_concurrent_rename(tmp_path):
    """A rename must succeed WHILE a large file is being hashed.

    This is the behavioural proof that ``FileMetadata.from_path`` opens with
    delete-sharing. With a plain ``Path.open("rb")`` the handle is held for the
    whole read, so every rename attempted during it fails with ``WinError 32``.
    """
    import threading
    import time

    payload = os.urandom(24 * 1024 * 1024)
    target = tmp_path / "incoming.bin"
    target.write_bytes(payload)

    renamed = tmp_path / "incoming.locked"

    finished = threading.Event()
    result: dict[str, object] = {}

    def hash_it() -> None:
        try:
            result["metadata"] = FileMetadata.from_path(target)
        finally:
            finished.set()

    worker = threading.Thread(target=hash_it)
    worker.start()

    # Try to rename while the hash is still running.
    renamed_while_hashing = False
    deadline = time.time() + 10.0

    while time.time() < deadline:
        if finished.is_set():
            break

        try:
            target.rename(renamed)
            renamed_while_hashing = True
            break
        except PermissionError:
            time.sleep(0.005)
        except FileNotFoundError:
            break

    worker.join(timeout=30)

    assert renamed_while_hashing, (
        "the rename was blocked while hashing, so the file handle is not shared"
    )
    assert renamed.is_file()

    # The hash itself must still have completed and be correct.
    metadata = result.get("metadata")
    assert metadata is not None
    assert metadata.sha256 == hashlib.sha256(payload).hexdigest()


# --------------------------------------------------------------------------
# one entropy implementation
# --------------------------------------------------------------------------


def test_telemetry_entropy_is_the_single_shared_implementation():
    from rdrs.entropy import calculate_entropy as canonical

    assert calculate_entropy is canonical


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (b"", 0.0),
        (b"A" * 1000, 0.0),
        (bytes(range(256)) * 10, 8.0),
    ],
)
def test_calculate_entropy_values(payload, expected):
    assert calculate_entropy(payload) == pytest.approx(expected)


def test_calculate_entropy_is_bounded():
    for payload in (b"", b"a", os.urandom(4096), bytes(range(256))):
        assert 0.0 <= calculate_entropy(payload) <= 8.0


# --------------------------------------------------------------------------
# process telemetry
# --------------------------------------------------------------------------


def test_process_telemetry_current_reports_this_process():
    telemetry = ProcessTelemetry.current()

    assert telemetry.pid == os.getpid()
    assert telemetry.name
    assert telemetry.cpu_percent >= 0.0
    assert telemetry.memory_rss >= 0
    assert telemetry.memory_percent >= 0.0
    assert telemetry.executable_path


def test_capture_returns_a_real_snapshot_for_a_pid():
    monitor = ProcessMonitor()
    snapshot = monitor.capture(pid=os.getpid())

    assert snapshot.pid == os.getpid()
    assert snapshot.name
    assert snapshot.parent_pid is not None
    assert snapshot.memory_rss > 0


def test_capture_all_includes_this_process():
    monitor = ProcessMonitor()
    monitor.prime()

    captured = monitor.capture_all()
    pids = {process.pid for process in captured}

    assert os.getpid() in pids


def test_process_tree_tracks_parents_and_children():
    tree = ProcessTree()
    tree.add(ProcessTelemetry(pid=10, name="root"))
    tree.add(ProcessTelemetry(pid=11, name="child", parent_pid=10))

    assert tree.parent_of(11) == 10
    assert tree.children_of(10) == [11]
    assert tree.parent_of(999) is None
    assert {p.pid for p in tree.all_processes()} == {10, 11}


def test_snapshot_serialises_the_tree():
    monitor = ProcessMonitor()
    monitor.capture(pid=os.getpid())

    rows = monitor.snapshot()

    assert rows
    assert all(isinstance(row, dict) for row in rows)
    assert any(row.get("pid") == os.getpid() for row in rows)


def test_cpu_sampler_never_reports_negative_values():
    sampler = CpuSampler()
    sampler.observe(1, 10.0)

    # A process cannot un-consume CPU time, but a malformed reading must not
    # produce a negative percentage.
    assert sampler.observe(1, 5.0) >= 0.0
    assert sampler.sample(os.getpid()) >= 0.0


# --------------------------------------------------------------------------
# host telemetry containers
# --------------------------------------------------------------------------


def test_network_telemetry_from_a_real_socket():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)

    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.connect(server.getsockname())

    try:
        telemetry = NetworkConnectionTelemetry.from_socket(client)

        assert telemetry.protocol == "tcp"
        assert telemetry.port > 0
        assert "127.0.0.1" in telemetry.remote_address
    finally:
        client.close()
        server.close()


def test_network_telemetry_degrades_gracefully_on_a_closed_socket():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.close()

    telemetry = NetworkConnectionTelemetry.from_socket(sock)

    # No exception; empty values rather than fabricated ones.
    assert telemetry.remote_address == ""
    assert telemetry.port == 0


def test_telemetry_snapshot_serialises_every_part(tmp_path):
    path = tmp_path / "file.txt"
    path.write_bytes(b"content")

    snapshot = TelemetrySnapshot(
        file=FileMetadata.from_path(path),
        process=ProcessTelemetry.current(),
        user=UserSessionTelemetry.current(),
        context={"note": "probe"},
    )

    payload = snapshot.to_dict()

    assert payload["file"]["path"] == str(path)
    assert payload["process"]["pid"] == os.getpid()
    assert payload["user"]["user"]
    assert payload["network"] is None
    assert payload["context"] == {"note": "probe"}


def test_telemetry_snapshot_handles_empty_parts():
    payload = TelemetrySnapshot().to_dict()

    assert payload == {
        "file": None,
        "process": None,
        "user": None,
        "network": None,
        "context": {},
    }


def test_entropy_history_records_and_reads_back():
    history = EntropyHistory()

    assert history.latest("/a.txt") is None

    history.record("/a.txt", 7.9)
    history.record("/b.txt", 1.2)
    history.record("/a.txt", 8.0)

    assert history.latest("/a.txt") == 8.0
    assert history.latest("/b.txt") == 1.2
    assert history.latest("/missing.txt") is None


# ---------------------------------------------------------------------------
# FR-04 integration: collection -> storage -> API -> frontend
# ---------------------------------------------------------------------------

#: The six fields Build Guide Phase 3 names for a process snapshot.
GUIDE_PROCESS_FIELDS = (
    "name",
    "cpu_percent",
    "memory_percent",
    "disk_write_bytes",
    "executable_path",
    "parent_pid",
)


def test_api_process_payload_carries_every_guide_field(tmp_path):
    """Every guide-required field must reach the API payload."""
    from rdrs.dashboard_data import DashboardData
    from rdrs.storage import DatabaseManager

    database = DatabaseManager(tmp_path / "api.db")
    database.initialize()

    monitor = ProcessMonitor()
    monitor.prime()
    database.record_processes(monitor.capture_all())

    rows = DashboardData(database).recent_processes(limit=5)

    assert rows, "no process rows were exposed"

    payload = rows[0]

    for field in GUIDE_PROCESS_FIELDS:
        assert field in payload, f"{field} is missing from the API payload"

    database.close()


def test_frontend_process_columns_match_the_api_fields(tmp_path):
    """The dashboard must not read keys the API does not send.

    The process table read ``parent_process`` / ``parent_name`` while the API
    sends ``parent_pid``, so the Parent column always rendered the em-dash
    placeholder.
    """
    import re

    from rdrs.dashboard_data import DashboardData
    from rdrs.storage import DatabaseManager

    database = DatabaseManager(tmp_path / "wiring.db")
    database.initialize()

    monitor = ProcessMonitor()
    monitor.prime()
    database.record_processes(monitor.capture_all())

    payload = DashboardData(database).recent_processes(limit=5)[0]
    api_fields = set(payload.keys())

    html = Path("src/rdrs/dashboard/index.html").read_text(encoding="utf-8")
    body = re.search(
        r"function renderProcesses\(.*?\n    \}",
        html,
        re.S,
    ).group(0)

    accessed = set(re.findall(r"process\.(\w+)", body))

    unknown = accessed - api_fields

    assert not unknown, (
        f"the dashboard reads fields the API does not provide: {sorted(unknown)}"
    )
    assert "parent_pid" in accessed

    database.close()


def test_process_page_table_header_matches_rendered_cells():
    """Seven headers must match seven rendered cells."""
    import re

    html = Path("src/rdrs/dashboard/index.html").read_text(encoding="utf-8")

    body = re.search(
        r"function renderProcesses\(.*?\n    \}",
        html,
        re.S,
    ).group(0)

    # The process page branch renders one <tr> per process with N <td> cells.
    page_branch = body.split('if (elementId === "processPageTable")')[1]
    page_branch = page_branch.split("return;")[0]

    cells = page_branch.count("<td>")
    assert cells == 7, f"expected 7 rendered cells, found {cells}"


def test_no_process_termination_anywhere_in_source():
    """Locked Rules §9: never kill or terminate a real process.

    Matches actual termination *calls*. ``signal.SIGTERM`` is deliberately not
    matched: the runtime installs a graceful-shutdown handler for SIGINT /
    SIGTERM, which the Build Guide requires, and that is not terminating
    another process.
    """
    import re

    forbidden = re.compile(
        r"\.kill\("
        r"|\.terminate\("
        r"|os\.kill\("
        r"|taskkill"
        r"|TerminateProcess"
        r"|psutil\.wait_procs"
    )

    offenders = []

    for path in sorted(Path("src/rdrs").glob("*.py")):
        text = path.read_text(encoding="utf-8-sig")

        for number, line in enumerate(text.splitlines(), start=1):
            if forbidden.search(line):
                offenders.append(f"{path.name}:{number}: {line.strip()}")

    assert not offenders, (
        "process-termination calls found (simulation only is required):\n"
        + "\n".join(offenders)
    )


def test_sigterm_is_handled_not_sent():
    """Graceful shutdown must be handled locally, never signalled outward."""
    import re

    text = Path("src/rdrs/runtime.py").read_text(encoding="utf-8-sig")

    # It installs a handler ...
    assert "signal.signal(" in text
    assert "self.handle_signal" in text

    # ... and never sends a signal to another process.
    assert not re.search(r"os\.kill\(", text)
    assert not re.search(r"\.send_signal\(", text)


def test_suspect_process_drives_the_cpu_spike_signal(tmp_path):
    """A real snapshot fed through the detector must raise cpu_spike."""
    from rdrs.burst_detection import BurstDetectionEngine

    monitor = ProcessMonitor()
    snapshot = monitor.capture(pid=os.getpid())
    snapshot.cpu_percent = 95.0

    engine = BurstDetectionEngine()
    analysis = engine.update_process(snapshot.__dict__.copy())

    assert "cpu_spike" in analysis.signals
    assert engine.latest_cpu_percent == pytest.approx(95.0)
    assert engine.process is not None
    assert engine.process["name"] == snapshot.name

    # A quiet process must not raise the signal.
    quiet = monitor.capture(pid=os.getpid())
    quiet.cpu_percent = 2.0

    quiet_analysis = engine.update_process(quiet.__dict__.copy())

    assert "cpu_spike" not in quiet_analysis.signals


def test_update_process_rejects_a_non_mapping():
    from rdrs.burst_detection import BurstDetectionEngine

    engine = BurstDetectionEngine()

    with pytest.raises(TypeError):
        engine.update_process(["not", "a", "mapping"])
