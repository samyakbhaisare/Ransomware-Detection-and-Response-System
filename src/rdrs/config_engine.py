from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ConfigEngine:
    monitoring: dict[str, Any] = field(default_factory=lambda: {"paths": ["."], "recursive": True})
    detection: dict[str, Any] = field(default_factory=lambda: {"rules": ["default"]})
    response: dict[str, Any] = field(default_factory=lambda: {"actions": ["log"]})
    ai: dict[str, Any] = field(default_factory=lambda: {"enabled": False})

    def validate(self) -> None:
        if not isinstance(self.monitoring, dict):
            raise ValueError("monitoring must be a dictionary")
        if not isinstance(self.detection, dict):
            raise ValueError("detection must be a dictionary")
        if not isinstance(self.response, dict):
            raise ValueError("response must be a dictionary")
        if not isinstance(self.ai, dict):
            raise ValueError("ai must be a dictionary")

        paths = self.monitoring.get("paths")
        if not isinstance(paths, list) or not paths:
            raise ValueError("monitoring.paths must be a non-empty list")

        rules = self.detection.get("rules")
        if not isinstance(rules, list) or not rules:
            raise ValueError("detection.rules must be a non-empty list")

        actions = self.response.get("actions")
        if not isinstance(actions, list) or not actions:
            raise ValueError("response.actions must be a non-empty list")

        if not isinstance(self.ai.get("enabled", False), bool):
            raise ValueError("ai.enabled must be a boolean")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "monitoring": self.monitoring,
            "detection": self.detection,
            "response": self.response,
            "ai": self.ai,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ConfigEngine":
        monitoring = payload.get("monitoring", {"paths": ["."], "recursive": True})
        detection = payload.get("detection", {"rules": ["default"]})
        response = payload.get("response", {"actions": ["log"]})
        ai = payload.get("ai", {"enabled": False})
        engine = cls(monitoring=monitoring, detection=detection, response=response, ai=ai)
        engine.validate()
        return engine

