from __future__ import annotations

import hashlib
import mimetypes
import os
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psutil

from rdrs.entropy import calculate_entropy
from rdrs.fileaccess import open_read_shared

__all__ = [
    "CpuSampler",
    "EntropyHistory",
    "FileArtifact",
    "FileMetadata",
    "NetworkConnectionTelemetry",
    "ProcessMonitor",
    "ProcessTelemetry",
    "ProcessTree",
    "TelemetrySnapshot",
    "UserSessionTelemetry",
    "calculate_entropy",
]


@dataclass
class FileMetadata:
    path: str
    size: int = 0
    extension: str = ""
    modified_at: float | None = None
    created_at: float | None = None
    sha256: str = ""

    @classmethod
    def from_path(
        cls,
        file_path: str | os.PathLike[str],
    ) -> "FileMetadata":
        path = Path(file_path)
        stat = path.stat()

        digest = hashlib.sha256()

        # Opened with delete-sharing so hashing a file never prevents another
        # process from renaming or deleting it (see rdrs.fileaccess).
        with open_read_shared(path) as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)

        return cls(
            path=str(path),
            size=stat.st_size,
            extension=path.suffix.lower(),
            modified_at=stat.st_mtime,
            created_at=stat.st_ctime,
            sha256=digest.hexdigest(),
        )


@dataclass
class FileArtifact:
    path: str
    size: int
    extension: str
    sha256: str
    mime_type: str = "application/octet-stream"
    created_at: float | None = None
    modified_at: float | None = None

    @classmethod
    def from_path(
        cls,
        file_path: str | os.PathLike[str],
    ) -> "FileArtifact":
        metadata = FileMetadata.from_path(file_path)

        # Use the platform MIME database rather than a hand-written table: the
        # previous table labelled .jpg/.jpeg as image/png and fell back to
        # application/octet-stream for common types such as .json and .pdf.
        mime_type = (
            mimetypes.guess_type(metadata.path)[0]
            or "application/octet-stream"
        )

        return cls(
            path=metadata.path,
            size=metadata.size,
            extension=metadata.extension,
            sha256=metadata.sha256,
            mime_type=mime_type,
            created_at=metadata.created_at,
            modified_at=metadata.modified_at,
        )


@dataclass
class ProcessTelemetry:
    pid: int
    name: str
    parent_pid: int | None = None
    command_line: str = ""
    cwd: str = ""
    started_at: float | None = None

    # Real psutil telemetry
    cpu_percent: float = 0.0
    memory_rss: int = 0
    memory_percent: float = 0.0
    disk_write_bytes: int = 0
    executable_path: str = ""

    @classmethod
    def current(cls) -> "ProcessTelemetry":
        return cls.from_pid(os.getpid())

    @classmethod
    def from_pid(
        cls,
        pid: int,
        *,
        cpu_percent: float | None = None,
    ) -> "ProcessTelemetry":
        """Collect telemetry for one process.

        ``cpu_percent`` may be supplied by the caller. A CPU percentage is only
        meaningful when two samples are compared, and the blocking form
        ``psutil`` offers for that (``cpu_percent(interval=0.05)``) *sleeps*
        50 ms per process -- ~26 s for a ~380-process machine, which stalled the
        whole process-monitor cycle. :class:`ProcessMonitor` therefore computes
        the percentage itself from the delta between cycles (see
        :class:`CpuSampler`) and passes it in here.

        When no value is supplied, ``psutil``'s non-blocking reading is used.
        That reading is 0.0 the first time a process is seen, which is honest
        ("not yet measurable") rather than fabricated.
        """
        process = psutil.Process(pid)

        with process.oneshot():
            try:
                name = process.name()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                name = "unknown"

            try:
                parent_pid = process.ppid()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                parent_pid = None

            try:
                command_line = " ".join(process.cmdline())
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                command_line = ""

            try:
                cwd = process.cwd()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                cwd = ""

            try:
                started_at = process.create_time()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                started_at = None

            if cpu_percent is None:
                try:
                    cpu_percent = process.cpu_percent(
                        interval=None
                    )
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    cpu_percent = 0.0

            try:
                memory = process.memory_info()
                memory_rss = memory.rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                memory_rss = 0

            try:
                memory_percent = process.memory_percent()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                memory_percent = 0.0

            try:
                io = process.io_counters()
                disk_write_bytes = io.write_bytes
            except (
                psutil.NoSuchProcess,
                psutil.AccessDenied,
                AttributeError,
            ):
                disk_write_bytes = 0

            try:
                executable_path = process.exe()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                executable_path = ""

        return cls(
            pid=pid,
            name=name,
            parent_pid=parent_pid,
            command_line=command_line,
            cwd=cwd,
            started_at=started_at,
            cpu_percent=cpu_percent,
            memory_rss=memory_rss,
            memory_percent=memory_percent,
            disk_write_bytes=disk_write_bytes,
            executable_path=executable_path,
        )


class CpuSampler:
    """Non-blocking, per-process CPU percentage sampling.

    ``psutil`` can only report a meaningful CPU percentage by comparing two
    samples. Its blocking helper, ``cpu_percent(interval=0.05)``, *sleeps* for
    the interval -- once per process. On a ~380-process machine that cost
    ~26 seconds for a single capture, so the process-monitor cycle never
    completed within a useful time and short monitoring runs persisted no
    process rows at all.

    This sampler keeps its own ``(wall_time, cpu_seconds)`` baseline per pid
    and derives the percentage from the delta between consecutive cycles. A
    full capture therefore never blocks, and the value is a real measurement of
    the interval that actually elapsed.

    The first time a pid is seen there is no baseline to compare against, so it
    reports ``0.0`` -- meaning "not yet measurable", never a fabricated figure.
    Call :meth:`prime` to establish baselines up front so the very first
    capture already carries real values.
    """

    #: Processes whose CPU time does not represent CPU *usage*.
    #:
    #: On Windows pid 0 is the "System Idle Process": its ``cpu_times`` track
    #: *idle* time, so any delta-based reading reports ~100%. Reporting that as
    #: CPU usage would make the highest-CPU suspect selection pick the idle
    #: process every cycle and fire the ``cpu_spike`` signal continuously --
    #: a permanent false positive. It is reported as 0.0 (not measurable as
    #: usage) rather than a fabricated figure.
    NON_MEASURABLE_PIDS = frozenset({0})

    def __init__(self) -> None:
        self._baseline: dict[int, tuple[float, float]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _cpu_seconds(pid: int) -> float | None:
        """Return total CPU seconds consumed by ``pid``, or None if gone."""
        try:
            times = psutil.Process(pid).cpu_times()
        except (
            psutil.NoSuchProcess,
            psutil.AccessDenied,
            psutil.ZombieProcess,
            OSError,
        ):
            return None

        return float(times.user + times.system)

    def observe(self, pid: int, cpu_seconds: float) -> float:
        """Return CPU percent for ``pid`` from an already-read CPU-seconds value.

        Lets the caller reuse the ``cpu_times`` it already read as part of a
        batched capture instead of issuing a second syscall for it.
        """
        if pid in self.NON_MEASURABLE_PIDS:
            return 0.0

        now = time.monotonic()

        with self._lock:
            previous = self._baseline.get(pid)
            self._baseline[pid] = (now, cpu_seconds)

        if previous is None:
            return 0.0

        previous_wall, previous_cpu = previous
        wall_delta = now - previous_wall

        if wall_delta <= 0:
            return 0.0

        cpu_delta = max(0.0, cpu_seconds - previous_cpu)

        # Percent of one core, capped at 100 to match the configured
        # 0-100 range used by the detection thresholds.
        return min(100.0, 100.0 * cpu_delta / wall_delta)

    def sample(self, pid: int) -> float:
        """Return CPU percent for ``pid`` since the previous sample."""
        cpu_seconds = self._cpu_seconds(pid)

        if cpu_seconds is None:
            with self._lock:
                self._baseline.pop(pid, None)

            return 0.0

        return self.observe(pid, cpu_seconds)

    def retain(self, pids: set[int]) -> None:
        """Drop baselines for processes that no longer exist."""
        with self._lock:
            if len(self._baseline) <= len(pids):
                return

            for pid in list(self._baseline):
                if pid not in pids:
                    self._baseline.pop(pid, None)

    def clear(self) -> None:
        with self._lock:
            self._baseline.clear()


class ProcessTree:
    def __init__(self) -> None:
        self._by_pid: dict[int, ProcessTelemetry] = {}

    def add(self, process: ProcessTelemetry) -> None:
        self._by_pid[process.pid] = process

    def parent_of(self, pid: int) -> int | None:
        process = self._by_pid.get(pid)
        return process.parent_pid if process else None

    def children_of(self, parent_pid: int) -> list[int]:
        return [
            process.pid
            for process in self._by_pid.values()
            if process.parent_pid == parent_pid
        ]

    def all_processes(self) -> list[ProcessTelemetry]:
        return list(self._by_pid.values())


class ProcessMonitor:
    """Collect real process telemetry from the local operating system."""

    # Attributes read in ONE batched pass per process. psutil's
    # ``process_iter(attrs=...)`` reads these through a single ``oneshot()``
    # block, which measured ~6x faster than issuing the individual accessors
    # ourselves (1.3 s vs 8.1 s for ~385 processes on Windows).
    CAPTURE_ATTRS = (
        "pid",
        "name",
        "ppid",
        "cpu_times",
        "memory_info",
        "create_time",
        "exe",
        "cmdline",
        "cwd",
        "io_counters",
    )

    def __init__(self) -> None:
        self.tree = ProcessTree()
        self.cpu_sampler = CpuSampler()
        self._tree_lock = threading.Lock()

    @staticmethod
    def _cpu_seconds_from_times(times: Any) -> float:
        if times is None:
            return 0.0

        return float(
            getattr(times, "user", 0.0)
            + getattr(times, "system", 0.0)
        )

    def _telemetry_from_info(
        self,
        info: dict[str, Any],
        *,
        total_memory: int,
    ) -> ProcessTelemetry | None:
        """Build one :class:`ProcessTelemetry` from a batched attribute read."""
        pid = info.get("pid")

        if pid is None:
            return None

        times = info.get("cpu_times")

        if times is not None:
            cpu_percent = self.cpu_sampler.observe(
                pid,
                self._cpu_seconds_from_times(times),
            )
        else:
            cpu_percent = self.cpu_sampler.sample(pid)

        memory = info.get("memory_info")
        memory_rss = int(getattr(memory, "rss", 0) or 0)

        # Derived once from a single virtual_memory() reading taken per
        # capture, instead of psutil's per-process memory_percent() accessor.
        memory_percent = (
            (100.0 * memory_rss / total_memory)
            if total_memory
            else 0.0
        )

        counters = info.get("io_counters")
        disk_write_bytes = int(
            getattr(counters, "write_bytes", 0) or 0
        )

        command_line = info.get("cmdline")
        if isinstance(command_line, (list, tuple)):
            command_line = " ".join(str(part) for part in command_line)
        elif command_line is None:
            command_line = ""

        name = info.get("name")
        started_at = info.get("create_time")

        return ProcessTelemetry(
            pid=int(pid),
            name=str(name) if name else "unknown",
            parent_pid=info.get("ppid"),
            command_line=str(command_line),
            cwd=str(info.get("cwd") or ""),
            started_at=(
                float(started_at)
                if isinstance(started_at, (int, float))
                else None
            ),
            cpu_percent=float(cpu_percent),
            memory_rss=memory_rss,
            memory_percent=float(memory_percent),
            disk_write_bytes=disk_write_bytes,
            executable_path=str(info.get("exe") or ""),
        )

    def prime(self) -> int:
        """Establish CPU baselines for every running process.

        Called once when the monitor starts so that the first capture reports
        real CPU percentages instead of an initial 0.0.
        """
        primed = 0

        for process in psutil.process_iter(attrs=["pid", "cpu_times"]):
            info = process.info
            times = info.get("cpu_times")

            if times is None:
                continue

            pid = info.get("pid")

            if pid is None:
                continue

            self.cpu_sampler.observe(
                int(pid),
                self._cpu_seconds_from_times(times),
            )
            primed += 1

        return primed

    def capture_current(self) -> ProcessTelemetry:
        process = ProcessTelemetry.from_pid(
            os.getpid(),
            cpu_percent=self.cpu_sampler.sample(os.getpid()),
        )
        self.tree.add(process)
        return process

    def capture(
        self,
        *,
        pid: int | None = None,
    ) -> ProcessTelemetry:
        target_pid = os.getpid() if pid is None else pid

        process = ProcessTelemetry.from_pid(
            target_pid,
            cpu_percent=self.cpu_sampler.sample(target_pid),
        )
        self.tree.add(process)
        return process

    def capture_all(self) -> list[ProcessTelemetry]:
        """Capture every running process efficiently.

        Two changes took this from ~26 s to well under 2 s:

        1. CPU percentages come from :class:`CpuSampler` deltas instead of
           ``psutil``'s blocking ``cpu_percent(interval=0.05)``, which *slept*
           50 ms for every process.
        2. Attributes are read in one batched ``process_iter(attrs=...)`` pass
           rather than through individual accessors, and ``memory_percent`` is
           derived from a single ``virtual_memory()`` reading instead of a
           per-process syscall.

        Both preserve exactly the same collected fields, so detection
        behaviour is unchanged while the process-monitor cycle now keeps pace
        with the file monitor.
        """
        try:
            total_memory = int(psutil.virtual_memory().total)
        except Exception:
            total_memory = 0

        captured: list[ProcessTelemetry] = []
        seen: set[int] = set()

        for process in psutil.process_iter(attrs=self.CAPTURE_ATTRS):
            # The whole per-process body is guarded: a process can exit at any
            # moment between the iteration and the attribute read, and one
            # unreadable process must never abort the entire capture.
            try:
                info = process.info
                pid = info.get("pid")

                if pid is None:
                    continue

                seen.add(int(pid))

                telemetry = self._telemetry_from_info(
                    info,
                    total_memory=total_memory,
                )
            except (
                psutil.NoSuchProcess,
                psutil.AccessDenied,
                psutil.ZombieProcess,
                OSError,
            ):
                continue

            if telemetry is None:
                continue

            with self._tree_lock:
                self.tree.add(telemetry)

            captured.append(telemetry)

        self.cpu_sampler.retain(seen)

        return captured

    def snapshot(self) -> list[dict[str, Any]]:
        return [
            process.__dict__.copy()
            for process in self.tree.all_processes()
        ]


@dataclass
class UserSessionTelemetry:
    user: str
    session_id: str
    privilege: str = "user"
    home: str = ""

    @staticmethod
    def is_elevated() -> bool:
        """Return whether the current process runs with elevated privileges.

        Reported rather than assumed: a hard-coded "user" would misreport an
        administrator session, and this value is persisted to the
        ``user_sessions`` table, so a fabricated value would become stored
        evidence.
        """
        try:
            if os.name == "nt":
                import ctypes

                return bool(
                    ctypes.windll.shell32.IsUserAnAdmin()
                )

            return os.geteuid() == 0
        except Exception:
            return False

    @classmethod
    def current(cls) -> "UserSessionTelemetry":
        user = (
            os.environ.get("USERNAME")
            or os.environ.get("USER")
            or "unknown"
        )

        return cls(
            user=user,
            session_id=os.environ.get("SESSIONNAME") or "local",
            privilege=(
                "admin"
                if cls.is_elevated()
                else "user"
            ),
            home=os.path.expanduser("~"),
        )


@dataclass
class NetworkConnectionTelemetry:
    family: str
    local_address: str
    remote_address: str
    protocol: str
    port: int

    @classmethod
    def from_socket(
        cls,
        sock: socket.socket,
    ) -> "NetworkConnectionTelemetry":
        try:
            peer = sock.getpeername()
            host = peer[0] if peer else ""
            port = peer[1] if len(peer) > 1 else 0

            protocol = (
                "tcp"
                if sock.type == socket.SOCK_STREAM
                else "udp"
            )

            local = sock.getsockname()
            local_address = str(local[:2]) if local else ""

            return cls(
                str(sock.family),
                local_address,
                str((host, port)),
                protocol,
                port,
            )

        except OSError:
            return cls(
                str(sock.family),
                "",
                "",
                "tcp",
                0,
            )


@dataclass
class TelemetrySnapshot:
    file: FileMetadata | None = None
    process: ProcessTelemetry | None = None
    user: UserSessionTelemetry | None = None
    network: NetworkConnectionTelemetry | None = None
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file.__dict__ if self.file else None,
            "process": self.process.__dict__ if self.process else None,
            "user": self.user.__dict__ if self.user else None,
            "network": self.network.__dict__ if self.network else None,
            "context": self.context,
        }


class EntropyHistory:
    def __init__(self) -> None:
        self.history: dict[str, float] = {}

    def record(self, path: str, entropy: float) -> None:
        self.history[path] = entropy

    def latest(self, path: str) -> float | None:
        return self.history.get(path)
