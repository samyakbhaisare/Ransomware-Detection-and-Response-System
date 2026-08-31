from __future__ import annotations

import signal
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Any

from .config import RuntimeConfig
from .logging import initialize_logging
from .storage import DatabaseManager


class RuntimeStatus(str, Enum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    SHUTTING_DOWN = "SHUTTING_DOWN"
    FAILED = "FAILED"


class RuntimeManager:
    _instance: "RuntimeManager | None" = None
    _lock = threading.RLock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
            return cls._instance

    def __init__(
        self,
        config_path: str | Path | None = None,
        data_dir: str | Path | None = None,
        logs_dir: str | Path | None = None,
        db_path: str | Path | None = None,
    ):
        if getattr(self, "_initialized", False):
            if config_path is not None or data_dir is not None or logs_dir is not None or db_path is not None:
                self._apply_runtime_settings(config_path=config_path, data_dir=data_dir, logs_dir=logs_dir, db_path=db_path)
            return

        self._initialized = True
        self.state = RuntimeStatus.STOPPED
        self._config: RuntimeConfig | None = None
        self._database: DatabaseManager | None = None
        self._subsystems: dict[str, dict[str, Any]] = {}
        self.logger = initialize_logging("rdrs")
        self.started_at: float | None = None
        self._shutdown_requested = False
        self._signal_handlers_installed = False
        self._pending_config_path = Path(config_path) if config_path is not None else Path(__file__).resolve().parents[2] / "config" / "rdrs.yaml"
        self._pending_data_dir = Path(data_dir) if data_dir is not None else None
        self._pending_logs_dir = Path(logs_dir) if logs_dir is not None else None
        self._pending_db_path = Path(db_path) if db_path is not None else None

        if config_path is not None or data_dir is not None or logs_dir is not None or db_path is not None:
            self._apply_runtime_settings(config_path=config_path, data_dir=data_dir, logs_dir=logs_dir, db_path=db_path)

    @classmethod
    def reset_singleton(cls) -> None:
        with cls._lock:
            if cls._instance is not None:
                try:
                    cls._instance.close()
                except Exception:
                    pass
            cls._instance = None

    def _apply_runtime_settings(
        self,
        *,
        config_path: str | Path | None = None,
        data_dir: str | Path | None = None,
        logs_dir: str | Path | None = None,
        db_path: str | Path | None = None,
    ) -> None:
        if config_path is None:
            config_path = self._pending_config_path if getattr(self, "_pending_config_path", None) is not None else Path(__file__).resolve().parents[2] / "config" / "rdrs.yaml"
        if data_dir is None:
            data_dir = self._pending_data_dir
        if logs_dir is None:
            logs_dir = self._pending_logs_dir
        if db_path is None:
            db_path = self._pending_db_path

        try:
            self._config = RuntimeConfig.load(config_path=config_path, data_dir=data_dir, logs_dir=logs_dir, db_path=db_path)
        except FileNotFoundError:
            self._config = None
            return

        self.logger = initialize_logging("rdrs", level=self._config.log_level)
        self._database = DatabaseManager(self._config.db_path)
        self._load_persisted_state()

    def _load_persisted_state(self) -> None:
        if self._database is None:
            return
        try:
            self._database.initialize()
            row = self._database.connection.execute("SELECT value FROM runtime_state WHERE key = 'state' ORDER BY updated_at DESC LIMIT 1").fetchone()
            if row is not None:
                value = row[0]
                if value in RuntimeStatus.__members__:
                    self.state = RuntimeStatus[value]
                else:
                    self.state = RuntimeStatus(value)
        except Exception:
            self.state = RuntimeStatus.STOPPED

    def _install_signal_handlers(self) -> None:
        if self._signal_handlers_installed:
            return
        self._signal_handlers_installed = True
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(signum, self.handle_signal)
            except (AttributeError, ValueError):
                continue

    def _register_subsystem(self, name: str, *, healthy: bool = True, critical: bool = True, details: dict[str, Any] | None = None) -> None:
        self._subsystems[name] = {
            "name": name,
            "healthy": bool(healthy),
            "critical": bool(critical),
            "details": details or {},
        }
        if self._database is not None:
            try:
                self._database.record_subsystem(name, healthy, details or {})
            except RuntimeError:
                pass

        if self.state in {RuntimeStatus.RUNNING, RuntimeStatus.DEGRADED}:
            if healthy:
                if all(info["healthy"] for info in self._subsystems.values()):
                    self.state = RuntimeStatus.RUNNING
            else:
                self.state = RuntimeStatus.DEGRADED

    def register_subsystem(self, name: str, *, healthy: bool = True, critical: bool = True, details: dict[str, Any] | None = None) -> None:
        self._register_subsystem(name, healthy=healthy, critical=critical, details=details)

    def _init_persistence(self) -> None:
        if self._config is None:
            raise RuntimeError("Runtime configuration not initialized")
        if not self._config.persistence_enabled:
            return
        if self._database is None:
            self._database = DatabaseManager(self._config.db_path)
        self._database.initialize()
        self._database.record_runtime_state(self.state.value)

    def _init_default_subsystems(self) -> None:
        self._subsystems = {}
        self._register_subsystem("config", healthy=True, critical=True, details={"config_path": str(self._config.config_path) if hasattr(self._config, 'config_path') else str(self._config)})
        self._register_subsystem("logging", healthy=True, critical=True, details={"level": self._config.log_level})
        self._register_subsystem("database", healthy=True, critical=True, details={"db_path": str(self._config.db_path)})

    def start(self) -> dict[str, Any]:
        if self.state in {RuntimeStatus.STARTING, RuntimeStatus.RUNNING}:
            raise RuntimeError("Runtime is already started and running")
        if self.state == RuntimeStatus.SHUTTING_DOWN:
            raise RuntimeError("Runtime is shutting down; wait for completion before starting again")
        if self.state == RuntimeStatus.FAILED:
            raise RuntimeError("Runtime is in FAILED state; reset or reinitialize before starting")

        self.state = RuntimeStatus.STARTING
        self._shutdown_requested = False
        self.logger.info("runtime.start.begin", extra={"state": self.state.value})

        try:
            if self._config is None:
                self._apply_runtime_settings(
                    config_path=self._pending_config_path,
                    data_dir=self._pending_data_dir,
                    logs_dir=self._pending_logs_dir,
                    db_path=self._pending_db_path,
                )
            if self._config is None:
                raise RuntimeError("Configuration file not found or invalid")
            self._init_persistence()
            self._init_default_subsystems()
            self._install_signal_handlers()
            self.started_at = time.time()
            self.state = RuntimeStatus.RUNNING
            self.logger.info("runtime.start.success", extra={"state": self.state.value, "subsystems": list(self._subsystems.keys())})
            if self._database is not None:
                self._database.record_runtime_state(self.state.value)
            return self.status()
        except Exception as exc:
            self.state = RuntimeStatus.FAILED
            self.logger.exception("runtime.start.failed", extra={"details": str(exc)})
            if self._database is not None:
                try:
                    self._database.record_runtime_state(self.state.value)
                except Exception:
                    pass
            raise RuntimeError(f"Failed to initialize runtime: {exc}") from exc

    def stop(self) -> dict[str, Any]:
        if self.state in {RuntimeStatus.STOPPED, RuntimeStatus.FAILED}:
            raise RuntimeError("Runtime is not running and cannot be stopped")
        if self.state == RuntimeStatus.SHUTTING_DOWN:
            raise RuntimeError("Runtime is already shutting down")

        self.state = RuntimeStatus.SHUTTING_DOWN
        self.logger.warning("runtime.stop.begin", extra={"state": self.state.value})
        try:
            for name, subsystem in list(self._subsystems.items()):
                subsystem["healthy"] = False
                self.logger.info("runtime.subsystem.stop", extra={"subsystem": name})
            if self._database is not None:
                self._database.record_runtime_state(self.state.value)
            self.state = RuntimeStatus.STOPPED
            if self._database is not None:
                self._database.record_runtime_state(self.state.value)
            self._cleanup()
            self.logger.info("runtime.stop.success", extra={"state": self.state.value})
            return self.status()
        except Exception as exc:
            self.state = RuntimeStatus.FAILED
            self.logger.exception("runtime.stop.failed", extra={"details": str(exc)})
            raise RuntimeError(f"Failed to stop runtime: {exc}") from exc

    def force_stop(self) -> None:
        self._cleanup()
        self.state = RuntimeStatus.STOPPED

    def _cleanup(self) -> None:
        if self._database is not None:
            self._database.close()
            self._database = None
        self.started_at = None
        self._subsystems = {}
        self._shutdown_requested = True

    def close(self) -> None:
        try:
            if self.state in {RuntimeStatus.STARTING, RuntimeStatus.RUNNING, RuntimeStatus.DEGRADED, RuntimeStatus.SHUTTING_DOWN}:
                self.stop()
            elif self.state == RuntimeStatus.FAILED:
                self.force_stop()
            else:
                self.force_stop()
        except RuntimeError:
            self.force_stop()

    def handle_signal(self, signum: int, frame: Any = None) -> None:
        signal_name = {signal.SIGINT: "SIGINT", signal.SIGTERM: "SIGTERM"}.get(signum, str(signum))
        self.logger.warning("runtime.signal.received", extra={"signal": signal_name, "state": self.state.value})
        if self.state in {RuntimeStatus.RUNNING, RuntimeStatus.DEGRADED, RuntimeStatus.STARTING}:
            try:
                self.stop()
            except RuntimeError:
                self.state = RuntimeStatus.SHUTTING_DOWN

    def status(self) -> dict[str, Any]:
        if self._database is not None:
            try:
                self._load_persisted_state()
            except Exception:
                pass
        result = {
            "state": self.state.value,
            "healthy": self.state in {RuntimeStatus.RUNNING},
            "started_at": self.started_at,
            "subsystems": {name: {"healthy": info["healthy"], "critical": info["critical"], "details": info["details"]} for name, info in self._subsystems.items()},
            "config": self._config.to_dict() if self._config is not None else {},
        }
        if self.state == RuntimeStatus.DEGRADED:
            result["healthy"] = False
        if self.state in {RuntimeStatus.STOPPED, RuntimeStatus.FAILED}:
            result["healthy"] = False
        return result

    def health_status(self) -> dict[str, Any]:
        subsystem_status = {name: info["healthy"] for name, info in self._subsystems.items()}
        healthy = all(subsystem_status.values()) if subsystem_status else self.state == RuntimeStatus.RUNNING
        return {
            "state": self.state.value,
            "healthy": healthy,
            "subsystems": subsystem_status,
            "degraded": self.state == RuntimeStatus.DEGRADED,
        }

    def shutdown(self) -> dict[str, Any]:
        return self.stop()

    def __repr__(self) -> str:
        return f"RuntimeManager(state={self.state.value}, started_at={self.started_at})"
