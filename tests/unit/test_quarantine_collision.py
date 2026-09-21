"""FR-09 regression tests — quarantine must never destroy evidence.

The defect this covers
----------------------
``QuarantineManager.quarantine`` wrote to ``quarantine_dir / source.name`` with
no collision handling, and ``shutil.copy2`` overwrites. Two affected files
sharing a basename therefore collapsed onto one destination and the first piece
of evidence was silently destroyed. That is the common case in a real incident:
the same document name appears in many folders.

A second defect: choosing the destination and copying were not atomic, so
concurrent quarantines claimed the same name and overwrote each other.

A third: re-quarantining a file already in the vault raised an opaque
``PermissionError [WinError 32]`` because the copy was onto itself.

All three are covered here, together with the safety properties the Build Guide
and Locked Rules require: copy-only semantics (the original is never moved,
renamed or modified), metadata preservation, simulation mode, error handling,
and destination containment.
"""

from __future__ import annotations

import hashlib
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from rdrs.quarantine import QuarantineManager


@pytest.fixture
def vault(tmp_path):
    return tmp_path / "vault"


def make_file(directory: Path, name: str, payload: bytes) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(payload)
    return path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------
# collision safety
# --------------------------------------------------------------------------


def test_collision_with_different_content_preserves_both(vault, tmp_path):
    """Two different files sharing a name must both survive."""
    first = make_file(tmp_path / "one", "report.docx", b"FIRST-REPORT" * 200)
    second = make_file(tmp_path / "two", "report.docx", b"SECOND-REPORT" * 200)

    manager = QuarantineManager(vault, simulation_mode=True)

    result_a = manager.quarantine(first)
    result_b = manager.quarantine(second)

    assert result_a.destination != result_b.destination

    # Both payloads still exist in the vault, byte for byte.
    stored = {digest(path) for path in vault.iterdir()}
    assert digest(first) in stored
    assert digest(second) in stored
    assert len(list(vault.iterdir())) == 2


def test_collision_with_identical_content_is_deduplicated(vault, tmp_path):
    """The same bytes quarantined twice must not be duplicated."""
    first = make_file(tmp_path / "one", "same.bin", b"IDENTICAL" * 300)
    second = make_file(tmp_path / "two", "same.bin", b"IDENTICAL" * 300)

    manager = QuarantineManager(vault, simulation_mode=True)

    result_a = manager.quarantine(first)
    result_b = manager.quarantine(second)

    assert result_a.destination == result_b.destination
    assert len(list(vault.iterdir())) == 1


def test_many_sequential_collisions_are_all_preserved(vault, tmp_path):
    """Twenty differently-named directories, one shared basename."""
    manager = QuarantineManager(vault, simulation_mode=True)

    sources = [
        make_file(tmp_path / f"dir{index}", "shared.txt", bytes([65 + index]) * 400)
        for index in range(20)
    ]

    results = [manager.quarantine(source) for source in sources]

    assert len({result.destination for result in results}) == 20
    assert len(list(vault.iterdir())) == 20

    stored = {digest(path) for path in vault.iterdir()}
    for source in sources:
        assert digest(source) in stored, f"evidence lost for {source}"


def test_concurrent_collisions_are_all_preserved(vault, tmp_path):
    """Concurrent quarantines of the same basename must not race.

    Without an atomic resolve-then-copy, two threads both see the name as free,
    both choose it, and one copy overwrites the other.
    """
    manager = QuarantineManager(vault, simulation_mode=True)

    sources = [
        make_file(tmp_path / f"d{index}", "race.txt", bytes([65 + index % 26]) * 300 + str(index).encode())
        for index in range(24)
    ]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(manager.quarantine, sources))

    assert len({result.destination for result in results}) == len(sources)
    assert len(list(vault.iterdir())) == len(sources)

    stored = {digest(path) for path in vault.iterdir()}
    for source in sources:
        assert digest(source) in stored, f"evidence lost for {source}"


def test_concurrent_identical_content_stays_deduplicated(vault, tmp_path):
    """Concurrency must not defeat deduplication of identical bytes."""
    manager = QuarantineManager(vault, simulation_mode=True)

    sources = [
        make_file(tmp_path / f"i{index}", "dup.bin", b"SAME-BYTES" * 400)
        for index in range(12)
    ]

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(manager.quarantine, sources))

    assert len({result.destination for result in results}) == 1
    assert len(list(vault.iterdir())) == 1


# --------------------------------------------------------------------------
# repeated quarantine / idempotence
# --------------------------------------------------------------------------


def test_repeated_quarantine_of_the_same_source_is_stable(vault, tmp_path):
    source = make_file(tmp_path, "repeat.txt", b"REPEAT" * 200)
    manager = QuarantineManager(vault, simulation_mode=True)

    first = manager.quarantine(source)
    second = manager.quarantine(source)
    third = manager.quarantine(source)

    assert first.destination == second.destination == third.destination
    assert len(list(vault.iterdir())) == 1
    assert digest(Path(first.destination)) == digest(source)


def test_requarantining_a_vault_file_is_a_safe_noop(vault, tmp_path):
    """A file already in the vault must not raise a sharing error."""
    source = make_file(tmp_path, "already.txt", b"ALREADY" * 200)
    manager = QuarantineManager(vault, simulation_mode=True)

    first = manager.quarantine(source)
    assert first.copied is True

    again = manager.quarantine(Path(first.destination))

    assert again.copied is False
    assert again.destination == first.destination
    assert len(list(vault.iterdir())) == 1


# --------------------------------------------------------------------------
# copy-only safety: the original is never touched
# --------------------------------------------------------------------------


def test_original_is_never_moved_renamed_or_modified(vault, tmp_path):
    source = make_file(tmp_path / "originals", "keep.txt", b"ORIGINAL" * 300)
    before_bytes = source.read_bytes()
    before_stat = source.stat()

    manager = QuarantineManager(vault, simulation_mode=True)
    result = manager.quarantine(source)

    assert source.is_file(), "the original was moved or deleted"
    assert source.read_bytes() == before_bytes, "the original was modified"
    assert source.stat().st_size == before_stat.st_size
    assert Path(result.destination).is_file()
    assert Path(result.destination) != source


def test_originals_survive_every_collision(vault, tmp_path):
    manager = QuarantineManager(vault, simulation_mode=True)

    sources = [
        make_file(tmp_path / f"o{index}", "shared.txt", bytes([70 + index]) * 200)
        for index in range(10)
    ]
    before = {source: source.read_bytes() for source in sources}

    for source in sources:
        manager.quarantine(source)

    for source, payload in before.items():
        assert source.is_file()
        assert source.read_bytes() == payload


# --------------------------------------------------------------------------
# integrity and metadata
# --------------------------------------------------------------------------


def test_quarantined_copy_is_byte_identical(vault, tmp_path):
    payload = os.urandom(64_000)
    source = make_file(tmp_path, "payload.bin", payload)

    manager = QuarantineManager(vault, simulation_mode=True)
    result = manager.quarantine(source)

    assert Path(result.destination).read_bytes() == payload
    assert digest(Path(result.destination)) == digest(source)


def test_modification_time_is_preserved(vault, tmp_path):
    """copy2 preserves metadata, which matters for incident timelines."""
    source = make_file(tmp_path, "timed.txt", b"TIMED" * 200)

    old = time.time() - 3600
    os.utime(source, (old, old))

    manager = QuarantineManager(vault, simulation_mode=True)
    result = manager.quarantine(source)

    copied = Path(result.destination)
    assert abs(copied.stat().st_mtime - source.stat().st_mtime) < 2.0


def test_large_file_is_quarantined_intact(vault, tmp_path):
    payload = os.urandom(3 * 1024 * 1024)
    source = make_file(tmp_path, "large.bin", payload)

    manager = QuarantineManager(vault, simulation_mode=True)
    result = manager.quarantine(source)

    copied = Path(result.destination)
    assert copied.stat().st_size == len(payload)
    assert digest(copied) == hashlib.sha256(payload).hexdigest()


def test_empty_file_is_quarantined(vault, tmp_path):
    source = make_file(tmp_path, "empty.txt", b"")

    manager = QuarantineManager(vault, simulation_mode=True)
    result = manager.quarantine(source)

    assert Path(result.destination).is_file()
    assert Path(result.destination).read_bytes() == b""


# --------------------------------------------------------------------------
# simulation mode
# --------------------------------------------------------------------------


def test_simulation_mode_defaults_to_on(vault, tmp_path):
    source = make_file(tmp_path, "sim.txt", b"SIM" * 100)

    result = QuarantineManager(vault).quarantine(source)

    assert result.simulation_mode is True


def test_simulation_mode_is_reported_accurately(vault, tmp_path):
    source = make_file(tmp_path, "sim2.txt", b"SIM" * 100)

    assert QuarantineManager(vault, simulation_mode=True).quarantine(source).simulation_mode is True

    other_vault = tmp_path / "vault2"
    source2 = make_file(tmp_path, "sim3.txt", b"SIM" * 100)

    assert (
        QuarantineManager(other_vault, simulation_mode=False)
        .quarantine(source2)
        .simulation_mode
        is False
    )


def test_simulation_mode_does_not_weaken_copy_only(vault, tmp_path):
    """Simulation mode must still copy evidence and preserve the original."""
    source = make_file(tmp_path, "simcopy.txt", b"SIM-COPY" * 200)

    result = QuarantineManager(vault, simulation_mode=True).quarantine(source)

    assert source.is_file()
    assert Path(result.destination).read_bytes() == source.read_bytes()


# --------------------------------------------------------------------------
# error handling
# --------------------------------------------------------------------------


def test_missing_source_raises_file_not_found(vault, tmp_path):
    manager = QuarantineManager(vault, simulation_mode=True)

    with pytest.raises(FileNotFoundError):
        manager.quarantine(tmp_path / "does-not-exist.txt")


def test_directory_source_raises_value_error(vault, tmp_path):
    directory = tmp_path / "a-directory"
    directory.mkdir()

    manager = QuarantineManager(vault, simulation_mode=True)

    with pytest.raises(ValueError):
        manager.quarantine(directory)


def test_vault_is_created_when_missing(tmp_path):
    missing_vault = tmp_path / "nested" / "deeper" / "vault"
    assert not missing_vault.exists()

    source = make_file(tmp_path, "create.txt", b"CREATE" * 100)
    result = QuarantineManager(missing_vault, simulation_mode=True).quarantine(source)

    assert missing_vault.is_dir()
    assert Path(result.destination).is_file()


# --------------------------------------------------------------------------
# destination containment (no traversal, no escapes)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "normal.txt",
        "with spaces.txt",
        "dots.in.name.tar.gz",
        "..leading-dots",
        "unicode-\u00e9\u00e8\u00ea.txt",
        "semi;colon.txt",
        "a" * 120 + ".txt",
    ],
)
def test_destination_always_stays_inside_the_vault(vault, tmp_path, name):
    source = make_file(tmp_path / "sub", name, b"CONTAINED" * 100)

    result = QuarantineManager(vault, simulation_mode=True).quarantine(source)

    destination = Path(result.destination).resolve()
    assert destination.parent == vault.resolve(), "destination escaped the vault"
    assert destination.is_file()


def test_source_directory_is_not_mirrored_into_the_vault(vault, tmp_path):
    """Only the file's own name is used, never its directory structure."""
    source = make_file(tmp_path / "deep" / "nested" / "path", "leaf.txt", b"LEAF" * 100)

    result = QuarantineManager(vault, simulation_mode=True).quarantine(source)

    destination = Path(result.destination)
    assert destination.parent.resolve() == vault.resolve()
    assert destination.name == "leaf.txt"


def test_no_partial_or_temporary_files_are_left_behind(vault, tmp_path):
    manager = QuarantineManager(vault, simulation_mode=True)

    for index in range(5):
        manager.quarantine(
            make_file(tmp_path / f"t{index}", "shared.txt", bytes([80 + index]) * 200)
        )

    leftovers = [
        path.name
        for path in vault.iterdir()
        if path.suffix in {".tmp", ".part"} or path.name.startswith(".~")
    ]

    assert leftovers == []
    assert len(list(vault.iterdir())) == 5


# --------------------------------------------------------------------------
# integration with the response pipeline
# --------------------------------------------------------------------------


def test_response_engine_records_each_quarantined_file_separately(vault, tmp_path):
    """The incident's quarantined-file list must hold distinct evidence paths.

    ``IncidentResponseEngine`` appends ``result.destination`` for every file it
    quarantines, so colliding destinations would also corrupt the incident
    record.
    """
    from rdrs.response import IncidentResponseEngine
    from rdrs.scoring import ThreatScore
    from rdrs.storage import DatabaseManager

    database = DatabaseManager(tmp_path / "response.db")
    database.initialize()

    engine = IncidentResponseEngine(
        database=database,
        quarantine_dir=vault,
        simulation_mode=True,
    )

    first = make_file(tmp_path / "a", "affected.docx", b"FIRST" * 400)
    second = make_file(tmp_path / "b", "affected.docx", b"SECOND" * 400)

    result = engine.process(
        ThreatScore(
            rapid_encryption=40,
            mass_rename=30,
            high_entropy=25,
        ),
        affected_files=[str(first), str(second)],
        suspect_process={"name": "probe.exe", "pid": 4321},
        reason="test burst",
    )

    quarantined = list(result.quarantined_files)

    assert len(quarantined) == 2
    assert len(set(quarantined)) == 2, "colliding destinations reached the incident"
    assert len(list(vault.iterdir())) == 2

    stored = {digest(path) for path in vault.iterdir()}
    assert digest(first) in stored
    assert digest(second) in stored

    # The originals are untouched.
    assert first.is_file() and second.is_file()

    database.close()
