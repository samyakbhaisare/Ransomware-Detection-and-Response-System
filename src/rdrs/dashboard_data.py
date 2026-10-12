from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select

from .storage import (
    DatabaseManager,
    EventRecord,
    ScoreRecord,
)


class DashboardData:
    """
    Read-only aggregation layer for the RDRS web application.

    This class does not create, modify, delete, or simulate security data.
    It reads persisted RDRS observations and exposes one stable JSON-safe
    contract for the REST API and dashboard.

    Data sources:
        - scores     -> current threat score / level
        - events     -> file activity / entropy history
        - processes  -> CPU and process telemetry
        - alerts     -> generated alerts
        - incidents  -> generated incidents

    The class is intentionally independent from the runtime lifecycle so the
    API can safely read persisted information while the monitor is running
    or stopped.
    """

    DEFAULT_EVENT_LIMIT = 50
    DEFAULT_PROCESS_LIMIT = 60
    DEFAULT_ALERT_LIMIT = 50
    DEFAULT_INCIDENT_LIMIT = 50
    DEFAULT_MODIFIED_FILE_LIMIT = 10

    def __init__(self, database: DatabaseManager):
        if database is None:
            raise ValueError("database is required")

        self.database = database

    # ------------------------------------------------------------------
    # Serialization helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _iso(value: Any) -> str | None:
        """Convert datetime-like values into JSON-safe ISO strings."""
        if value is None:
            return None

        if hasattr(value, "isoformat"):
            return value.isoformat()

        return str(value)

    @staticmethod
    def _json_object(value: Any) -> dict[str, Any]:
        """
        Safely decode event metadata.

        Storage implementations may return metadata either as a dict or
        as a JSON string. Invalid metadata never breaks the dashboard.
        """
        if value is None:
            return {}

        if isinstance(value, dict):
            return value

        if isinstance(value, str):
            try:
                decoded = json.loads(value)

                if isinstance(decoded, dict):
                    return decoded

            except (TypeError, ValueError, json.JSONDecodeError):
                pass

        return {}

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        """Convert a value to float without breaking API generation."""
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        """Convert a value to int without breaking API generation."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    # ------------------------------------------------------------------
    # Threat score
    # ------------------------------------------------------------------

    def latest_score(self) -> dict[str, Any]:
        """
        Return the most recent persisted threat score.

        FR-7 / FR-8 provide the score and alert/incident state consumed here.
        No score is invented when the database is empty.
        """
        with self.database.engine.connect() as connection:
            row = connection.execute(
                ScoreRecord.__table__.select()
                .order_by(ScoreRecord.created_at.desc())
                .limit(1)
            ).mappings().first()

        if row is None:
            return {
                "score": 0.0,
                "level": "Normal",
                "reason": "",
                "created_at": None,
            }

        return {
            "score": self._safe_float(row["score"]),
            "level": str(row["level"]),
            "reason": str(row["reason"] or ""),
            "created_at": self._iso(row["created_at"]),
        }

    # ------------------------------------------------------------------
    # Processes
    # ------------------------------------------------------------------

    def recent_processes(
        self,
        limit: int = DEFAULT_PROCESS_LIMIT,
        *,
        cursor: tuple[Any, int] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Return recent persisted psutil process snapshots.

        FR-4 process telemetry is exposed for the dashboard and CPU chart.
        """
        return self.database.list_processes(
            limit=max(1, int(limit)),
            cursor=cursor,
        )

    # ------------------------------------------------------------------
    # File events
    # ------------------------------------------------------------------

    def recent_events(
        self,
        limit: int = DEFAULT_EVENT_LIMIT,
    ) -> list[dict[str, Any]]:
        """
        Return recent persisted file-system events.

        FR-2 and FR-3 data are consumed from the real event database.
        """
        limit = max(1, int(limit))

        events = self.database.list_events(limit=limit)

        normalized: list[dict[str, Any]] = []

        for event in events:
            if not isinstance(event, dict):
                continue

            normalized.append(
                {
                    "id": event.get("id"),
                    "path": event.get("path") or "",
                    "action": event.get("action") or "",
                    "previous_path": (
                        event.get("previous_path") or ""
                    ),
                    "new_path": (
                        event.get("new_path") or ""
                    ),
                    "metadata": self._json_object(
                        event.get("metadata")
                    ),
                    "created_at": self._iso(
                        event.get("created_at")
                    ),
                }
            )

        return normalized

    # ------------------------------------------------------------------
    # Alerts
    # ------------------------------------------------------------------

    def recent_alerts(
        self,
        limit: int = DEFAULT_ALERT_LIMIT,
    ) -> list[dict[str, Any]]:
        """Return real persisted RDRS alerts."""
        limit = max(1, int(limit))

        alerts = self.database.list_alerts(limit=limit)

        normalized: list[dict[str, Any]] = []

        for alert in alerts:
            if not isinstance(alert, dict):
                continue

            normalized.append(
                {
                    "id": alert.get("id"),
                    "severity": (
                        alert.get("severity")
                        or alert.get("level")
                        or ""
                    ),
                    "message": (
                        alert.get("message")
                        or alert.get("reason")
                        or ""
                    ),
                    "evidence": alert.get("evidence") or {},
                    "score": alert.get("score"),
                    "threat_score": alert.get("threat_score"),
                    "created_at": self._iso(
                        alert.get("created_at")
                    ),
                }
            )

        return normalized

    # ------------------------------------------------------------------
    # Incidents
    # ------------------------------------------------------------------

    def recent_incidents(
        self,
        limit: int = DEFAULT_INCIDENT_LIMIT,
    ) -> list[dict[str, Any]]:
        """Return real persisted RDRS incidents."""
        limit = max(1, int(limit))

        incidents = self.database.list_incidents(
            limit=limit
        )

        normalized: list[dict[str, Any]] = []

        for incident in incidents:
            if not isinstance(incident, dict):
                continue

            normalized.append(
                {
                    "id": incident.get("id"),
                    "severity": (
                        incident.get("severity")
                        or incident.get("level")
                        or ""
                    ),
                    "status": incident.get("status") or "",
                    "description": (
                        incident.get("description")
                        or incident.get("reason")
                        or ""
                    ),
                    "evidence": incident.get(
                        "evidence"
                    ),
                    "created_at": self._iso(
                        incident.get("created_at")
                    ),
                }
            )

        return normalized

    # ------------------------------------------------------------------
    # Top modified files
    # ------------------------------------------------------------------

    def top_modified_files(
        self,
        limit: int = DEFAULT_MODIFIED_FILE_LIMIT,
    ) -> list[dict[str, Any]]:
        """
        Aggregate modified file paths from persisted events.

        The result is calculated from actual database events. No counters
        are maintained separately and no values are fabricated.
        """
        limit = max(1, int(limit))

        path = func.coalesce(
            func.nullif(EventRecord.path, ""),
            EventRecord.new_path,
        )
        statement = (
            select(
                path.label("path"),
                func.count().label("modifications"),
            )
            .where(
                EventRecord.action.in_(["modify", "modified"]),
                path.is_not(None),
                path != "",
            )
            .group_by(path)
            .order_by(func.count().desc(), path.asc())
            .limit(limit)
        )

        with self.database.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()

        return [
            {
                "path": str(row["path"]),
                "modifications": int(row["modifications"]),
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # CPU / entropy telemetry
    # ------------------------------------------------------------------

    def cpu_history(
        self,
        limit: int = DEFAULT_PROCESS_LIMIT,
        *,
        processes: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Return chronological CPU telemetry for dashboard charting.

        Values originate from ProcessRecord / psutil snapshots.
        """
        if processes is None:
            processes = self.recent_processes(limit)
        else:
            processes = list(processes[:max(1, int(limit))])

        processes.reverse()

        return [
            {
                "time": process["captured_at"],
                "value": process["cpu_percent"],
                "pid": process["pid"],
                "name": process["name"],
            }
            for process in processes
            if process["captured_at"] is not None
        ]

    def entropy_history(
        self,
        limit: int = DEFAULT_EVENT_LIMIT,
        *,
        events: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Return entropy values recorded with file events.

        Only events containing a valid persisted entropy value are included.
        """
        if events is None:
            events = self.recent_events(limit)
        else:
            events = list(events[:max(1, int(limit))])

        history: list[dict[str, Any]] = []

        for event in reversed(events):
            metadata = event["metadata"]

            entropy = metadata.get("entropy")

            if entropy is None:
                continue

            try:
                entropy_value = float(entropy)
            except (TypeError, ValueError):
                continue

            if entropy_value < 0.0 or entropy_value > 8.0:
                continue

            history.append(
                {
                    "time": event["created_at"],
                    "value": entropy_value,
                    "path": event["path"],
                    "action": event["action"],
                }
            )

        return history

    def telemetry(
        self,
        *,
        process_limit: int = DEFAULT_PROCESS_LIMIT,
        event_limit: int = DEFAULT_EVENT_LIMIT,
    ) -> dict[str, list[dict[str, Any]]]:
        """
        Return the complete CPU and entropy chart data contract.
        """
        return {
            "cpu": self.cpu_history(process_limit),
            "entropy": self.entropy_history(event_limit),
        }

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """
        Return live database counters.

        Counts are read directly from the persistence layer, from a single
        consistent snapshot. Building them from five independent queries let a
        concurrent writer tear the result -- reporting a score whose incident
        had not been counted yet -- which made the dashboard counters look
        inconsistent when the stored data was correct.
        """
        tables = [
            "events",
            "processes",
            "scores",
            "alerts",
            "incidents",
        ]

        count_many = getattr(
            self.database,
            "count_records_many",
            None,
        )

        if count_many is not None:
            counts = count_many(tables)
        else:
            counts = {
                table: self.database.count_records(table)
                for table in tables
            }

        return {
            "event_count": counts["events"],
            "process_count": counts["processes"],
            "score_count": counts["scores"],
            "alert_count": counts["alerts"],
            "incident_count": counts["incidents"],
        }

    # ------------------------------------------------------------------
    # Complete dashboard contract
    # ------------------------------------------------------------------

    def complete(
        self,
        *,
        event_limit: int = DEFAULT_EVENT_LIMIT,
        process_limit: int = DEFAULT_PROCESS_LIMIT,
        alert_limit: int = DEFAULT_ALERT_LIMIT,
        incident_limit: int = DEFAULT_INCIDENT_LIMIT,
        modified_file_limit: int = (
            DEFAULT_MODIFIED_FILE_LIMIT
        ),
    ) -> dict[str, Any]:
        """
        Build the complete dashboard response.

        This is the single read-only data contract consumed by the
        Phase 7 web application.
        """
        events = self.recent_events(event_limit)
        processes = self.recent_processes(process_limit)
        alerts = self.recent_alerts(alert_limit)
        incidents = self.recent_incidents(
            incident_limit
        )

        return {
            "summary": self.summary(),

            "score": self.latest_score(),

            "events": events,

            "processes": processes,

            "alerts": alerts,

            "incidents": incidents,

            "top_modified": self.top_modified_files(
                modified_file_limit
            ),

            "telemetry": {
                "cpu": self.cpu_history(
                    process_limit,
                    processes=processes,
                ),
                "entropy": self.entropy_history(
                    event_limit,
                    events=events,
                ),
            },
        }

