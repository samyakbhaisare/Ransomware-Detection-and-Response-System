from __future__ import annotations

import hashlib
import mimetypes
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from rdrs.entropy import calculate_entropy
from rdrs.fileaccess import open_read_shared

__all__ = [
    "DirectoryMonitor",
    "FileActionDetector",
    "FileEvent",
    "RealtimeFileMonitor",
    "calculate_file_metadata",
    "is_excluded_path",
    "open_read_shared",
]


MAX_ENTROPY_SAMPLE_SIZE = 64 * 1024


@dataclass(frozen=True)
class FileEvent(Mapping[str, Any]):
    """Normalized RDRS filesystem event."""

    action: str
    path: str
    timestamp: float = field(default_factory=time.time)
    extension: str = ""
    previous_path: str | None = None
    new_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def _as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "path": self.path,
            "timestamp": self.timestamp,
            "extension": self.extension,
            "previous_path": self.previous_path,
            "new_path": self.new_path,
            "metadata": self.metadata,
        }

    def __getitem__(self, key: str) -> Any:
        return self._as_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._as_dict())

    def __len__(self) -> int:
        return len(self._as_dict())


def is_excluded_path(
    path: str | Path,
    excludes: tuple[str, ...] | list[str],
) -> bool:
    """Return whether a path matches any exclude pattern.

    The Build Guide asks for *exclude patterns* ("for example, ignore temporary
    files and system files"), so glob patterns must work. Matching only exact
    path components silently ignored every pattern a user would naturally
    write: ``*.tmp`` never equalled a component named ``skip.tmp``.

    A pattern is tested against the file name itself (``*.tmp``, ``rdrs.db``)
    and against every path component, so a directory name such as
    ``quarantine`` excludes everything beneath it.
    """
    if not excludes:
        return False

    candidate = Path(str(path))

    name = candidate.name.lower()
    parts = [part.lower() for part in candidate.parts]

    for raw_pattern in excludes:
        pattern = str(raw_pattern).strip().lower()

        if not pattern:
            continue

        if fnmatch(name, pattern):
            return True

        if any(fnmatch(part, pattern) for part in parts):
            return True

    return False


def _sha256(path: Path) -> str:
    """Calculate SHA-256 from real file contents."""

    digest = hashlib.sha256()

    with open_read_shared(path) as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def calculate_file_metadata(
    path: str | Path,
    *,
    entropy_sample_size: int = MAX_ENTROPY_SAMPLE_SIZE,
) -> dict[str, Any]:
    """Collect real filesystem metadata."""

    file_path = Path(path)

    metadata: dict[str, Any] = {
        "path": str(file_path),
        "exists": file_path.exists(),
        "is_file": file_path.is_file(),
        "is_directory": file_path.is_dir(),
        "size": 0,
        "extension": file_path.suffix.lower(),
        "file_type": (
            mimetypes.guess_type(str(file_path))[0]
            or "application/octet-stream"
        ),
        "sha256": None,
        "entropy": None,
        "created_at": None,
        "modified_at": None,
    }

    if not file_path.exists():
        return metadata

    try:
        stat = file_path.stat()

        metadata["size"] = stat.st_size
        metadata["created_at"] = stat.st_ctime
        metadata["modified_at"] = stat.st_mtime

        if file_path.is_file():
            metadata["sha256"] = _sha256(file_path)

            # Read the entropy sample through the shared handle rather than
            # file_entropy(), which would open the file again without
            # FILE_SHARE_DELETE.
            with open_read_shared(file_path) as handle:
                sample = handle.read(entropy_sample_size)

            metadata["entropy"] = calculate_entropy(sample)

    except (OSError, ValueError):
        pass

    return metadata


class FileActionDetector:
    """Detect and normalize filesystem actions."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        entropy_sample_size: int = MAX_ENTROPY_SAMPLE_SIZE,
    ) -> None:
        self.root = Path(root) if root is not None else None
        self.entropy_sample_size = entropy_sample_size

    def detect(
        self,
        path_or_event: str | Path | Any,
        action: str | None = None,
        *,
        previous_path: str | None = None,
        new_path: str | None = None,
        timestamp: float | None = None,
    ) -> FileEvent | None:
        """Normalize watchdog events or direct test input."""

        if action is None and hasattr(path_or_event, "event_type"):
            return self._detect_watchdog_event(
                path_or_event,
                timestamp=timestamp,
            )

        path = Path(path_or_event)
        normalized_action = self._normalize_action(action)

        if normalized_action is None:
            return None

        event_time = (
            time.time()
            if timestamp is None
            else float(timestamp)
        )

        if normalized_action == "created":
            metadata = calculate_file_metadata(
                path,
                entropy_sample_size=self.entropy_sample_size,
            )

            return FileEvent(
                action="create",
                path=str(path),
                timestamp=event_time,
                extension=path.suffix.lower(),
                metadata=metadata,
            )

        if normalized_action == "modified":
            metadata = calculate_file_metadata(
                path,
                entropy_sample_size=self.entropy_sample_size,
            )

            return FileEvent(
                action="modify",
                path=str(path),
                timestamp=event_time,
                extension=path.suffix.lower(),
                metadata=metadata,
            )

        if normalized_action == "deleted":
            return FileEvent(
                action="delete",
                path=str(path),
                timestamp=event_time,
                extension=path.suffix.lower(),
                metadata={
                    "path": str(path),
                    "exists": False,
                    "is_file": False,
                    "is_directory": False,
                    "size": 0,
                    "extension": path.suffix.lower(),
                    "sha256": None,
                    "entropy": None,
                    "created_at": None,
                    "modified_at": None,
                },
            )

        if normalized_action == "renamed":
            destination = Path(new_path or path)

            metadata = calculate_file_metadata(
                destination,
                entropy_sample_size=self.entropy_sample_size,
            )

            return FileEvent(
                action="rename",
                path=str(destination),
                timestamp=event_time,
                extension=destination.suffix.lower(),
                previous_path=(
                    str(previous_path)
                    if previous_path is not None
                    else None
                ),
                new_path=str(destination),
                metadata=metadata,
            )

        return None

    def _detect_watchdog_event(
        self,
        event: Any,
        *,
        timestamp: float | None = None,
    ) -> FileEvent | None:
        event_type = str(
            getattr(event, "event_type", "")
        ).lower()

        if getattr(event, "is_directory", False):
            return None

        src_path = str(
            getattr(event, "src_path", "")
            or ""
        )

        if event_type == "created":
            result = self.detect(
                src_path,
                "create",
                timestamp=timestamp,
            )
            return self._watchdog_result(result, "created")

        if event_type == "modified":
            result = self.detect(
                src_path,
                "modify",
                timestamp=timestamp,
            )
            return self._watchdog_result(result, "modified")

        if event_type == "deleted":
            result = self.detect(
                src_path,
                "delete",
                timestamp=timestamp,
            )
            return self._watchdog_result(result, "deleted")

        if event_type == "moved":
            dest_path = str(
                getattr(event, "dest_path", "")
                or ""
            )

            result = self.detect(
                dest_path,
                "rename",
                previous_path=src_path,
                new_path=dest_path,
                timestamp=timestamp,
            )
            return self._watchdog_result(result, "renamed")

        return None

    @staticmethod
    def _watchdog_result(
        event: FileEvent | None,
        action: str,
    ) -> FileEvent | None:
        """Return watchdog-facing event names without changing direct API."""

        if event is None:
            return None

        return FileEvent(
            action=action,
            path=event.path,
            timestamp=event.timestamp,
            extension=event.extension,
            previous_path=event.previous_path,
            new_path=event.new_path,
            metadata=event.metadata,
        )

    @staticmethod
    def _normalize_action(action: str | None) -> str | None:
        if action is None:
            return None

        value = str(action).strip().lower()

        aliases = {
            "create": "created",
            "created": "created",
            "modify": "modified",
            "modified": "modified",
            "delete": "deleted",
            "deleted": "deleted",
            "rename": "renamed",
            "renamed": "renamed",
            "move": "renamed",
            "moved": "renamed",
        }

        return aliases.get(value)


class DirectoryMonitor:
    """Recursive real-time and snapshot directory monitor."""

    def __init__(
        self,
        path: str | Path,
        callback: Callable[[FileEvent], None] | None = None,
        *,
        recursive: bool = True,
        excludes: list[str] | tuple[str, ...] | None = None,
        database: Any | None = None,
    ) -> None:
        self.path = Path(path)
        self.callback = callback
        self.recursive = bool(recursive)
        self.excludes = tuple(
            str(item)
            for item in (excludes or ())
        )
        self.database = database

        self.detector = FileActionDetector(self.path)

        self.observer = Observer()
        self.event_count = 0
        self._running = False
        self._handler: _WatchdogHandler | None = None

    def start(self) -> None:
        if self._running:
            return

        self.path.mkdir(
            parents=True,
            exist_ok=True,
        )

        self._handler = _WatchdogHandler(
            detector=self.detector,
            callback=self._handle_event,
            excludes=self.excludes,
        )

        self.observer.schedule(
            self._handler,
            str(self.path),
            recursive=self.recursive,
        )

        self.observer.start()
        self._running = True

    def stop(self) -> None:
        if not self._running:
            return

        self.observer.stop()
        self.observer.join(timeout=5)

        self._running = False
        self._handler = None

    def scan(self) -> list[Path]:
        """Return real files under the monitored directory."""

        if not self.path.exists():
            return []

        if self.path.is_file():
            return [self.path]

        iterator = (
            self.path.rglob("*")
            if self.recursive
            else self.path.glob("*")
        )

        results: list[Path] = []

        for candidate in iterator:
            if not candidate.is_file():
                continue

            if self._is_excluded(candidate):
                continue

            results.append(candidate)

        return sorted(results)

    def _is_excluded(self, path: Path) -> bool:
        return is_excluded_path(path, self.excludes)

    def _handle_event(self, event: FileEvent) -> None:
        self.event_count += 1

        self._persist(event)

        if self.callback is not None:
            self.callback(event)

    def _persist(self, event: FileEvent) -> None:
        """Persist the real event using the DatabaseManager contract."""

        if self.database is None:
            return

        method = getattr(
            self.database,
            "record_file_event",
            None,
        )

        if method is None:
            return

        payload = {
            "path": event.path,
            "action": event.action,
            "previous_path": event.previous_path,
            "new_path": event.new_path,
            "metadata": event.metadata,
            "timestamp": event.timestamp,
            "extension": event.extension,
        }

        # Persisting is best-effort, but it must never be *fatal*. This runs on
        # a watchdog observer thread, and an exception escaping here kills that
        # thread -- the monitor then silently stops recording events.
        #
        # SQLite can reject a commit made concurrently with another statement
        # ("cannot commit transaction - SQL statements in progress"), which
        # arrives as sqlalchemy.exc.OperationalError rather than one of the
        # builtin types caught previously. Retry briefly, then give up quietly.
        import time as _time

        for attempt in range(3):
            try:
                method(payload)
                return
            except Exception:
                if attempt == 2:
                    return

                _time.sleep(0.05 * (attempt + 1))

    @property
    def running(self) -> bool:
        return self._running

    def __enter__(self) -> "DirectoryMonitor":
        self.start()
        return self

    def __exit__(
        self,
        exc_type: Any,
        exc_value: Any,
        traceback: Any,
    ) -> None:
        self.stop()


class FileTypeClassifier:
    """Classify files by extension or MIME type."""

    EXTENSION_TYPES = {
        ".txt": "text",
        ".log": "text",
        ".csv": "text",
        ".json": "data",
        ".yaml": "data",
        ".yml": "data",
        ".xml": "data",
        ".py": "source",
        ".js": "source",
        ".ts": "source",
        ".html": "web",
        ".css": "web",
        ".md": "document",
        ".pdf": "document",
        ".doc": "document",
        ".docx": "document",
        ".xls": "spreadsheet",
        ".xlsx": "spreadsheet",
        ".ppt": "presentation",
        ".pptx": "presentation",
        ".jpg": "image",
        ".jpeg": "image",
        ".png": "image",
        ".gif": "image",
        ".bmp": "image",
        ".zip": "archive",
        ".rar": "archive",
        ".7z": "archive",
        ".tar": "archive",
        ".gz": "archive",
        ".exe": "executable",
        ".msi": "executable",
        ".dll": "executable",
        ".bin": "binary",
        ".locked": "suspicious",
    }

    @classmethod
    def classify(cls, path: str | Path) -> str:
        extension = Path(path).suffix.lower()

        if extension in cls.EXTENSION_TYPES:
            return cls.EXTENSION_TYPES[extension]

        mime_type, _ = mimetypes.guess_type(str(path))

        if mime_type is None:
            return "unknown"

        if mime_type.startswith("text/"):
            return "text"

        if mime_type.startswith("image/"):
            return "image"

        if mime_type.startswith("audio/"):
            return "audio"

        if mime_type.startswith("video/"):
            return "video"

        if mime_type.startswith("application/"):
            return "application"

        return "unknown"


class _WatchdogHandler(FileSystemEventHandler):
    """Convert watchdog events into RDRS events."""

    def __init__(
        self,
        detector: FileActionDetector,
        callback: Callable[[FileEvent], None],
        excludes: tuple[str, ...],
    ) -> None:
        super().__init__()
        self.detector = detector
        self.callback = callback
        self.excludes = tuple(
            item.lower()
            for item in excludes
        )

    def _excluded(self, path: str) -> bool:
        return is_excluded_path(path, self.excludes)

    def _emit(self, raw_event: Any) -> None:
        src_path = str(
            getattr(raw_event, "src_path", "")
            or ""
        )

        if self._excluded(src_path):
            return

        if getattr(raw_event, "event_type", "") == "moved":
            dest_path = str(
                getattr(raw_event, "dest_path", "")
                or ""
            )

            if self._excluded(dest_path):
                return

        try:
            event = self.detector.detect(raw_event)

            if event is not None:
                self.callback(event)

        except (OSError, ValueError, TypeError):
            return

    def on_created(self, event: Any) -> None:
        self._emit(event)

    def on_modified(self, event: Any) -> None:
        self._emit(event)

    def on_deleted(self, event: Any) -> None:
        self._emit(event)

    def on_moved(self, event: Any) -> None:
        self._emit(event)


class RealtimeFileMonitor:
    """Real-time filesystem monitor with callback and SQLite persistence."""

    def __init__(
        self,
        path: str | Path,
        callback: Callable[[FileEvent], None] | None = None,
        database: Any | None = None,
        *,
        recursive: bool = True,
        excludes: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        self.path = Path(path)
        self.callback = callback
        self.database = database

        self.monitor = DirectoryMonitor(
            self.path,
            callback=self._handle_event,
            recursive=recursive,
            excludes=excludes,
            database=database,
        )

        self.event_count = 0

    def start(self) -> None:
        self.monitor.start()

    def stop(self) -> None:
        self.monitor.stop()

    @property
    def running(self) -> bool:
        return self.monitor.running

    def _handle_event(self, event: FileEvent) -> None:
        self.event_count += 1

        if self.callback is not None:
            self.callback(event)

    def __enter__(self) -> "RealtimeFileMonitor":
        self.start()
        return self

    def __exit__(
        self,
        exc_type: Any,
        exc_value: Any,
        traceback: Any,
    ) -> None:
        self.stop()

