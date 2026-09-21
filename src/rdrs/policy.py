from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Policy:
    name: str
    action: str
    resource: str
    condition: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True

    def matches(self, resource: str, context: dict[str, Any]) -> bool:
        if not self.enabled:
            return False
        if self.resource != resource:
            return False
        for key, expected in self.condition.items():
            actual = context.get(key)
            if isinstance(expected, str) and isinstance(actual, str):
                if actual.startswith(expected):
                    continue
            if actual != expected:
                return False
        return True


class PolicyEngine:
    def __init__(self, policies: list[Policy] | None = None):
        self.policies = list(policies or [])

    def evaluate(self, resource: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        context = context or {}
        for policy in self.policies:
            if policy.matches(resource, context):
                return {"allowed": policy.action.lower() != "deny", "policy": policy.name, "reason": "matched_policy"}
        return {"allowed": False, "policy": None, "reason": "no_policy"}

    def add_policy(self, policy: Policy) -> None:
        self.policies.append(policy)

