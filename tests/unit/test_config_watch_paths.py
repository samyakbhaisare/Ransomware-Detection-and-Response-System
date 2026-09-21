"""FR-01 + Build Guide Phase 1 regression tests.

The defect this covers
----------------------
Build Guide Phase 1 requires the config loader to read **"which folders to
watch"** from ``config.yaml``. There was no such setting: the runtime always
monitored ``config.data_dir``, so a user could not point RDRS at the folders
they actually want protected -- the primary purpose of the tool.

Phase 1 also requires rotating logs (one each for system, events, alerts,
errors, audit) and Shannon entropy with a 64 KB sample returning 0.0-8.0.

Note on loguru: the guide says "The loguru library makes this easy" and lists it
in the install command. The project uses the standard-library ``logging`` module
with ``RotatingFileHandler`` instead, which produces the same required outcome
(five rotating log files) with no extra dependency. That substitution is
deliberate and documented; the outcome the guide requires is asserted below.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
import yaml

from rdrs.config import RuntimeConfig

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "rdrs.yaml"
    path.write_text(body, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# FR-01: "which folders to watch"
# --------------------------------------------------------------------------


def test_shipped_config_declares_watch_paths():
    """The FSD-named config file must state which folders to watch."""
    raw = yaml.safe_load(
        (PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8")
    )

    assert "watch_paths" in raw["runtime"], (
        "config.yaml does not declare runtime.watch_paths"
    )
    assert isinstance(raw["runtime"]["watch_paths"], list)
    assert raw["runtime"]["watch_paths"]


def test_relative_watch_paths_resolve_against_the_config_file(tmp_path):
    """A relative entry means the same folder regardless of the CWD."""
    config = RuntimeConfig.load(
        write_config(
            tmp_path,
            "runtime:\n  data_dir: data\n  watch_paths:\n    - watched\n",
        )
    )

    assert len(config.watch_paths) == 1
    resolved = config.watch_paths[0]

    assert resolved.is_absolute()
    assert resolved == (tmp_path / "watched")


def test_absolute_watch_paths_are_kept_as_given(tmp_path):
    target = tmp_path / "absolute_target"

    config = RuntimeConfig.load(
        write_config(
            tmp_path,
            "runtime:\n  data_dir: data\n"
            f"  watch_paths:\n    - {target.as_posix()}\n",
        )
    )

    assert config.watch_paths == [target]


def test_monitored_paths_falls_back_to_data_dir(tmp_path):
    """With nothing configured, the monitor still watches a real folder."""
    config = RuntimeConfig.load(
        write_config(tmp_path, "runtime:\n  data_dir: mydata\n")
    )

    assert config.watch_paths == []
    assert [p.name for p in config.monitored_paths] == ["mydata"]


def test_monitored_paths_prefers_the_configured_list(tmp_path):
    config = RuntimeConfig.load(
        write_config(
            tmp_path,
            "runtime:\n  data_dir: data\n"
            "  watch_paths:\n    - one\n    - two\n",
        )
    )

    assert [p.name for p in config.monitored_paths] == ["one", "two"]


def test_watch_paths_rejects_a_bare_string(tmp_path):
    with pytest.raises(ValueError, match="watch_paths"):
        RuntimeConfig.load(
            write_config(tmp_path, "runtime:\n  watch_paths: not-a-list\n")
        )


def test_watch_paths_skips_blank_entries(tmp_path):
    config = RuntimeConfig.load(
        write_config(
            tmp_path,
            "runtime:\n  data_dir: data\n"
            "  watch_paths:\n    - ''\n    - '   '\n    - real\n",
        )
    )

    assert [p.name for p in config.watch_paths] == ["real"]


def test_to_dict_exposes_watch_paths():
    config = RuntimeConfig.load(PROJECT_ROOT / "config.yaml")
    payload = config.to_dict()

    assert "watch_paths" in payload
    assert "monitored_paths" in payload
    assert payload["monitored_paths"]


# --------------------------------------------------------------------------
# FR-01 live: the runtime actually monitors every configured folder
# --------------------------------------------------------------------------


def test_runtime_monitors_every_configured_folder(tmp_path):
    """Two configured folders must both produce monitored events."""
    from rdrs.runtime import RuntimeManager

    for name in ("data", "logs", "watchedA", "watchedB"):
        (tmp_path / name).mkdir()

    base = (PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8")

    watch_block = (
        "  watch_paths:\n"
        f"    - {(tmp_path / 'watchedA').as_posix()}\n"
        f"    - {(tmp_path / 'watchedB').as_posix()}\n"
    )
    body = base.replace("  watch_paths:\n    - data\n", watch_block)
    assert body != base, "the watch_paths block was not replaced"

    body = (
        body.replace("data_dir: data", f"data_dir: {(tmp_path / 'data').as_posix()}")
        .replace("logs_dir: logs", f"logs_dir: {(tmp_path / 'logs').as_posix()}")
        .replace(
            "db_path: data\\rdrs.db",
            f"db_path: {(tmp_path / 'data' / 'rdrs.db').as_posix()}",
        )
    )

    config_path = tmp_path / "rdrs.yaml"
    config_path.write_text(body, encoding="utf-8")

    RuntimeManager.reset_singleton()
    runtime = RuntimeManager(config_path=config_path)

    try:
        runtime.start()
        time.sleep(1.0)

        assert len(runtime._monitors) == 2

        monitored = {Path(monitor.path).name for monitor in runtime._monitors}
        assert monitored == {"watchedA", "watchedB"}

        before = runtime._database.count_records("events")

        (tmp_path / "watchedA" / "a.txt").write_text("activity in A")
        (tmp_path / "watchedB" / "b.txt").write_text("activity in B")

        deadline = time.time() + 10.0
        while time.time() < deadline:
            if runtime._database.count_records("events") > before:
                break
            time.sleep(0.1)

        assert runtime._database.count_records("events") > before
    finally:
        runtime.stop()
        RuntimeManager.reset_singleton()

    assert runtime._monitors == []


def test_runtime_falls_back_to_data_dir_when_unset(tmp_path):
    """An unconfigured deployment must still monitor its own data dir."""
    from rdrs.runtime import RuntimeManager

    for name in ("data", "logs"):
        (tmp_path / name).mkdir()

    base = (PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8")
    body = (
        base.replace("  watch_paths:\n    - data\n", "")
        .replace("data_dir: data", f"data_dir: {(tmp_path / 'data').as_posix()}")
        .replace("logs_dir: logs", f"logs_dir: {(tmp_path / 'logs').as_posix()}")
        .replace(
            "db_path: data\\rdrs.db",
            f"db_path: {(tmp_path / 'data' / 'rdrs.db').as_posix()}",
        )
    )

    config_path = tmp_path / "rdrs.yaml"
    config_path.write_text(body, encoding="utf-8")

    RuntimeManager.reset_singleton()
    runtime = RuntimeManager(config_path=config_path)

    try:
        runtime.start()
        time.sleep(0.8)

        assert len(runtime._monitors) == 1
        assert Path(runtime._monitors[0].path) == tmp_path / "data"
    finally:
        runtime.stop()
        RuntimeManager.reset_singleton()


# --------------------------------------------------------------------------
# Phase 1: rotating logs
# --------------------------------------------------------------------------


def test_all_five_rotating_logs_are_configured(tmp_path):
    """system, events, alerts, errors and audit — each a rotating file."""
    from rdrs.logging import LOG_FILES, initialize_rotating_logs

    assert set(LOG_FILES) == {
        "system",
        "events",
        "alerts",
        "errors",
        "audit",
    }

    logs_dir = tmp_path / "logs"
    initialize_rotating_logs(logs_dir)

    for filename in LOG_FILES.values():
        assert (logs_dir / filename).is_file(), f"{filename} was not created"

    source = (PROJECT_ROOT / "src" / "rdrs" / "logging.py").read_text(
        encoding="utf-8-sig"
    )

    assert "RotatingFileHandler" in source
    assert "maxBytes" in source
    assert "backupCount" in source


# --------------------------------------------------------------------------
# Phase 1: entropy "Done when"
# --------------------------------------------------------------------------


def test_phase1_done_when_entropy_range(tmp_path):
    """A normal text file is low entropy; random bytes are close to 8.0."""
    import os

    from rdrs.entropy import calculate_file_entropy

    text = tmp_path / "normal.txt"
    text.write_text("normal document content " * 200, encoding="utf-8")

    random_file = tmp_path / "random.bin"
    random_file.write_bytes(os.urandom(64 * 1024))

    text_entropy = calculate_file_entropy(text)
    random_entropy = calculate_file_entropy(random_file)

    assert 0.0 <= text_entropy <= 5.0, text_entropy
    assert random_entropy >= 7.9, random_entropy


def test_entropy_samples_only_the_first_64kb(tmp_path):
    """Sampling keeps entropy fast on large files."""
    from rdrs.entropy import MAX_SAMPLE_SIZE, calculate_file_entropy

    assert MAX_SAMPLE_SIZE == 64 * 1024

    path = tmp_path / "big.bin"
    path.write_bytes(b"A" * (5 * 1024 * 1024))

    started = time.perf_counter()
    value = calculate_file_entropy(path)
    elapsed = time.perf_counter() - started

    assert value == pytest.approx(0.0)
    assert elapsed < 1.0
