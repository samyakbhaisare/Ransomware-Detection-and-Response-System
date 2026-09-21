from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .runtime import RuntimeManager, RuntimeStatus


def _default_config_path() -> str:
    """Resolve the configuration file for the CLI.

    Delegates to :meth:`RuntimeManager.default_config_path` so the CLI, the API
    and the dashboard all load the same FSD-named ``config.yaml``. The
    ``RDRS_CONFIG_PATH`` environment variable still overrides it.
    """
    return os.environ.get(
        "RDRS_CONFIG_PATH",
        str(RuntimeManager.default_config_path()),
    )


def _build_runtime() -> RuntimeManager:
    """Build the runtime from the resolved configuration.

    Only ``config_path`` is supplied. Passing explicit ``data_dir`` /
    ``logs_dir`` / ``db_path`` pinned them to the project root, so
    ``RDRS_CONFIG_PATH`` changed which configuration was *read* but not which
    database was *used* -- the CLI silently operated on the project's own
    database regardless. The configuration now decides, which is the same
    behaviour the API and dashboard have.

    For the default configuration this resolves to exactly the same paths as
    before: ``config.yaml`` lives at the project root and lists ``data`` /
    ``logs`` relative to itself.
    """
    return RuntimeManager(config_path=_default_config_path())


def _print_status(status: dict) -> None:
    print(f"state={status['state']}")
    print(f"healthy={status['healthy']}")
    for name, details in status["subsystems"].items():
        print(f"subsystem={name} healthy={details['healthy']} critical={details['critical']}")


def _watch(
    paths: list[str],
    *,
    interval: float = 1.0,
    duration: float | None = None,
    stream: Any = None,
) -> int:
    """Watch folders and print a running event count live.

    Build Guide Phase 2: "Print a running count of events so you can see the
    monitor working live." Each change is echoed with its type, path, time and
    extension, and the running total is reprinted every ``interval`` seconds.

    Runs until Ctrl+C, or for ``duration`` seconds when that is given. The
    monitors are always stopped before returning.
    """
    import time

    from .monitoring import RealtimeFileMonitor

    out = stream if stream is not None else sys.stdout

    resolved = [Path(item) for item in paths]

    missing = [path for path in resolved if not path.exists()]

    if missing:
        for path in missing:
            print(f"ERROR: watch path does not exist: {path}", file=sys.stderr)

        return 1

    counters = {"total": 0}

    def handle(event: Any) -> None:
        counters["total"] += 1

        extension = getattr(event, "extension", "") or ""

        print(
            "%s  %-9s %s%s"
            % (
                time.strftime(
                    "%H:%M:%S",
                    time.localtime(getattr(event, "timestamp", time.time())),
                ),
                getattr(event, "action", "?"),
                getattr(event, "path", ""),
                f"  ({extension})" if extension else "",
            ),
            file=out,
            flush=True,
        )

    monitors: list[Any] = []

    for path in resolved:
        monitor = RealtimeFileMonitor(path, callback=handle)
        monitor.start()
        monitors.append(monitor)

    print(
        "watching: %s" % ", ".join(str(path) for path in resolved),
        file=out,
        flush=True,
    )
    print("press Ctrl+C to stop", file=out, flush=True)

    started = time.monotonic()

    try:
        while True:
            time.sleep(interval)

            print(
                f"[events: {counters['total']}]",
                file=out,
                flush=True,
            )

            if duration is not None and (
                time.monotonic() - started >= duration
            ):
                break
    except KeyboardInterrupt:
        print(file=out)
        print(
            f"stopped. total events: {counters['total']}",
            file=out,
        )
    finally:
        for monitor in monitors:
            try:
                monitor.stop()
            except Exception:
                pass

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rdrs")
    parser.add_argument(
        "command",
        nargs="?",
        choices=["status", "start", "stop", "watch"],
        default="status",
    )
    parser.add_argument(
        "--path",
        "--watch",
        dest="paths",
        action="append",
        default=None,
        metavar="PATH",
        help=(
            "folder to watch (repeatable). Defaults to the folders listed "
            "under runtime.watch_paths in config.yaml."
        ),
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="seconds between running-count updates (default: 1)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help=(
            "stop watching after this many seconds (default: run until "
            "Ctrl+C)"
        ),
    )
    args = parser.parse_args(argv)

    try:
        runtime = _build_runtime()
        if args.command == "status":
            _print_status(runtime.status())
            return 0
        if args.command == "start":
            status = runtime.start()
            _print_status(status)
            return 0
        if args.command == "stop":
            # Stopping an already-stopped runtime is a no-op, not a failure.
            # It used to exit 1, which made `stop` unusable as a "make sure it
            # is stopped" step in a script. `getattr` keeps a genuine stop
            # failure -- which must still surface -- flowing to the handler
            # below rather than being silently treated as "already stopped".
            if getattr(runtime, "state", None) == RuntimeStatus.STOPPED:
                _print_status(runtime.status())
                return 0

            status = runtime.stop()
            _print_status(status)
            return 0
        if args.command == "watch":
            if args.paths:
                watch_paths = [str(item) for item in args.paths]
            else:
                watch_paths = [
                    str(path)
                    for path in runtime._config.monitored_paths
                ]

            return _watch(
                watch_paths,
                interval=args.interval,
                duration=args.duration,
            )
        parser.error(f"Unsupported command: {args.command}")
        return 2
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

