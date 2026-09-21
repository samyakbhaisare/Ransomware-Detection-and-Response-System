from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


LOG_FILES = {
    "system": "system.log",
    "events": "events.log",
    "alerts": "alerts.log",
    "errors": "errors.log",
    "audit": "audit.log",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created,
                tz=timezone.utc,
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        standard_fields = {
            "name", "msg", "args", "levelname", "levelno",
            "pathname", "filename", "module", "exc_info",
            "exc_text", "stack_info", "lineno", "funcName",
            "created", "msecs", "relativeCreated", "thread",
            "threadName", "processName", "process", "message",
            "asctime",
        }

        for key, value in record.__dict__.items():
            if key not in standard_fields and not key.startswith("_"):
                payload.setdefault(key, value)

        return json.dumps(payload, separators=(",", ":"), default=str)


class StructuredLogger(logging.Logger):
    def __init__(
        self,
        name: str = "rdrs",
        *,
        log_path: str | Path | None = None,
        level: str = "INFO",
    ):
        super().__init__(
            name,
            level=getattr(logging, level.upper(), logging.INFO),
        )

        self.propagate = False
        self.handlers.clear()

        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(JsonFormatter())
        self.addHandler(stream_handler)

        if log_path is not None:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)

            file_handler = RotatingFileHandler(
                log_path,
                maxBytes=5 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
            file_handler.setFormatter(JsonFormatter())
            self.addHandler(file_handler)

    def close(self) -> None:
        """Flush and release every handler owned by this logger.

        Without this, file handlers stay open until garbage collection, which
        leaks file descriptors and surfaces as ``ResourceWarning``.
        """
        for handler in self.handlers[:]:
            self.removeHandler(handler)

            try:
                handler.flush()
            except (OSError, ValueError):
                pass

            try:
                handler.close()
            except (OSError, ValueError):
                pass


def initialize_logging(
    name: str = "rdrs",
    level: str = "INFO",
    logs_dir: str | Path = "logs",
) -> logging.Logger:
    """Initialize the existing application logger.

    Kept compatible with the runtime module.
    """
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False

    if logger.handlers:
        return logger

    logs_path = Path(logs_dir)
    logs_path.mkdir(parents=True, exist_ok=True)

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)

    return logger


def initialize_rotating_logs(
    logs_dir: str | Path = "logs",
    *,
    level: str = "INFO",
) -> dict[str, logging.Logger]:
    """Create the five rotating operational loggers."""
    logs_path = Path(logs_dir)
    logs_path.mkdir(parents=True, exist_ok=True)

    loggers: dict[str, logging.Logger] = {}

    for log_name, filename in LOG_FILES.items():
        logger_name = f"rdrs.{log_name}"
        logger = logging.getLogger(logger_name)

        # Important for repeated initialization during tests/runtime reloads.
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()

        logger.setLevel(getattr(logging, level.upper(), logging.INFO))
        logger.propagate = False

        file_handler = RotatingFileHandler(
            logs_path / filename,
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(JsonFormatter())
        logger.addHandler(file_handler)

        loggers[log_name] = logger

    return loggers


def close_rotating_logs(
    loggers: dict[str, logging.Logger] | None,
) -> None:
    """Flush and close the rotating handlers created for a runtime.

    Mirrors :func:`initialize_rotating_logs` so a stopped runtime releases its
    file descriptors instead of leaking them until interpreter shutdown.
    """
    for logger in (loggers or {}).values():
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)

            try:
                handler.flush()
            except (OSError, ValueError):
                pass

            try:
                handler.close()
            except (OSError, ValueError):
                pass


def make_structured_logger(
    name: str = "rdrs",
    *,
    log_path: str | Path | None = None,
    level: str = "INFO",
) -> StructuredLogger:
    return StructuredLogger(
        name,
        log_path=log_path,
        level=level,
    )
