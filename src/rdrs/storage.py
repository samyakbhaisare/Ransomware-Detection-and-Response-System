from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


class DatabaseManager:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.connection: sqlite3.Connection | None = None

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path)
        self.connection.execute("CREATE TABLE IF NOT EXISTS runtime_state (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)")
        self.connection.execute("CREATE TABLE IF NOT EXISTS subsystem_health (name TEXT PRIMARY KEY, healthy INTEGER NOT NULL, details TEXT, updated_at TEXT NOT NULL)")
        self.connection.commit()

    def record_runtime_state(self, state: str) -> None:
        if self.connection is None:
            raise RuntimeError("Database not initialized")
        self.connection.execute(
            "INSERT INTO runtime_state(key, value, updated_at) VALUES(?, ?, datetime('now')) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now')",
            ("state", state),
        )
        self.connection.commit()

    def record_subsystem(self, name: str, healthy: bool, details: dict[str, Any] | None = None) -> None:
        if self.connection is None:
            raise RuntimeError("Database not initialized")
        self.connection.execute(
            "INSERT INTO subsystem_health(name, healthy, details, updated_at) VALUES(?, ?, ?, datetime('now')) ON CONFLICT(name) DO UPDATE SET healthy=excluded.healthy, details=excluded.details, updated_at=datetime('now')",
            (name, int(healthy), repr(details or {})),
        )
        self.connection.commit()

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def __enter__(self) -> "DatabaseManager":
        if self.connection is None:
            self.initialize()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
