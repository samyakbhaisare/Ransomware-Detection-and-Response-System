from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
import uuid


@dataclass(slots=True)
class SecurityEvent:
    event_id: str
    source: str
    route: str
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    severity: str = "info"

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "source": self.source,
            "route": self.route,
            "type": self.type,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "severity": self.severity,
        }


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable[[SecurityEvent], Any]]] = {}
        self._seen_event_ids: set[str] = set()
        self._published: list[SecurityEvent] = []

    def subscribe(self, route: str, callback: Callable[[SecurityEvent], Any]) -> None:
        self._subscribers.setdefault(route, []).append(callback)

    def publish(
        self,
        route: str,
        *,
        event_type: str,
        payload: dict[str, Any] | None = None,
        source: str | None = None,
        severity: str = "info",
    ) -> SecurityEvent:
        event = SecurityEvent(
            event_id=str(uuid.uuid4()),
            source=source or route,
            route=route,
            type=event_type,
            payload=payload or {},
            severity=severity,
        )
        self.publish_event(event)
        return event

    def publish_event(self, event: SecurityEvent) -> SecurityEvent | None:
        if event.event_id in self._seen_event_ids:
            return None
        self._seen_event_ids.add(event.event_id)
        self._published.append(event)
        for callback in list(self._subscribers.get(event.route, [])):
            try:
                callback(event)
            except Exception:
                continue
        return event

    def drain(self) -> list[SecurityEvent]:
        drained = list(self._published)
        self._published.clear()
        return drained

    def route_count(self, route: str) -> int:
        return len(self._subscribers.get(route, []))

    def __len__(self) -> int:
        return len(self._published)
