from __future__ import annotations

import hashlib
import os
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path

from rdrs.fileaccess import open_read_shared


@dataclass(frozen=True)
class QuarantineResult:
    source: str
    destination: str
    copied: bool
    simulation_mode: bool


class QuarantineManager:
    """Evidence quarantine with copy-only safety semantics."""

    #: How many disambiguated names to try before giving up. Collisions beyond
    #: this would require 1000 different files sharing a basename AND the first
    #: eight hex characters of their SHA-256.
    MAX_NAME_ATTEMPTS = 1000

    def __init__(
        self,
        quarantine_dir: str | Path,
        *,
        simulation_mode: bool = True,
    ) -> None:
        self.quarantine_dir = Path(quarantine_dir)
        self.simulation_mode = simulation_mode

        # Choosing a destination and copying into it must be atomic. Without
        # this, two threads quarantining files that share a basename both see
        # the name as free, both pick it, and one copy silently overwrites the
        # other -- destroying evidence.
        self._lock = threading.Lock()

    @staticmethod
    def _digest(path: Path) -> str:
        """Return the SHA-256 of a file's real contents."""
        digest = hashlib.sha256()

        with open_read_shared(path) as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)

        return digest.hexdigest()

    @classmethod
    def _identical(cls, first: Path, second: Path) -> bool:
        """Return whether two files hold exactly the same bytes."""
        try:
            if first.stat().st_size != second.stat().st_size:
                return False

            return cls._digest(first) == cls._digest(second)
        except OSError:
            return False

    @staticmethod
    def _is_same_file(first: Path, second: Path) -> bool:
        """Return whether two paths refer to the same file on disk."""
        try:
            return os.path.samefile(first, second)
        except OSError:
            try:
                return first.resolve() == second.resolve()
            except OSError:
                return False

    def _resolve_destination(
        self,
        source_path: Path,
        digest: str,
    ) -> Path:
        """Choose a destination that cannot destroy previously held evidence.

        Using the bare basename silently overwrote earlier evidence whenever
        two affected files shared a name -- which is the common case in a real
        incident (the same document name in many folders). Losing evidence
        defeats the purpose of quarantine, so:

        * a free basename is used as-is;
        * a basename already holding the *same* bytes is reused (identical
          evidence is not duplicated);
        * a basename holding *different* bytes is disambiguated with a content
          hash, so both copies survive.
        """
        base = self.quarantine_dir / source_path.name

        if not base.exists() or self._identical(base, source_path):
            return base

        stem = source_path.stem
        suffix = source_path.suffix
        short = digest[:8]

        candidate = self.quarantine_dir / f"{stem}__{short}{suffix}"

        if not candidate.exists() or self._identical(candidate, source_path):
            return candidate

        for index in range(2, self.MAX_NAME_ATTEMPTS):
            candidate = (
                self.quarantine_dir / f"{stem}__{short}_{index}{suffix}"
            )

            if not candidate.exists():
                return candidate

        raise OSError(
            "Could not find an unused quarantine name for "
            f"{source_path.name} after {self.MAX_NAME_ATTEMPTS} attempts"
        )

    def quarantine(
        self,
        source: str | Path,
    ) -> QuarantineResult:
        source_path = Path(source)

        if not source_path.exists():
            raise FileNotFoundError(
                f"Evidence file not found: {source_path}"
            )

        if not source_path.is_file():
            raise ValueError(
                f"Evidence path is not a file: {source_path}"
            )

        self.quarantine_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        digest = self._digest(source_path)

        # Destination selection and the copy form one critical section so that
        # concurrent quarantines cannot claim the same name.
        with self._lock:
            destination = self._resolve_destination(
                source_path,
                digest,
            )

            # A file that already lives in the vault is already quarantined.
            # Copying it onto itself would fail with an opaque OS sharing error
            # on Windows, so report the existing evidence instead of raising.
            if self._is_same_file(destination, source_path):
                return QuarantineResult(
                    source=str(source_path),
                    destination=str(destination),
                    copied=False,
                    simulation_mode=self.simulation_mode,
                )

            # Copy only. The original is never moved, renamed or modified.
            shutil.copy2(
                source_path,
                destination,
            )

        return QuarantineResult(
            source=str(source_path),
            destination=str(destination),
            copied=True,
            simulation_mode=self.simulation_mode,
        )
