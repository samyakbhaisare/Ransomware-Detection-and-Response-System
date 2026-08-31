from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class FileEvent:
    path: str
    action: str
    previous_path: str | None = None
    new_path: str | None = None
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "action": self.action,
            "previous_path": self.previous_path,
            "new_path": self.new_path,
            "metadata": self.metadata or {},
        }


class FileActionDetector:
    def __init__(self, base_path: str | Path):
        self.base_path = Path(base_path)

    def detect(self, path: str | Path, action: str, *, previous_path: str | None = None, new_path: str | None = None) -> dict[str, Any]:
        resolved_path = Path(path)
        metadata = {
            "exists": resolved_path.exists(),
            "size": resolved_path.stat().st_size if resolved_path.exists() else 0,
            "sha256": self._hash(resolved_path) if resolved_path.exists() else None,
        }
        event = FileEvent(
            path=str(resolved_path),
            action=action,
            previous_path=previous_path,
            new_path=new_path,
            metadata=metadata,
        )
        return event.to_dict()

    @staticmethod
    def _hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()


class DirectoryMonitor:
    def __init__(self, root: str | Path, *, recursive: bool = True, excludes: list[str] | None = None):
        self.root = Path(root)
        self.recursive = recursive
        self.excludes = set(excludes or [])

    def scan(self) -> list[Path]:
        results: list[Path] = []
        iterator = self.root.rglob("*") if self.recursive else self.root.iterdir()
        for path in iterator:
            if path.is_dir():
                if any(part in self.excludes for part in path.parts):
                    continue
                continue
            if any(part in self.excludes for part in path.parts):
                continue
            results.append(path)
        return sorted(results)


class FileTypeClassifier:
    @staticmethod
    def classify(path: str | Path) -> str:
        suffix = Path(path).suffix.lower()
        if suffix in {".txt", ".md", ".log"}:
            return "text"
        if suffix in {".png", ".jpg", ".jpeg", ".gif"}:
            return "image"
        if suffix in {".bin", ".dat", ".dll", ".exe"}:
            return "binary"
        return "unknown"
