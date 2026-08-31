from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("state", "signal", "subsystems", "details", "feature"):
            if record.__dict__.get(key) is not None:
                payload[key] = record.__dict__[key]
        for key, value in record.__dict__.items():
            if key not in {"name", "msg", "args", "levelname", "levelno", "pathname", "filename", "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName", "created", "msecs", "relativeCreated", "thread", "threadName", "processName", "process", "message", "asctime"}:
                payload.setdefault(key, value)
        return json.dumps(payload, separators=(",", ":"))


class StructuredLogger(logging.Logger):
    def __init__(self, name: str = "rdrs", *, log_path: str | Path | None = None, level: str = "INFO"):
        super().__init__(name, level=getattr(logging, level.upper(), logging.INFO))
        self.propagate = False
        self.handlers.clear()
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(JsonFormatter())
        self.addHandler(stream_handler)
        if log_path is not None:
            file_handler = logging.FileHandler(log_path, encoding="utf-8")
            file_handler.setFormatter(JsonFormatter())
            self.addHandler(file_handler)


def initialize_logging(name: str = "rdrs", level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False

    if logger.handlers:
        return logger

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    return logger


def make_structured_logger(name: str = "rdrs", *, log_path: str | Path | None = None, level: str = "INFO") -> StructuredLogger:
    return StructuredLogger(name, log_path=log_path, level=level)
