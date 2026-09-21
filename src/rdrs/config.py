from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


DEFAULT_DETECTION_THRESHOLDS: dict[str, float] = {
    "window_seconds": 60.0,
    "modified_files_per_minute": 20.0,
    "renames": 10.0,
    "extension_changes": 5.0,
    "average_entropy": 7.0,
    "cpu_percent": 80.0,
}


DEFAULT_SCORING_WEIGHTS: dict[str, float] = {
    "rapid_encryption": 40.0,
    "mass_rename": 30.0,
    "high_entropy": 25.0,
    "cpu_spike": 15.0,
    "unknown_program": 10.0,
}


class ConfigConflictError(RuntimeError):
    """Raised when a save would overwrite a newer configuration on disk.

    A stale dashboard tab or a second process can hold an out-of-date view of
    ``config.yaml``. Writing it back would silently discard whatever the other
    writer changed, so :meth:`RuntimeConfig.save` refuses instead.
    """


def _yaml_scalar(value: Any) -> str:
    """Render a single value as a YAML scalar.

    Delegates to PyYAML so quoting, escaping and float formatting stay exactly
    as the loader expects.
    """
    return yaml.safe_dump(
        value,
        default_flow_style=True,
        allow_unicode=True,
        width=10_000,
    ).strip().rstrip("...").strip()


@dataclass
class RuntimeConfig:
    name: str = "rdrs"
    environment: str = "development"
    data_dir: Path = field(
        default_factory=lambda: Path("data")
    )
    logs_dir: Path = field(
        default_factory=lambda: Path("logs")
    )
    #: Folders the file monitor watches. The Build Guide's Phase 1 requires the
    #: configuration to state "which folders to watch". An empty list means
    #: "watch data_dir", so a deployment that does not set this still monitors
    #: its own sandbox rather than nothing. These are the *resolved* paths the
    #: monitor uses.
    watch_paths: list[Path] = field(
        default_factory=list
    )
    #: The ``watch_paths`` entries exactly as they appeared in the file. Kept so
    #: a save writes the operator's own form back (``data`` rather than
    #: ``D:\\project\\data``) instead of a machine-specific absolute path.
    watch_paths_raw: list[str] = field(
        default_factory=list
    )
    #: SHA-256 of the file this configuration was loaded from. ``save`` uses it
    #: to detect that another writer changed the file underneath us. ``None``
    #: for a configuration built in code rather than read from disk, in which
    #: case no conflict check is possible or needed.
    loaded_digest: str | None = None
    db_path: Path | None = None
    shutdown_timeout: float = 10.0
    log_level: str = "INFO"
    persistence_enabled: bool = True
    subsystems: list[dict[str, Any]] = field(
        default_factory=list
    )
    config_path: str | None = None

    detection_thresholds: dict[str, float] = field(
        default_factory=lambda: dict(
            DEFAULT_DETECTION_THRESHOLDS
        )
    )

    scoring_weights: dict[str, float] = field(
        default_factory=lambda: dict(
            DEFAULT_SCORING_WEIGHTS
        )
    )

    @property
    def monitored_paths(self) -> list[Path]:
        """Return the folders the file monitor must watch.

        Build Guide Phase 1: the config loader reads "which folders to watch"
        from ``config.yaml``. When none are configured, ``data_dir`` is used so
        the monitor always watches a real folder.
        """
        if self.watch_paths:
            return list(self.watch_paths)

        return [self.data_dir]

    @classmethod
    def load(
        cls,
        config_path: str | Path,
        data_dir: str | Path | None = None,
        logs_dir: str | Path | None = None,
        db_path: str | Path | None = None,
    ) -> "RuntimeConfig":
        path = Path(config_path)

        if not path.exists():
            raise FileNotFoundError(
                f"Configuration file not found: {path}"
            )

        with path.open(
            "r",
            encoding="utf-8",
        ) as handle:
            raw = yaml.safe_load(handle) or {}

        if not isinstance(raw, dict):
            raise ValueError(
                "Configuration root must be a mapping"
            )

        runtime_section = cls._mapping(
            raw.get("runtime"),
            "runtime",
        )

        logging_section = cls._mapping(
            raw.get("logging"),
            "logging",
        )

        persistence_section = cls._mapping(
            raw.get("persistence"),
            "persistence",
        )

        detection_section = cls._mapping(
            raw.get("detection"),
            "detection",
        )

        scoring_section = cls._mapping(
            raw.get("scoring"),
            "scoring",
        )

        subsystems = raw.get(
            "subsystems",
            [],
        )

        if not isinstance(subsystems, list):
            raise ValueError(
                "subsystems must be a list"
            )

        data_root = (
            Path(data_dir)
            if data_dir is not None
            else Path(
                runtime_section.get(
                    "data_dir",
                    "data",
                )
            )
        )

        logs_root = (
            Path(logs_dir)
            if logs_dir is not None
            else Path(
                runtime_section.get(
                    "logs_dir",
                    "logs",
                )
            )
        )

        configured_db_path = persistence_section.get(
            "db_path"
        )

        # "which folders to watch" (Build Guide Phase 1). Relative entries are
        # resolved against the configuration file so their meaning does not
        # change with the process working directory.
        configured_watch_paths = runtime_section.get(
            "watch_paths",
            [],
        )

        if configured_watch_paths is None:
            configured_watch_paths = []

        if isinstance(
            configured_watch_paths,
            (str, bytes),
        ):
            raise ValueError(
                "runtime.watch_paths must be a list of paths"
            )

        if not isinstance(configured_watch_paths, list):
            raise ValueError(
                "runtime.watch_paths must be a list of paths"
            )

        watch_paths: list[Path] = []
        watch_paths_raw: list[str] = []

        # Resolve against the configuration file's absolute directory so a
        # relative entry means the same folder no matter what the process
        # working directory is.
        config_root = path.resolve().parent

        for entry in configured_watch_paths:
            text = str(entry).strip()

            if not text:
                continue

            watch_paths_raw.append(text)

            candidate = Path(text)

            if not candidate.is_absolute():
                candidate = config_root / candidate

            watch_paths.append(candidate)

        database_path = (
            Path(db_path)
            if db_path is not None
            else (
                Path(configured_db_path)
                if configured_db_path is not None
                else data_root / "rdrs.db"
            )
        )

        detection_thresholds = (
            dict(DEFAULT_DETECTION_THRESHOLDS)
        )

        cls._merge_numeric_settings(
            detection_thresholds,
            detection_section,
            "detection",
        )

        scoring_weights = (
            dict(DEFAULT_SCORING_WEIGHTS)
        )

        cls._merge_numeric_settings(
            scoring_weights,
            scoring_section.get(
                "weights",
                {},
            ),
            "scoring.weights",
        )

        config = cls(
            name=str(
                runtime_section.get(
                    "name",
                    "rdrs",
                )
            ),
            environment=str(
                runtime_section.get(
                    "environment",
                    "development",
                )
            ),
            data_dir=data_root,
            logs_dir=logs_root,
            watch_paths=watch_paths,
            watch_paths_raw=watch_paths_raw,
            db_path=database_path,
            shutdown_timeout=float(
                runtime_section.get(
                    "shutdown_timeout",
                    10.0,
                )
            ),
            log_level=str(
                logging_section.get(
                    "level",
                    "INFO",
                )
            ).upper(),
            persistence_enabled=bool(
                persistence_section.get(
                    "enabled",
                    True,
                )
            ),
            subsystems=list(subsystems),
            config_path=str(path),
            detection_thresholds=detection_thresholds,
            scoring_weights=scoring_weights,
            loaded_digest=hashlib.sha256(
                path.read_bytes()
            ).hexdigest(),
        )

        config.validate()
        return config

    @staticmethod
    def _mapping(
        value: Any,
        name: str,
    ) -> dict[str, Any]:
        if value is None:
            return {}

        if not isinstance(value, dict):
            raise ValueError(
                f"{name} must be a mapping"
            )

        return value

    @staticmethod
    def _merge_numeric_settings(
        target: dict[str, float],
        source: Any,
        name: str,
    ) -> None:
        if source is None:
            return

        if not isinstance(source, dict):
            raise ValueError(
                f"{name} must be a mapping"
            )

        for key, value in source.items():
            if key not in target:
                raise ValueError(
                    f"Unknown {name} setting: {key}"
                )

            if isinstance(value, bool):
                raise ValueError(
                    f"{name}.{key} must be numeric"
                )

            try:
                target[key] = float(value)
            except (
                TypeError,
                ValueError,
            ) as exc:
                raise ValueError(
                    f"{name}.{key} must be numeric"
                ) from exc

    def validate(self) -> None:
        if not self.name.strip():
            raise ValueError(
                "Runtime name cannot be empty"
            )

        if self.shutdown_timeout <= 0:
            raise ValueError(
                "shutdown_timeout must be positive"
            )

        if self.log_level not in {
            "TRACE",
            "DEBUG",
            "INFO",
            "SUCCESS",
            "WARNING",
            "ERROR",
            "CRITICAL",
        }:
            raise ValueError(
                f"Unsupported log level: {self.log_level}"
            )

        if (
            self.persistence_enabled
            and self.db_path is None
        ):
            raise ValueError(
                "Database path is required when "
                "persistence is enabled"
            )

        self._validate_detection()
        self._validate_scoring()

        self.data_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.logs_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        if self.db_path is not None:
            self.db_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

    def _validate_detection(self) -> None:
        required = DEFAULT_DETECTION_THRESHOLDS

        if set(self.detection_thresholds) != set(
            required
        ):
            raise ValueError(
                "Detection thresholds must contain "
                "the complete configured signal set"
            )

        if (
            self.detection_thresholds[
                "window_seconds"
            ]
            <= 0
        ):
            raise ValueError(
                "detection.window_seconds must be positive"
            )

        for key in (
            "modified_files_per_minute",
            "renames",
            "extension_changes",
        ):
            if self.detection_thresholds[key] < 0:
                raise ValueError(
                    f"detection.{key} cannot be negative"
                )

        entropy = self.detection_thresholds[
            "average_entropy"
        ]

        if not 0 <= entropy <= 8:
            raise ValueError(
                "detection.average_entropy must be "
                "between 0 and 8"
            )

        cpu = self.detection_thresholds[
            "cpu_percent"
        ]

        if not 0 <= cpu <= 100:
            raise ValueError(
                "detection.cpu_percent must be "
                "between 0 and 100"
            )

    def _validate_scoring(self) -> None:
        required = DEFAULT_SCORING_WEIGHTS

        if set(self.scoring_weights) != set(
            required
        ):
            raise ValueError(
                "Scoring weights must contain the "
                "complete configured signal set"
            )

        for key, weight in self.scoring_weights.items():
            if weight < 0:
                raise ValueError(
                    f"scoring.weights.{key} "
                    "cannot be negative"
                )

        if sum(self.scoring_weights.values()) <= 0:
            raise ValueError(
                "At least one scoring weight must "
                "be greater than zero"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "environment": self.environment,
            "data_dir": str(self.data_dir),
            "logs_dir": str(self.logs_dir),
            "watch_paths": [
                str(path) for path in self.watch_paths
            ],
            "monitored_paths": [
                str(path) for path in self.monitored_paths
            ],
            "db_path": (
                str(self.db_path)
                if self.db_path is not None
                else None
            ),
            "shutdown_timeout": self.shutdown_timeout,
            "log_level": self.log_level,
            "persistence_enabled": (
                self.persistence_enabled
            ),
            "subsystems": self.subsystems,
            "detection_thresholds": dict(
                self.detection_thresholds
            ),
            "scoring_weights": dict(
                self.scoring_weights
            ),
        }

    def to_config_dict(self) -> dict[str, Any]:
        """Render the runtime settings as loadable YAML config.

        Uses the same ``runtime``/``logging``/``persistence``/``detection``/
        ``scoring`` shape that :meth:`load` consumes, so a saved file can be
        loaded back verbatim.

        ``watch_paths`` is included. Omitting it meant every save silently
        dropped the folders the operator asked to protect, and a restart then
        monitored only ``data_dir`` -- a silent protection loss. This must stay
        in step with :meth:`to_dict`, which has always reported it.
        """
        return {
            "runtime": {
                "name": self.name,
                "environment": self.environment,
                "data_dir": str(self.data_dir),
                "logs_dir": str(self.logs_dir),
                "shutdown_timeout": self.shutdown_timeout,
                "watch_paths": [
                    str(path) for path in self.watch_paths
                ],
            },
            "logging": {"level": self.log_level},
            "persistence": {
                "enabled": self.persistence_enabled,
                "db_path": (
                    str(self.db_path)
                    if self.db_path is not None
                    else None
                ),
            },
            "detection": dict(
                self.detection_thresholds
            ),
            "scoring": {
                "weights": dict(
                    self.scoring_weights
                )
            },
            "subsystems": self.subsystems,
        }

    def render_yaml(self) -> str:
        """Render the complete configuration as commented YAML text.

        PyYAML cannot round-trip comments, and ``ruamel.yaml`` is not a
        dependency of this project. Rather than let every save strip the
        documentation out of ``config.yaml``, the file is rendered from this
        canonical commented template: the comments are regenerated on each
        write, so they are never lost.

        Relative ``watch_paths`` entries are written back as they were
        configured. Entries that were resolved to absolute paths by
        :meth:`load` are written absolutely, which preserves their meaning.
        """
        import yaml

        lines: list[str] = [
            "# RDRS configuration.",
            "#",
            "# This is the single canonical configuration file, at the path the FSD",
            '# specifies ("Load configuration from config.yaml" - FSD FR-1) and the Build',
            "# Guide's project structure shows.",
            "#",
            "# It is loaded and validated at start-up, exposed through GET /status, edited",
            "# through POST /settings, and written back here so changes survive a restart.",
            "",
            "runtime:",
            f"  name: {_yaml_scalar(self.name)}",
            f"  environment: {_yaml_scalar(self.environment)}",
            f"  data_dir: {_yaml_scalar(str(self.data_dir))}",
            f"  logs_dir: {_yaml_scalar(str(self.logs_dir))}",
            f"  shutdown_timeout: {_yaml_scalar(self.shutdown_timeout)}",
            "",
            "  # Folders the file monitor watches (Build Guide Phase 1: the config loader",
            '  # reads "which folders to watch" from config.yaml).',
            "  #",
            "  # Add the folders you want protected here. Relative paths resolve against",
            '  # this file, so "data" means <project>/data regardless of the working',
            '  # directory. An empty list means "watch data_dir".',
            "  #",
            "  # Keep every folder RDRS writes to excluded from monitoring (see the",
            "  # monitor excludes in runtime.py) so the detector never observes its own",
            "  # output.",
        ]

        if self.watch_paths:
            # Prefer the operator's own form when it still lines up with the
            # resolved paths, so a save does not rewrite "data" as an absolute
            # machine-specific path.
            entries = (
                list(self.watch_paths_raw)
                if len(self.watch_paths_raw) == len(self.watch_paths)
                else [str(path) for path in self.watch_paths]
            )

            lines.append("  watch_paths:")

            for entry in entries:
                lines.append(f"    - {_yaml_scalar(str(entry))}")
        else:
            lines.append("  watch_paths: []")

        lines += [
            "",
            "logging:",
            f"  level: {_yaml_scalar(self.log_level)}",
            "",
            "persistence:",
            f"  enabled: {_yaml_scalar(bool(self.persistence_enabled))}",
            f"  db_path: {_yaml_scalar(str(self.db_path) if self.db_path is not None else None)}",
            "",
            "# Detection thresholds. Compared against the sliding-window counters that the",
            "# detection engine recomputes over the configured window.",
            "detection:",
        ]

        for key, value in self.detection_thresholds.items():
            lines.append(f"  {key}: {_yaml_scalar(value)}")

        lines += [
            "",
            "# Weighted threat score (0-100). Rapid encryption dominates because it is the",
            "# strongest single indicator of ransomware-like behaviour.",
            "scoring:",
            "  weights:",
        ]

        for key, value in self.scoring_weights.items():
            lines.append(f"    {key}: {_yaml_scalar(value)}")

        lines += [
            "",
            "# Subsystems registered and health-tracked at start-up.",
        ]

        if not self.subsystems:
            # A bare "subsystems:" parses as None, which the loader rejects
            # ("subsystems must be a list"). Emit an explicit empty sequence.
            lines.append("subsystems: []")
        else:
            lines.append("subsystems:")

            for subsystem in self.subsystems:
                entries = list(subsystem.items())

                for index, (key, value) in enumerate(entries):
                    prefix = "- " if index == 0 else "  "
                    lines.append(f"{prefix}{key}: {_yaml_scalar(value)}")

        text = "\n".join(lines) + "\n"

        # The rendered document must be loadable; a template bug must never
        # write a file the loader cannot read back.
        reloaded = yaml.safe_load(text)

        if not isinstance(reloaded, dict) or "runtime" not in reloaded:
            raise ValueError(
                "rendered configuration is not valid YAML"
            )

        return text

    def save(
        self,
        config_path: str | Path,
        *,
        force: bool = False,
        backup: bool = True,
    ) -> Path:
        """Persist the current settings back to the config file.

        Without this, settings applied through ``POST /settings`` live only in
        memory and are silently lost on restart.

        Three guards protect the operator's configuration:

        * **No-op guard** -- when the rendered document already matches the file
          byte for byte, nothing is written. An empty or unchanged settings
          payload can therefore never rewrite the file.
        * **Conflict detection** -- :meth:`load` records the digest of the file
          it read. If the file on disk no longer matches, a stale writer (an old
          browser tab, a second process) would silently discard whatever the
          other writer changed, so :class:`ConfigConflictError` is raised.
          ``force=True`` overrides this for a deliberate overwrite.
        * **Pre-write backup** -- the previous file is copied aside before it is
          replaced, so a bad write is always recoverable.
        """
        import hashlib
        import shutil
        import time

        path = Path(config_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        rendered = self.render_yaml().encode("utf-8")

        existing = path.read_bytes() if path.is_file() else None

        # 1. No-op guard.
        if existing == rendered:
            return path

        # 2. Conflict detection against the digest this instance loaded.
        if (
            existing is not None
            and not force
            and self.loaded_digest is not None
        ):
            current_digest = hashlib.sha256(existing).hexdigest()

            if current_digest != self.loaded_digest:
                raise ConfigConflictError(
                    "the configuration file changed on disk since it was "
                    "loaded; refusing to overwrite a newer version"
                )

        # 3. Pre-write backup.
        if existing is not None and backup:
            stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())

            try:
                shutil.copy2(path, path.with_name(f"{path.name}.bak-{stamp}"))
            except OSError:
                # A failed backup must not block the write; the caller still
                # gets the persisted settings.
                pass

        path.write_bytes(rendered)

        self.loaded_digest = hashlib.sha256(rendered).hexdigest()

        return path
