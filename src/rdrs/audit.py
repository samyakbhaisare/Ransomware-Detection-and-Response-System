from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class AuditEntry:
    actor: str
    action: str
    result: str
    success: bool
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "actor": self.actor,
            "action": self.action,
            "result": self.result,
            "success": self.success,
            "details": self.details,
            "timestamp": self.timestamp,
        }


class AuditEngine:
    def __init__(self) -> None:
        self.entries: list[AuditEntry] = []

    def record(self, actor: str, action: str, result: str, *, success: bool = True, details: dict[str, Any] | None = None) -> dict[str, Any]:
        entry = AuditEntry(actor=actor, action=action, result=result, success=success, details=details or {})
        self.entries.append(entry)
        return entry.to_dict()
