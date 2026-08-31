from __future__ import annotations

import hashlib
import math
import os
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def calculate_entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts: dict[int, int] = {}
    for byte in data:
        counts[byte] = counts.get(byte, 0) + 1
    length = len(data)
    entropy = 0.0
    for count in counts.values():
        probability = count / length
        entropy -= probability * math.log2(probability)
    return entropy


@dataclass
class FileMetadata:
    path: str
    size: int = 0
    extension: str = ""
    modified_at: float | None = None
    created_at: float | None = None
    sha256: str = ""

    @classmethod
    def from_path(cls, file_path: str | os.PathLike[str]) -> "FileMetadata":
        path = Path(file_path)
        stat = path.stat()
        extension = path.suffix.lower()
        h = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                h.update(chunk)
        return cls(
            path=str(path),
            size=stat.st_size,
            extension=extension,
            modified_at=getattr(stat, "st_mtime", None),
            created_at=getattr(stat, "st_ctime", None),
            sha256=h.hexdigest(),
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
    def from_path(cls, file_path: str | os.PathLike[str]) -> "FileArtifact":
        path = Path(file_path)
        stat = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        if path.suffix.lower() in {".txt", ".md", ".log"}:
            mime_type = "text/plain"
        elif path.suffix.lower() in {".png", ".jpg", ".jpeg"}:
            mime_type = "image/png"
        else:
            mime_type = "application/octet-stream"
        return cls(
            path=str(path),
            size=stat.st_size,
            extension=path.suffix.lower(),
            sha256=digest.hexdigest(),
            mime_type=mime_type,
            created_at=getattr(stat, "st_ctime", None),
            modified_at=getattr(stat, "st_mtime", None),
        )


@dataclass
class ProcessTelemetry:
    pid: int
    name: str
    parent_pid: int | None = None
    command_line: str = ""
    cwd: str = ""
    started_at: float | None = None

    @classmethod
    def current(cls) -> "ProcessTelemetry":
        return cls(
            pid=os.getpid(),
            name=Path(os.getenv("PYTHON_PROGRAM_NAME", "python")).name,
            parent_pid=getattr(os, "getppid", lambda: None)(),
            command_line=" ".join(os.sys.argv),
            cwd=os.getcwd(),
            started_at=None,
        )


class ProcessTree:
    def __init__(self) -> None:
        self._by_pid: dict[int, ProcessTelemetry] = {}

    def add(self, process: ProcessTelemetry) -> None:
        self._by_pid[process.pid] = process

    def parent_of(self, pid: int) -> int | None:
        process = self._by_pid.get(pid)
        return process.parent_pid if process else None

    def children_of(self, parent_pid: int) -> list[int]:
        return [p.pid for p in self._by_pid.values() if p.parent_pid == parent_pid]


@dataclass
class UserSessionTelemetry:
    user: str
    session_id: str
    privilege: str = "user"
    home: str = ""

    @classmethod
    def current(cls) -> "UserSessionTelemetry":
        user = os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"
        return cls(user=user, session_id=os.environ.get("SESSIONNAME") or "local", privilege="user", home=os.path.expanduser("~"))


@dataclass
class NetworkConnectionTelemetry:
    family: str
    local_address: str
    remote_address: str
    protocol: str
    port: int

    @classmethod
    def from_socket(cls, sock: socket.socket) -> "NetworkConnectionTelemetry":
        try:
            peer = sock.getpeername()
            host = peer[0] if peer else ""
            port = peer[1] if len(peer) > 1 else 0
            proto = "tcp" if sock.type == socket.SOCK_STREAM else "udp"
            local = sock.getsockname()
            local_addr = str(local[:2]) if local else ""
            return cls(str(sock.family), local_addr, str((host, port)), proto, port)
        except OSError:
            return cls(str(sock.family), "", "", "tcp", 0)


@dataclass
class TelemetrySnapshot:
    file: FileMetadata | None = None
    process: ProcessTelemetry | None = None
    user: UserSessionTelemetry | None = None
    network: NetworkConnectionTelemetry | None = None
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file.__dict__ if self.file is not None else None,
            "process": self.process.__dict__ if self.process is not None else None,
            "user": self.user.__dict__ if self.user is not None else None,
            "network": self.network.__dict__ if self.network is not None else None,
            "context": self.context,
        }


class EntropyHistory:
    def __init__(self) -> None:
        self.history: dict[str, float] = {}

    def record(self, path: str, entropy: float) -> None:
        self.history[path] = entropy

    def latest(self, path: str) -> float | None:
        return self.history.get(path)
