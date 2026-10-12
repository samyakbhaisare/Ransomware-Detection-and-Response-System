"""Fail-closed public projections; original stored data is never modified."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any

_KEY = secrets.token_bytes(32)
_SIGNALS = {"rapid_encryption", "mass_rename", "extension_change", "high_entropy", "cpu_spike", "unknown_program"}
_ENUMS = {
    "state": {"RUNNING", "STOPPED", "STARTING", "SHUTTING_DOWN", "DEGRADED", "FAILED"},
    "action": {"created", "modified", "deleted", "moved", "renamed"},
    "level": {"NORMAL", "WARNING", "CRITICAL", "Normal", "Warning", "Critical"},
    "status": {"open", "closed", "resolved", "active"},
    "log_level": {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"},
    "file_type": {".txt", ".doc", ".docx", ".pdf", ".csv", ".json", ".png", ".jpg", ".exe", ".locked", ".enc", "text", "binary", "unknown"},
}
_NUMBERS = {"id", "incident_id", "score_id", "alert_id", "pid", "parent_pid", "score", "threat_score", "cpu_percent", "memory_rss", "memory_percent", "disk_write_bytes", "started_at", "event_count", "process_count", "score_count", "alert_count", "incident_count", "count", "affected_file_count", "shutdown_timeout", "value", "modifications", "entropy", "size", "size_bytes"}
_BOOLS = {"healthy", "critical", "degraded", "simulation_mode", "persistence_enabled", "config_available", "exists", "detected", "dangerous_burst", "enabled"} | _SIGNALS
_TIMES = {"created_at", "captured_at", "timestamp", "time", "generated_at", "started_at_iso"}
_PATHS = {"path", "previous_path", "new_path", "executable_path", "cwd", "home"}
_OBJECTS = {"summary", "score", "metadata", "evidence", "suspect_process", "incident", "signals", "telemetry", "cpu", "entropy", "subsystems", "config", "detection_thresholds", "scoring_weights"}
_LISTS = {"events", "processes", "alerts", "incidents", "timeline", "scores", "top_modified", "affected_files", "recommendations", "unhealthy_subsystems"}
_SUBSYSTEMS = {"config", "logging", "database", "detection", "response", "file_monitor", "process_monitor"}
_THRESHOLDS = {"window_seconds", "modified_files_per_minute", "renames", "extension_changes", "average_entropy", "cpu_percent"}


def token(kind: str, value: Any) -> str:
    digest = hmac.new(_KEY, str(value).encode(), hashlib.sha256).hexdigest()[:16]
    return f"[Redacted {kind} {digest}]"


def redact(value: Any, key: str = "") -> Any:
    if key in _PATHS:
        return token("path", value) if value else ""
    if key == "command_line":
        return "[Redacted command]" if value else ""
    if key in {"name", "user", "username"}:
        return token("identity", value) if value else ""
    if key in {"message", "reason"}:
        import re
        signals = [name for name in sorted(_SIGNALS) if re.search(r"\b" + re.escape(name) + r"\b", str(value))]
        return "Recorded detection" + ("; signals: " + ", ".join(signals) if signals else "; identifying text redacted") if value else ""
    if key in {"description", "suspect_process_attribution"}:
        return "Recorded detection; identifying text redacted" if value else ""
    if key in _TIMES:
        # Only ISO timestamps, not arbitrary free-text values.
        from datetime import datetime
        try:
            datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return value
        except (TypeError, ValueError):
            return None
    enum_key = "level" if key in {"severity", "threat_level"} else key
    if enum_key in _ENUMS:
        return value if value in _ENUMS[enum_key] else "Redacted"
    if key == "environment":
        return "Public read-only"
    if key == "report_version":
        return "1.0"
    if isinstance(value, dict):
        result = {}
        for field, item in value.items():
            if key == "subsystems" and field in _SUBSYSTEMS:
                result[field] = redact(item) if isinstance(item, dict) else bool(item)
            elif key in {"detection_thresholds", "scoring_weights"} and field in (_THRESHOLDS | _SIGNALS):
                if isinstance(item, (int, float)) and not isinstance(item, bool):
                    result[field] = item
            elif field in (_NUMBERS | _BOOLS | _TIMES | _PATHS | _OBJECTS | _LISTS | set(_ENUMS) | {"severity", "threat_level", "name", "environment", "command_line", "message", "description", "reason", "suspect_process_attribution", "report_version"}):
                result[field] = redact(item, field)
        return result
    if isinstance(value, list):
        if key == "affected_files":
            return [token("path", item) for item in value]
        if key == "recommendations":
            return ["Recorded recommendation; identifying text redacted" for _ in value]
        if key == "unhealthy_subsystems":
            return [item for item in value if item in _SUBSYSTEMS]
        return [redact(item) for item in value]
    if value is None:
        return None
    if isinstance(value, bool):
        return value if key in _BOOLS else None
    if isinstance(value, (int, float)):
        return value if key in _NUMBERS or key == "" else None
    return "[Redacted]"
