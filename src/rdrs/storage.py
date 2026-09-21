from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    inspect as sa_inspect,
    or_,
    text as sa_text,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
)


def utc_now() -> datetime:
    """Return the current timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Base class for all RDRS database models."""

    pass


class EventRecord(Base):
    """Persisted real-time file-system event."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )
    path: Mapped[str] = mapped_column(
        String(2048),
        default="",
    )
    action: Mapped[str] = mapped_column(
        String(64),
        default="",
    )
    previous_path: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )
    new_path: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )
    metadata_json: Mapped[str] = mapped_column(
        Text,
        default="{}",
    )
    incident_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
        default=utc_now,
    )


class ProcessRecord(Base):
    """Persisted process telemetry snapshot."""

    __tablename__ = "processes"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )
    pid: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )
    parent_pid: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    command_line: Mapped[str] = mapped_column(
        Text,
        default="",
    )
    cwd: Mapped[str] = mapped_column(
        Text,
        default="",
    )
    started_at: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    cpu_percent: Mapped[float] = mapped_column(
        Float,
        default=0.0,
    )
    memory_rss: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )
    memory_percent: Mapped[float] = mapped_column(
        Float,
        default=0.0,
    )
    disk_write_bytes: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )
    executable_path: Mapped[str] = mapped_column(
        Text,
        default="",
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
        default=utc_now,
    )


class ScoreRecord(Base):
    """Persisted threat score."""

    __tablename__ = "scores"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )
    score: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )
    level: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(
        Text,
        default="",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
        default=utc_now,
    )


class AlertRecord(Base):
    """Persisted security alert."""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )
    severity: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    message: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    evidence_json: Mapped[str] = mapped_column(
        Text,
        default="{}",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
        default=utc_now,
    )


class IncidentRecord(Base):
    """Persisted security incident.

    The threat score, threat level, suspect process and affected-file list are
    first-class columns (not only blobbed inside ``evidence_json``) so that the
    REST API, dashboard and Phase 8 report layer can read them reliably.
    ``evidence_json`` is retained as the full evidence payload.
    """

    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="open",
    )
    severity: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    threat_score: Mapped[float] = mapped_column(
        Float,
        default=0.0,
    )
    threat_level: Mapped[str] = mapped_column(
        String(32),
        default="NORMAL",
    )
    suspect_process_json: Mapped[str] = mapped_column(
        Text,
        default="{}",
    )
    affected_files_json: Mapped[str] = mapped_column(
        Text,
        default="[]",
    )
    score_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    alert_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    evidence_json: Mapped[str] = mapped_column(
        Text,
        default="{}",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
        default=utc_now,
    )


class RuntimeStateRecord(Base):
    """Persisted runtime state."""

    __tablename__ = "runtime_state"

    key: Mapped[str] = mapped_column(
        String(255),
        primary_key=True,
    )
    value: Mapped[str] = mapped_column(
        Text,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
    )


class SubsystemHealthRecord(Base):
    """Persisted subsystem health state."""

    __tablename__ = "subsystem_health"

    name: Mapped[str] = mapped_column(
        String(255),
        primary_key=True,
    )
    healthy: Mapped[bool] = mapped_column(
        Boolean,
    )
    details: Mapped[str] = mapped_column(
        Text,
        default="{}",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
    )


class UserSessionRecord(Base):
    """Persisted user-session telemetry."""

    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )
    user: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    session_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    privilege: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    home: Mapped[str] = mapped_column(
        Text,
        default="",
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
    )


class NetworkConnectionRecord(Base):
    """Persisted network-connection telemetry."""

    __tablename__ = "network_connections"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )
    family: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    local_address: Mapped[str] = mapped_column(
        Text,
        default="",
    )
    remote_address: Mapped[str] = mapped_column(
        Text,
        default="",
    )
    protocol: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    port: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
    )


def _install_sqlite_concurrency_pragmas(engine, *, durable: bool) -> None:
    """Make SQLite tolerate the concurrent writers this process creates.

    RDRS runs one watchdog observer thread per monitored folder plus a process
    monitor, and they all write through this engine. The default rollback
    journal lets a reader block a writer, which surfaces as
    ``sqlite3.OperationalError: cannot commit transaction - SQL statements in
    progress`` -- an event would be dropped and the pipeline logged a failure.

    * ``journal_mode=WAL`` lets readers and a writer proceed together. It needs
      a real file, so it is skipped for the in-memory database.
    * ``busy_timeout`` makes a blocked writer wait for the lock instead of
      failing immediately.
    * ``synchronous=NORMAL`` is the recommended pairing with WAL.
    """
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _apply_pragmas(dbapi_connection, connection_record):  # noqa: ANN001
        try:
            cursor = dbapi_connection.cursor()

            try:
                if durable:
                    cursor.execute("PRAGMA journal_mode=WAL")
                    cursor.execute("PRAGMA synchronous=NORMAL")

                cursor.execute("PRAGMA busy_timeout=30000")
            finally:
                cursor.close()
        except Exception:
            # A pragma that cannot be applied must not stop the engine from
            # being used; the retry paths still cover a transient lock.
            return


class DatabaseManager:
    """Own the RDRS SQLite database and persistence operations.

    ``db_path=None`` selects a **non-durable in-memory** database. That mode
    exists so ``persistence.enabled: false`` still leaves the runtime with a
    working database: detection, scoring and incident response need one, and
    refusing to provide any previously made the runtime impossible to start
    again ("Database must be initialized before incident response").
    """

    def __init__(self, db_path: str | Path | None):
        # ``None`` (or ":memory:") means "do not write anything to disk".
        if db_path is None or str(db_path) == ":memory:":
            self.db_path: Path | None = None
        else:
            self.db_path = Path(db_path)

        self.engine = None

    @property
    def durable(self) -> bool:
        """True when rows survive a restart, i.e. a real file backs the DB."""
        return self.db_path is not None

    def initialize(self) -> None:
        """Create the SQLite engine and all required tables.

        Several threads write to this database at once -- one watchdog observer
        per monitored folder, plus the process-monitor thread. Without WAL mode
        and a busy timeout SQLite raises "cannot commit transaction - SQL
        statements in progress" under that concurrency, which surfaced as lost
        events and a ``rdrs.detection.pipeline.failed`` log entry.
        """
        if self.db_path is None:
            # StaticPool keeps a single connection alive, so every session
            # sees the same in-memory database instead of a private one.
            from sqlalchemy.pool import StaticPool

            self.engine = create_engine(
                "sqlite://",
                future=True,
                connect_args={
                    "check_same_thread": False,
                },
                poolclass=StaticPool,
            )
        else:
            self.db_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            self.engine = create_engine(
                f"sqlite:///{self.db_path}",
                future=True,
                connect_args={
                    "timeout": 30,
                    "check_same_thread": False,
                },
            )

        _install_sqlite_concurrency_pragmas(
            self.engine,
            durable=self.db_path is not None,
        )

        Base.metadata.create_all(self.engine)
        self._migrate_schema()
        self._migrate_indexes()

    # Columns added after the original schema shipped. Existing databases
    # (created before this change) are upgraded in place with ALTER TABLE so
    # that no persisted evidence is lost and no manual migration is required.
    #: Kept for reference: the columns earlier releases added by hand.
    #: The migration no longer uses this map -- it derives the column set from
    #: the SQLAlchemy model so it cannot drift out of date.
    _LEGACY_ADDED_COLUMNS: dict[str, dict[str, str]] = {
        "events": {
            "incident_id": "INTEGER",
        },
        "incidents": {
            "threat_score": "FLOAT DEFAULT 0.0",
            "threat_level": "VARCHAR(32) DEFAULT 'NORMAL'",
            "suspect_process_json": "TEXT DEFAULT '{}'",
            "affected_files_json": "TEXT DEFAULT '[]'",
            "score_id": "INTEGER",
            "alert_id": "INTEGER",
        },
    }

    @staticmethod
    def _migration_ddl(column: Any, dialect: Any) -> str:
        """Build the ``ALTER TABLE ... ADD COLUMN`` definition for a column.

        SQLite refuses to add a NOT NULL column without a default, so a
        non-nullable column is given a type-appropriate literal default and the
        existing rows get a sensible value instead of NULL.
        """
        type_sql = column.type.compile(dialect)
        definition = f"{column.name} {type_sql}"

        if column.nullable:
            return definition

        affinity = type_sql.upper()

        if any(
            token in affinity
            for token in ("INT", "REAL", "FLOA", "DOUB", "NUM", "DEC")
        ):
            return definition + " DEFAULT 0"

        if any(
            token in affinity
            for token in ("CHAR", "CLOB", "TEXT")
        ):
            return definition + " DEFAULT ''"

        # Anything else (for example DATETIME) is added as nullable; the
        # readers already treat a missing value as "unknown".
        return definition

    def _migrate_schema(self) -> None:
        """Add any model column that a pre-existing table is missing.

        The column set is derived from the SQLAlchemy model rather than a
        hand-maintained list. The previous hand-written list had drifted from
        the model: a legacy database was still missing ``incidents.status``
        (and ``evidence_json`` / ``created_at``), so every query against the
        table failed with ``no such column``. Deriving from the model means the
        migration cannot fall behind a schema change again.
        """
        if self.engine is None:
            return

        inspector = sa_inspect(self.engine)
        existing_tables = set(inspector.get_table_names())

        with self.engine.begin() as connection:
            for table in Base.metadata.sorted_tables:
                if table.name not in existing_tables:
                    continue

                present = {
                    column["name"]
                    for column in inspector.get_columns(table.name)
                }

                for column in table.columns:
                    if column.name in present:
                        continue

                    connection.execute(
                        sa_text(
                            f"ALTER TABLE {table.name} "
                            "ADD COLUMN "
                            f"{self._migration_ddl(column, self.engine.dialect)}"
                        )
                    )

    def _migrate_indexes(self) -> None:
        """Create any index the model declares but the database is missing.

        ``create_all`` only builds indexes for tables it creates, so a database
        that predates an index never gains it. Without this, an existing install
        keeps sorting a six-figure ``processes`` table on every dashboard poll.
        Creating an index is additive and lossless.
        """
        inspector = sa_inspect(self.engine)
        existing_tables = set(inspector.get_table_names())

        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue

            present = {
                index["name"]
                for index in inspector.get_indexes(table.name)
            }

            for index in table.indexes:
                if index.name in present:
                    continue

                index.create(bind=self.engine, checkfirst=True)

    def _require_engine(self):
        """Return the initialized engine or raise a clear error."""
        if self.engine is None:
            raise RuntimeError(
                "Database not initialized"
            )

        return self.engine

    @staticmethod
    def _decode_json(value: str | None) -> dict[str, Any]:
        """Safely decode persisted JSON objects."""
        if not value:
            return {}

        try:
            decoded = json.loads(value)
        except (
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            return {}

        return decoded if isinstance(decoded, dict) else {}

    @staticmethod
    def _decode_json_list(value: str | None) -> list[Any]:
        """Safely decode persisted JSON arrays.

        A legacy row may hold a bare ``"/path/to/file"`` string where a JSON
        array belongs. That value is not valid JSON, so it must be treated as
        a single-element list rather than silently discarded - dropping it
        would lose the only record of which file was affected.
        """
        if not value:
            return []

        try:
            decoded = json.loads(value)
        except (
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            # Not JSON at all - keep the raw scalar instead of losing it.
            if isinstance(value, str):
                return [value]

            return []

        if isinstance(decoded, list):
            return decoded

        # Tolerate a legacy single string being stored where a list belongs.
        if isinstance(decoded, str):
            return [decoded]

        return []

    @staticmethod
    def _iso(value: datetime | None) -> str | None:
        """Serialize a database timestamp to ISO-8601."""
        return (value.replace(tzinfo=timezone.utc).isoformat() if value.tzinfo is None else value.isoformat()) if value is not None else None

    def record_runtime_state(
        self,
        state: str,
    ) -> None:
        """Persist the current runtime state."""
        with Session(self._require_engine()) as session:
            record = session.get(
                RuntimeStateRecord,
                "state",
            )

            if record is None:
                record = RuntimeStateRecord(
                    key="state",
                    value=str(state),
                )
                session.add(record)
            else:
                record.value = str(state)
                record.updated_at = utc_now()

            session.commit()

    def record_subsystem(
        self,
        name: str,
        healthy: bool,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Persist subsystem health information."""
        encoded = json.dumps(
            details or {},
            default=str,
        )

        with Session(self._require_engine()) as session:
            record = session.get(
                SubsystemHealthRecord,
                name,
            )

            if record is None:
                session.add(
                    SubsystemHealthRecord(
                        name=str(name),
                        healthy=bool(healthy),
                        details=encoded,
                    )
                )
            else:
                record.healthy = bool(healthy)
                record.details = encoded
                record.updated_at = utc_now()

            session.commit()

    def record_file_event(
        self,
        event: dict[str, Any],
    ) -> int:
        """Persist one real file-system event."""
        with Session(self._require_engine()) as session:
            record = EventRecord(
                path=str(
                    event.get("path", "")
                ),
                action=str(
                    event.get("action", "")
                ),
                previous_path=event.get(
                    "previous_path"
                ),
                new_path=event.get(
                    "new_path"
                ),
                metadata_json=json.dumps(
                    event.get(
                        "metadata",
                        {},
                    ),
                    default=str,
                ),
                incident_id=(
                    int(event["incident_id"])
                    if event.get("incident_id")
                    is not None
                    else None
                ),
            )

            session.add(record)
            session.commit()
            session.refresh(record)

            return int(record.id)

    def attach_events_to_incident(
        self,
        incident_id: int,
        event_ids: list[int],
    ) -> int:
        """Associate already-persisted events with an incident.

        Returns the number of event rows actually updated. Events that already
        belong to another incident are left untouched so that multiple
        incidents stay isolated.
        """
        if not event_ids:
            return 0

        safe_ids = [
            int(value) for value in event_ids
        ]

        with Session(self._require_engine()) as session:
            updated = (
                session.query(EventRecord)
                .filter(
                    EventRecord.id.in_(safe_ids),
                    EventRecord.incident_id.is_(None),
                )
                .update(
                    {
                        EventRecord.incident_id: int(
                            incident_id
                        )
                    },
                    synchronize_session=False,
                )
            )

            session.commit()

            return int(updated)

    def attach_events_to_incident_by_paths(
        self,
        incident_id: int | None,
        paths: list[str],
    ) -> int:
        """Attach unassigned events for specific files to an incident.

        Preferred over window attribution because the affected-file set is
        exact. Events already owned by another incident are never reassigned,
        so concurrent incidents stay isolated. A ``None`` incident id (no
        incident was created) is a no-op that attributes nothing.
        """
        if incident_id is None:
            return 0

        safe_paths = [
            str(value) for value in paths if value
        ]

        if not safe_paths:
            return 0

        with Session(self._require_engine()) as session:
            updated = (
                session.query(EventRecord)
                .filter(
                    EventRecord.incident_id.is_(None),
                    or_(
                        EventRecord.path.in_(safe_paths),
                        EventRecord.new_path.in_(
                            safe_paths
                        ),
                        EventRecord.previous_path.in_(
                            safe_paths
                        ),
                    ),
                )
                .update(
                    {
                        EventRecord.incident_id: int(
                            incident_id
                        )
                    },
                    synchronize_session=False,
                )
            )

            session.commit()

            return int(updated)

    def attach_events_to_incident_by_window(
        self,
        incident_id: int | None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> int:
        """Attach unassigned events inside a time window to an incident.

        Used by the detection pipeline: the events that produced a burst are
        the ones recorded between the previous evaluation and the burst, so
        they are attributed to the incident created from that burst. Events
        already owned by another incident are never reassigned. A ``None``
        incident id is a no-op that attributes nothing.
        """
        if incident_id is None:
            return 0

        with Session(self._require_engine()) as session:
            query = session.query(EventRecord).filter(
                EventRecord.incident_id.is_(None)
            )

            if since is not None:
                query = query.filter(
                    EventRecord.created_at >= since
                )

            if until is not None:
                query = query.filter(
                    EventRecord.created_at <= until
                )

            updated = query.update(
                {
                    EventRecord.incident_id: int(
                        incident_id
                    )
                },
                synchronize_session=False,
            )

            session.commit()

            return int(updated)

    @staticmethod
    def _process_record(process: Any) -> "ProcessRecord":
        """Build a ProcessRecord from a ProcessTelemetry-compatible snapshot."""
        return ProcessRecord(
            pid=int(process.pid),
            name=str(process.name),
            parent_pid=(
                int(process.parent_pid)
                if process.parent_pid is not None
                else None
            ),
            command_line=str(process.command_line),
            cwd=str(process.cwd),
            started_at=process.started_at,
            cpu_percent=float(getattr(process, "cpu_percent", 0.0)),
            memory_rss=int(getattr(process, "memory_rss", 0)),
            memory_percent=float(
                getattr(process, "memory_percent", 0.0)
            ),
            disk_write_bytes=int(
                getattr(process, "disk_write_bytes", 0)
            ),
            executable_path=str(
                getattr(process, "executable_path", "")
            ),
        )

    def record_processes(
        self,
        processes: list[Any],
    ) -> int:
        """Persist many process snapshots in a single transaction.

        One transaction instead of one per process: a full capture is several
        hundred snapshots, and committing each separately made persistence the
        slowest part of the process-monitor cycle.
        """
        if not processes:
            return 0

        with Session(self._require_engine()) as session:
            records = [
                self._process_record(process)
                for process in processes
            ]

            session.add_all(records)
            session.commit()

            return len(records)

    def record_process(
        self,
        process: Any,
    ) -> int:
        """Persist a ProcessTelemetry-compatible snapshot."""
        with Session(self._require_engine()) as session:
            record = self._process_record(process)

            session.add(record)
            session.commit()
            session.refresh(record)

            return int(record.id)

    def record_score(
        self,
        score: float,
        level: str,
        reason: str = "",
    ) -> int:
        """Persist one threat-score evaluation.

        The FSD defines the threat score as a 0-100 value (FR-7). Rejecting
        anything outside that range keeps the stored data honest: a score the
        detector could never produce must not appear in the database, the API
        or a report.
        """
        value = float(score)

        if not 0.0 <= value <= 100.0:
            raise ValueError(
                f"Threat score must be between 0 and 100, got {value}"
            )

        with Session(self._require_engine()) as session:
            record = ScoreRecord(
                score=value,
                level=str(level),
                reason=str(reason),
            )

            session.add(record)
            session.commit()
            session.refresh(record)

            return int(record.id)

    def record_alert(
        self,
        severity: str,
        message: str,
        evidence: dict[str, Any] | None = None,
    ) -> int:
        """Persist one security alert."""
        with Session(self._require_engine()) as session:
            record = AlertRecord(
                severity=str(severity),
                message=str(message),
                evidence_json=json.dumps(
                    evidence or {},
                    default=str,
                ),
            )

            session.add(record)
            session.commit()
            session.refresh(record)

            return int(record.id)

    def record_incident(
        self,
        severity: str,
        description: str,
        evidence: dict[str, Any] | None = None,
        status: str = "open",
        *,
        threat_score: float | None = None,
        threat_level: str | None = None,
        suspect_process: dict[str, Any] | None = None,
        affected_files: list[str] | None = None,
        score_id: int | None = None,
        alert_id: int | None = None,
    ) -> int:
        """Persist one security incident.

        ``threat_score`` / ``threat_level`` / ``suspect_process`` /
        ``affected_files`` are written to dedicated columns so report and API
        readers never have to dig them out of the evidence blob. When they are
        not supplied explicitly they are taken from ``evidence``.
        """
        evidence = evidence or {}

        resolved_score = (
            float(threat_score)
            if threat_score is not None
            else float(evidence.get("score", 0.0) or 0.0)
        )

        resolved_level = str(
            threat_level
            if threat_level is not None
            else evidence.get("level", "NORMAL")
        )

        resolved_suspect = (
            suspect_process
            if suspect_process is not None
            else evidence.get("suspect_process")
        ) or {}

        resolved_files = (
            affected_files
            if affected_files is not None
            else evidence.get("affected_files")
        ) or []

        with Session(self._require_engine()) as session:
            record = IncidentRecord(
                status=str(status),
                severity=str(severity),
                description=str(description),
                threat_score=resolved_score,
                threat_level=resolved_level,
                suspect_process_json=json.dumps(
                    resolved_suspect,
                    default=str,
                ),
                affected_files_json=json.dumps(
                    list(resolved_files),
                    default=str,
                ),
                score_id=(
                    int(score_id)
                    if score_id is not None
                    else None
                ),
                alert_id=(
                    int(alert_id)
                    if alert_id is not None
                    else None
                ),
                evidence_json=json.dumps(
                    evidence,
                    default=str,
                ),
            )

            session.add(record)
            session.commit()
            session.refresh(record)

            return int(record.id)

    def record_user_session(
        self,
        session_data: Any,
    ) -> int:
        """Persist a user-session snapshot."""
        with Session(self._require_engine()) as session:
            record = UserSessionRecord(
                user=str(session_data.user),
                session_id=str(
                    session_data.session_id
                ),
                privilege=str(
                    session_data.privilege
                ),
                home=str(session_data.home),
            )

            session.add(record)
            session.commit()
            session.refresh(record)

            return int(record.id)

    def record_network_connection(
        self,
        connection_data: Any,
    ) -> int:
        """Persist a network-connection snapshot."""
        with Session(self._require_engine()) as session:
            record = NetworkConnectionRecord(
                family=str(
                    connection_data.family
                ),
                local_address=str(
                    connection_data.local_address
                ),
                remote_address=str(
                    connection_data.remote_address
                ),
                protocol=str(
                    connection_data.protocol
                ),
                port=int(
                    connection_data.port
                ),
            )

            session.add(record)
            session.commit()
            session.refresh(record)

            return int(record.id)

    def list_events(
        self,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return newest persisted file events first."""
        safe_limit = max(1, int(limit))

        with Session(self._require_engine()) as session:
            records = (
                session.query(EventRecord)
                .order_by(
                    EventRecord.created_at.desc()
                )
                .limit(safe_limit)
                .all()
            )

            return [
                {
                    "id": record.id,
                    "path": record.path,
                    "action": record.action,
                    "previous_path": (
                        record.previous_path
                    ),
                    "new_path": record.new_path,
                    "metadata": self._decode_json(
                        record.metadata_json
                    ),
                    "incident_id": record.incident_id,
                    "created_at": self._iso(
                        record.created_at
                    ),
                }
                for record in records
            ]

    def list_processes(
        self,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return newest persisted process snapshots first."""
        safe_limit = max(1, int(limit))

        with Session(self._require_engine()) as session:
            records = (
                session.query(ProcessRecord)
                .order_by(
                    ProcessRecord.captured_at.desc()
                )
                .limit(safe_limit)
                .all()
            )

            return [
                {
                    "id": record.id,
                    "pid": record.pid,
                    "name": record.name,
                    "parent_pid": record.parent_pid,
                    "command_line": record.command_line,
                    "cwd": record.cwd,
                    "started_at": record.started_at,
                    "cpu_percent": record.cpu_percent,
                    "memory_rss": record.memory_rss,
                    "memory_percent": (
                        record.memory_percent
                    ),
                    "disk_write_bytes": (
                        record.disk_write_bytes
                    ),
                    "executable_path": (
                        record.executable_path
                    ),
                    "captured_at": self._iso(
                        record.captured_at
                    ),
                }
                for record in records
            ]

    def list_scores(
        self,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return newest persisted threat scores first."""
        safe_limit = max(1, int(limit))

        with Session(self._require_engine()) as session:
            records = (
                session.query(ScoreRecord)
                .order_by(
                    ScoreRecord.created_at.desc()
                )
                .limit(safe_limit)
                .all()
            )

            return [
                {
                    "id": record.id,
                    "score": record.score,
                    "level": record.level,
                    "reason": record.reason,
                    "created_at": self._iso(
                        record.created_at
                    ),
                }
                for record in records
            ]

    def list_alerts(
        self,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return newest persisted alerts first."""
        safe_limit = max(1, int(limit))

        with Session(self._require_engine()) as session:
            records = (
                session.query(AlertRecord)
                .order_by(
                    AlertRecord.created_at.desc()
                )
                .limit(safe_limit)
                .all()
            )

            return [
                {
                    "id": record.id,
                    "severity": record.severity,
                    "message": record.message,
                    "evidence": self._decode_json(
                        record.evidence_json
                    ),
                    "created_at": self._iso(
                        record.created_at
                    ),
                }
                for record in records
            ]

    def list_incidents(
        self,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return newest persisted incidents first."""
        safe_limit = max(1, int(limit))

        with Session(self._require_engine()) as session:
            records = (
                session.query(IncidentRecord)
                .order_by(
                    IncidentRecord.created_at.desc()
                )
                .limit(safe_limit)
                .all()
            )

            return [
                {
                    "id": record.id,
                    # ``incident_id`` is exposed as an alias of ``id`` so the
                    # report builder can resolve the incident identifier from
                    # the same payload whichever key the caller happened to
                    # read from the API.
                    "incident_id": record.id,
                    "status": record.status,
                    "severity": record.severity,
                    "description": record.description,
                    "threat_score": float(
                        record.threat_score or 0.0
                    ),
                    "threat_level": str(
                        record.threat_level or "NORMAL"
                    ),
                    "suspect_process": (
                        self._decode_json(
                            record.suspect_process_json
                        )
                    ),
                    "affected_files": (
                        self._decode_json_list(
                            record.affected_files_json
                        )
                    ),
                    "score_id": record.score_id,
                    "alert_id": record.alert_id,
                    "evidence": self._decode_json(
                        record.evidence_json
                    ),
                    "created_at": self._iso(
                        record.created_at
                    ),
                }
                for record in records
            ]

    def get_incident(
        self,
        incident_id: int,
    ) -> dict[str, Any] | None:
        """Return a single persisted incident by id."""
        with Session(self._require_engine()) as session:
            record = session.get(
                IncidentRecord,
                int(incident_id),
            )

            if record is None:
                return None

            return {
                "id": record.id,
                "incident_id": record.id,
                "status": record.status,
                "severity": record.severity,
                "description": record.description,
                "threat_score": float(
                    record.threat_score or 0.0
                ),
                "threat_level": str(
                    record.threat_level or "NORMAL"
                ),
                "suspect_process": self._decode_json(
                    record.suspect_process_json
                ),
                "affected_files": (
                    self._decode_json_list(
                        record.affected_files_json
                    )
                ),
                "score_id": record.score_id,
                "alert_id": record.alert_id,
                "evidence": self._decode_json(
                    record.evidence_json
                ),
                "created_at": self._iso(
                    record.created_at
                ),
            }

    def list_events_for_incident(
        self,
        incident_id: int | None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Return only the events attributed to one incident.

        A ``None`` incident id means "no incident was ever created", which is
        a legitimate state (a Normal or Warning score produces no incident).
        It yields an empty list rather than raising, so callers can request
        the timeline unconditionally.
        """
        if incident_id is None:
            return []

        safe_limit = max(1, int(limit))

        with Session(self._require_engine()) as session:
            records = (
                session.query(EventRecord)
                .filter(
                    EventRecord.incident_id
                    == int(incident_id)
                )
                .order_by(
                    EventRecord.created_at.asc()
                )
                .limit(safe_limit)
                .all()
            )

            return [
                {
                    "id": record.id,
                    "path": record.path,
                    "action": record.action,
                    "previous_path": (
                        record.previous_path
                    ),
                    "new_path": record.new_path,
                    "metadata": self._decode_json(
                        record.metadata_json
                    ),
                    "incident_id": record.incident_id,
                    "created_at": self._iso(
                        record.created_at
                    ),
                }
                for record in records
            ]

    #: Tables that may be counted, mapped to their model.
    COUNTABLE_TABLES: dict[str, Any] = {
        "events": EventRecord,
        "file_events": EventRecord,
        "processes": ProcessRecord,
        "process_telemetry": ProcessRecord,
        "scores": ScoreRecord,
        "alerts": AlertRecord,
        "incidents": IncidentRecord,
        "user_sessions": UserSessionRecord,
        "network_connections": NetworkConnectionRecord,
    }

    def count_records(
        self,
        table: str,
    ) -> int:
        """Return the number of records in a supported table."""
        model = self.COUNTABLE_TABLES.get(table)

        if model is None:
            raise ValueError(
                f"Unsupported table: {table}"
            )

        with Session(self._require_engine()) as session:
            return int(
                session.query(model).count()
            )

    def count_records_many(
        self,
        tables: list[str],
    ) -> dict[str, int]:
        """Count several tables from ONE consistent snapshot.

        ``count_records`` opens its own transaction per call, so a dashboard
        summary built from five separate calls can be torn by a concurrent
        writer: it can report a score whose incident has not been counted yet
        (or the reverse), making the counters look inconsistent when the stored
        data is perfectly correct. Running every count inside a single
        transaction gives the caller a coherent read.
        """
        models: dict[str, Any] = {}

        for table in tables:
            model = self.COUNTABLE_TABLES.get(table)

            if model is None:
                raise ValueError(
                    f"Unsupported table: {table}"
                )

            models[table] = model

        counts: dict[str, int] = {}

        with Session(self._require_engine()) as session:
            for table, model in models.items():
                counts[table] = int(
                    session.query(model).count()
                )

        return counts

    def close(self) -> None:
        """Dispose the database engine."""
        if self.engine is not None:
            self.engine.dispose()
            self.engine = None

    def __del__(self) -> None:
        """Release the engine's pooled connections as a last resort.

        ``close()`` remains the supported way to release resources. This guard
        ensures a manager that is dropped without an explicit close still
        returns its SQLite connections instead of leaking them until
        interpreter shutdown (surfacing as ``ResourceWarning``).
        """
        try:
            engine = getattr(self, "engine", None)
        except Exception:
            return

        if engine is not None:
            try:
                engine.dispose()
            except Exception:
                pass

            try:
                self.engine = None
            except Exception:
                pass

    def __enter__(self) -> "DatabaseManager":
        if self.engine is None:
            self.initialize()

        return self

    def __exit__(
        self,
        exc_type,
        exc,
        tb,
    ) -> None:
        self.close()




