from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .runtime import RuntimeManager


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "rdrs.yaml"


def _build_runtime() -> RuntimeManager:
    root = Path(__file__).resolve().parents[2]
    config_path = os.environ.get("RDRS_CONFIG_PATH", str(DEFAULT_CONFIG_PATH))
    return RuntimeManager(
        config_path=config_path,
        data_dir=root / "data",
        logs_dir=root / "logs",
        db_path=root / "data" / "rdrs.db",
    )


def _print_status(status: dict) -> None:
    print(f"state={status['state']}")
    print(f"healthy={status['healthy']}")
    for name, details in status["subsystems"].items():
        print(f"subsystem={name} healthy={details['healthy']} critical={details['critical']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rdrs")
    parser.add_argument("command", nargs="?", choices=["status", "start", "stop"], default="status")
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
            status = runtime.stop()
            _print_status(status)
            return 0
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
