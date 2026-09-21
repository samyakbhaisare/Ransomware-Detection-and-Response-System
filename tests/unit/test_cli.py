from __future__ import annotations

import sys
from pathlib import Path

import pytest

from rdrs import cli


def make_status(
    state="STOPPED",
    healthy=True,
    subsystems=None,
):
    return {
        "state": state,
        "healthy": healthy,
        "subsystems": subsystems or {},
    }


def test_build_runtime(monkeypatch):
    """The CLI must load the same configuration file the runtime resolves.

    FSD FR-1 names ``config.yaml``, so the CLI delegates to
    ``RuntimeManager.default_config_path()`` rather than hard-coding a path.
    """
    captured = {}
    resolved = {"path": None}

    class FakeRuntime:
        def __init__(
            self,
            config_path=None,
            data_dir=None,
            logs_dir=None,
            db_path=None,
        ):
            captured["config_path"] = config_path
            captured["data_dir"] = data_dir
            captured["logs_dir"] = logs_dir
            captured["db_path"] = db_path

        @staticmethod
        def default_config_path():
            from rdrs.runtime import RuntimeManager

            path = RuntimeManager.default_config_path()
            resolved["path"] = path
            return path

    monkeypatch.setattr(
        cli,
        "RuntimeManager",
        FakeRuntime,
    )

    result = cli._build_runtime()

    assert isinstance(result, FakeRuntime)
    assert Path(captured["config_path"]) == resolved["path"]
    assert Path(captured["config_path"]).name == "config.yaml"

    # The CLI must NOT force the data/logs/database paths. Pinning them to the
    # project root meant RDRS_CONFIG_PATH changed which configuration was read
    # but not which database was used, so the CLI silently operated on the
    # project's own database. The configuration decides, as it does for the API.
    assert captured["data_dir"] is None
    assert captured["logs_dir"] is None
    assert captured["db_path"] is None


def test_build_runtime_uses_environment_config(monkeypatch):
    """RDRS_CONFIG_PATH overrides the resolved default configuration."""
    custom_config = r"C:\temp\rdrs-test.yaml"
    captured = {}

    class FakeRuntime:
        def __init__(
            self,
            config_path=None,
            data_dir=None,
            logs_dir=None,
            db_path=None,
        ):
            captured["config_path"] = config_path

        @staticmethod
        def default_config_path():
            from rdrs.runtime import RuntimeManager

            return RuntimeManager.default_config_path()

    monkeypatch.setattr(
        cli,
        "RuntimeManager",
        FakeRuntime,
    )
    monkeypatch.setenv(
        "RDRS_CONFIG_PATH",
        custom_config,
    )

    cli._build_runtime()

    assert captured["config_path"] == custom_config


def test_print_status(capsys):
    status = make_status(
        state="STOPPED",
        healthy=True,
        subsystems={
            "database": {
                "healthy": True,
                "critical": False,
            },
            "monitor": {
                "healthy": False,
                "critical": True,
            },
        },
    )

    cli._print_status(status)

    output = capsys.readouterr().out

    assert "state=STOPPED" in output
    assert "healthy=True" in output
    assert "subsystem=database healthy=True critical=False" in output
    assert "subsystem=monitor healthy=False critical=True" in output


def test_print_status_empty_subsystems(capsys):
    cli._print_status(
        make_status(
            state="RUNNING",
            healthy=True,
            subsystems={},
        )
    )

    output = capsys.readouterr().out

    assert "state=RUNNING" in output
    assert "healthy=True" in output


def test_main_status(monkeypatch, capsys):
    class FakeRuntime:
        def status(self):
            return make_status(
                state="STOPPED",
                healthy=True,
            )

    monkeypatch.setattr(
        cli,
        "_build_runtime",
        lambda: FakeRuntime(),
    )

    result = cli.main(["status"])

    output = capsys.readouterr().out

    assert result == 0
    assert "state=STOPPED" in output


def test_main_default_status(monkeypatch, capsys):
    class FakeRuntime:
        def status(self):
            return make_status(
                state="STOPPED",
                healthy=True,
            )

    monkeypatch.setattr(
        cli,
        "_build_runtime",
        lambda: FakeRuntime(),
    )

    result = cli.main([])

    output = capsys.readouterr().out

    assert result == 0
    assert "state=STOPPED" in output


def test_main_start(monkeypatch, capsys):
    calls = []

    class FakeRuntime:
        def start(self):
            calls.append("start")
            return make_status(
                state="RUNNING",
                healthy=True,
            )

    monkeypatch.setattr(
        cli,
        "_build_runtime",
        lambda: FakeRuntime(),
    )

    result = cli.main(["start"])

    output = capsys.readouterr().out

    assert result == 0
    assert calls == ["start"]
    assert "state=RUNNING" in output


def test_main_stop(monkeypatch, capsys):
    calls = []

    class FakeRuntime:
        def stop(self):
            calls.append("stop")
            return make_status(
                state="STOPPED",
                healthy=True,
            )

    monkeypatch.setattr(
        cli,
        "_build_runtime",
        lambda: FakeRuntime(),
    )

    result = cli.main(["stop"])

    output = capsys.readouterr().out

    assert result == 0
    assert calls == ["stop"]
    assert "state=STOPPED" in output


def test_main_runtime_error(monkeypatch, capsys):
    class FakeRuntime:
        def status(self):
            raise RuntimeError("runtime failure")

    monkeypatch.setattr(
        cli,
        "_build_runtime",
        lambda: FakeRuntime(),
    )

    result = cli.main(["status"])

    captured = capsys.readouterr()

    assert result == 1
    assert "ERROR: runtime failure" in captured.err


def test_main_generic_exception(monkeypatch, capsys):
    class FakeRuntime:
        def status(self):
            raise ValueError("unexpected failure")

    monkeypatch.setattr(
        cli,
        "_build_runtime",
        lambda: FakeRuntime(),
    )

    result = cli.main(["status"])

    captured = capsys.readouterr()

    assert result == 1
    assert "ERROR: unexpected failure" in captured.err


def test_main_start_runtime_error(monkeypatch, capsys):
    class FakeRuntime:
        def start(self):
            raise RuntimeError("start failed")

    monkeypatch.setattr(
        cli,
        "_build_runtime",
        lambda: FakeRuntime(),
    )

    result = cli.main(["start"])

    captured = capsys.readouterr()

    assert result == 1
    assert "ERROR: start failed" in captured.err


def test_main_stop_runtime_error(monkeypatch, capsys):
    class FakeRuntime:
        def stop(self):
            raise RuntimeError("stop failed")

    monkeypatch.setattr(
        cli,
        "_build_runtime",
        lambda: FakeRuntime(),
    )

    result = cli.main(["stop"])

    captured = capsys.readouterr()

    assert result == 1
    assert "ERROR: stop failed" in captured.err


def test_invalid_command():
    with pytest.raises(SystemExit):
        cli.main(["invalid-command"])


def test_help():
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])

    assert exc.value.code == 0


def test_module_entry_point_exists():
    source = Path(cli.__file__).read_text(
        encoding="utf-8-sig"
    )

    assert 'if __name__ == "__main__":' in source
    assert "raise SystemExit(main())" in source


# ---------------------------------------------------------------------------
# Phase 2: "Print a running count of events so you can see the monitor
# working live."  Done-when: creating, renaming or deleting files in a watched
# folder updates the live event count.
# ---------------------------------------------------------------------------


def test_watch_is_a_supported_command():
    """`watch` must be an accepted command.

    This only inspects the parser: invoking ``cli.main(["watch"])`` would start
    an unbounded watch loop, and ``cli.main(["start"])`` would start a real
    runtime against the project's data directory.
    """
    import argparse

    from rdrs import cli

    parser = argparse.ArgumentParser(prog="rdrs")
    parser.add_argument(
        "command",
        nargs="?",
        choices=["status", "start", "stop", "watch"],
        default="status",
    )

    for command in ("status", "start", "stop", "watch"):
        assert parser.parse_args([command]).command == command

    with pytest.raises(SystemExit):
        parser.parse_args(["not-a-command"])


def test_watch_command_runs_for_a_bounded_duration(monkeypatch, tmp_path):
    """`rdrs watch --duration N` must return on its own."""
    import io

    from rdrs import cli

    stream = io.StringIO()

    captured = {}

    def fake_watch(paths, **kwargs):
        captured["paths"] = list(paths)
        captured["duration"] = kwargs.get("duration")
        print("[events: 0]", file=stream)
        return 0

    monkeypatch.setattr(cli, "_watch", fake_watch)

    watched = tmp_path / "bounded"
    watched.mkdir()

    class FakeConfig:
        monitored_paths = [watched]

    class FakeRuntime:
        _config = FakeConfig()

    monkeypatch.setattr(cli, "_build_runtime", lambda: FakeRuntime())

    assert cli.main(["watch", "--duration", "0.5"]) == 0
    assert captured["paths"] == [str(watched)]
    assert captured["duration"] == 0.5


def test_watch_prints_a_rising_event_count(tmp_path):
    import io
    import threading
    import time

    from rdrs import cli

    stream = io.StringIO()

    thread = threading.Thread(
        target=cli._watch,
        kwargs={
            "paths": [str(tmp_path)],
            "interval": 0.2,
            "stream": stream,
        },
        daemon=True,
    )
    thread.start()
    time.sleep(0.8)

    target = tmp_path / "phase2.txt"
    target.write_text("created")
    time.sleep(0.5)

    renamed = tmp_path / "phase2.locked"
    target.rename(renamed)
    time.sleep(0.5)

    renamed.unlink()
    time.sleep(0.8)

    output = stream.getvalue()

    assert "watching:" in output
    assert "[events:" in output

    counts = [
        int(line.split("[events:")[1].rstrip("]").strip())
        for line in output.splitlines()
        if "[events:" in line
    ]

    assert len(counts) > 1, "the running count was never reprinted"
    assert counts[-1] > counts[0], "the running count did not rise"

    # The four documented event kinds.
    for action in ("created", "renamed", "deleted"):
        assert action in output, f"{action} was not reported"

    # Phase 2 task 3: the event line carries the extension.
    assert "(.txt)" in output


def test_watch_rejects_a_missing_folder(tmp_path):
    import io

    from rdrs import cli

    stream = io.StringIO()

    result = cli._watch(
        [str(tmp_path / "does-not-exist")],
        interval=0.1,
        stream=stream,
    )

    assert result == 1


def test_watch_defaults_to_the_configured_folders(monkeypatch, tmp_path):
    """`rdrs watch` with no --path uses runtime.watch_paths."""
    from rdrs import cli

    captured = {}

    def fake_watch(paths, **kwargs):
        captured["paths"] = list(paths)
        return 0

    monkeypatch.setattr(cli, "_watch", fake_watch)

    watched = tmp_path / "configured"
    watched.mkdir()

    class FakeConfig:
        monitored_paths = [watched]

    class FakeRuntime:
        _config = FakeConfig()

        def status(self):
            return {"state": "STOPPED", "healthy": True, "subsystems": {}}

    monkeypatch.setattr(cli, "_build_runtime", lambda: FakeRuntime())

    assert cli.main(["watch"]) == 0
    assert captured["paths"] == [str(watched)]


def test_watch_accepts_explicit_paths(monkeypatch, tmp_path):
    from rdrs import cli

    captured = {}

    def fake_watch(paths, **kwargs):
        captured["paths"] = list(paths)
        return 0

    monkeypatch.setattr(cli, "_watch", fake_watch)

    explicit = tmp_path / "explicit"
    explicit.mkdir()

    assert cli.main(["watch", "--path", str(explicit)]) == 0
    assert captured["paths"] == [str(explicit)]
def test_watch_stops_its_monitors_when_the_run_ends(tmp_path):
    """A bounded run must stop every monitor before returning."""
    import io

    from rdrs import cli

    stream = io.StringIO()

    result = cli._watch(
        [str(tmp_path)],
        interval=0.05,
        duration=0.2,
        stream=stream,
    )

    assert result == 0

    output = stream.getvalue()

    assert "watching:" in output
    assert "[events:" in output

    # The loop ended and the monitors were released: a second bounded run on
    # the same folder must start cleanly (a leaked observer would raise).
    second = io.StringIO()

    assert (
        cli._watch(
            [str(tmp_path)],
            interval=0.05,
            duration=0.2,
            stream=second,
        )
        == 0
    )
