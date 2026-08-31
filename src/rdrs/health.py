from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ComponentHealth:
    healthy: bool = True
    details: dict[str, Any] = field(default_factory=dict)


class HealthRegistry:
    def __init__(self) -> None:
        self._components: dict[str, ComponentHealth] = {}

    def update(self, name: str, *, healthy: bool, details: dict[str, Any] | None = None) -> None:
        self._components[name] = ComponentHealth(healthy=bool(healthy), details=details or {})

    def snapshot(self) -> dict[str, Any]:
        snapshot = {name: {"healthy": info.healthy, "details": info.details} for name, info in self._components.items()}
        overall = all(item["healthy"] for item in snapshot.values()) if snapshot else True
        snapshot["overall"] = overall
        return snapshot
