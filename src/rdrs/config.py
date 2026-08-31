from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class RuntimeConfig:
    name: str = "rdrs"
    environment: str = "development"
    data_dir: Path = field(default_factory=lambda: Path("data"))
    logs_dir: Path = field(default_factory=lambda: Path("logs"))
    db_path: Path | None = None
    shutdown_timeout: float = 10.0
    log_level: str = "INFO"
    persistence_enabled: bool = True
    subsystems: list[dict[str, Any]] = field(default_factory=list)
    config_path: str | None = None

    @classmethod
    def load(cls, config_path: str | Path, data_dir: str | Path | None = None, logs_dir: str | Path | None = None, db_path: str | Path | None = None) -> "RuntimeConfig":
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"Configuration file not found: {path}")

        with path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}

        runtime_section = raw.get("runtime", {}) if isinstance(raw, dict) else {}
        logging_section = raw.get("logging", {}) if isinstance(raw, dict) else {}
        persistence_section = raw.get("persistence", {}) if isinstance(raw, dict) else {}
        subsystems = raw.get("subsystems", []) if isinstance(raw, dict) else []

        data_root = Path(data_dir) if data_dir is not None else Path(runtime_section.get("data_dir", "data"))
        logs_root = Path(logs_dir) if logs_dir is not None else Path(runtime_section.get("logs_dir", "logs"))
        database_path = Path(db_path) if db_path is not None else Path(persistence_section.get("db_path", data_root / "rdrs.db"))

        config = cls(
            name=str(runtime_section.get("name", "rdrs")),
            environment=str(runtime_section.get("environment", "development")),
            data_dir=data_root,
            logs_dir=logs_root,
            db_path=database_path,
            shutdown_timeout=float(runtime_section.get("shutdown_timeout", 10.0)),
            log_level=str(logging_section.get("level", "INFO")),
            persistence_enabled=bool(persistence_section.get("enabled", True)),
            subsystems=list(subsystems),
            config_path=str(path),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if not self.name:
            raise ValueError("Runtime name cannot be empty")
        if self.shutdown_timeout <= 0:
            raise ValueError("shutdown_timeout must be positive")
        if self.persistence_enabled and self.db_path is None:
            raise ValueError("Database path is required when persistence is enabled")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        if self.db_path is not None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "environment": self.environment,
            "data_dir": str(self.data_dir),
            "logs_dir": str(self.logs_dir),
            "db_path": str(self.db_path),
            "shutdown_timeout": self.shutdown_timeout,
            "log_level": self.log_level,
            "persistence_enabled": self.persistence_enabled,
            "subsystems": self.subsystems,
        }
