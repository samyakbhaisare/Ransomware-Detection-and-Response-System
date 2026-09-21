from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .policy import Policy, PolicyEngine


@dataclass(frozen=True)
class Detection:
    detection_id: str
    rule_name: str
    resource: str
    severity: str
    confidence: float
    action: str
    reason: str
    evidence: dict[str, Any]
    timestamp: str
    tags: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "detection_id": self.detection_id,
            "rule_name": self.rule_name,
            "resource": self.resource,
            "severity": self.severity,
            "confidence": self.confidence,
            "action": self.action,
            "reason": self.reason,
            "evidence": self.evidence,
            "timestamp": self.timestamp,
            "tags": list(self.tags),
        }


class DetectionEngine:
    """Real rule-based detection engine for RDRS events."""

    VALID_SEVERITIES = {"low", "medium", "high", "critical"}

    def __init__(
        self,
        policies: list[Policy] | None = None,
        *,
        deduplicate: bool = True,
    ) -> None:
        self.policy_engine = PolicyEngine(policies or [])
        self.deduplicate = deduplicate
        self._history: list[Detection] = []
        self._fingerprints: set[str] = set()

    @property
    def history(self) -> list[Detection]:
        return list(self._history)

    def add_policy(self, policy: Policy) -> None:
        self.policy_engine.add_policy(policy)

    def evaluate(
        self,
        resource: str,
        context: dict[str, Any] | None = None,
    ) -> Detection | None:
        context = dict(context or {})

        decision = self.policy_engine.evaluate(
            resource,
            context,
        )

        if decision["policy"] is None:
            return None

        action = str(decision.get("action") or "")
        policy_name = str(decision["policy"])

        policy = next(
            (
                item
                for item in self.policy_engine.policies
                if item.name == policy_name
            ),
            None,
        )

        if policy is None:
            return None

        severity = self._severity(context, policy)
        confidence = self._confidence(context, policy)

        evidence = {
            "resource": resource,
            "context": context,
            "policy": policy_name,
            "decision": decision,
        }

        fingerprint = self._fingerprint(
            policy_name,
            resource,
            context,
            action,
        )

        if self.deduplicate and fingerprint in self._fingerprints:
            return None

        timestamp = datetime.now(timezone.utc).isoformat()

        detection = Detection(
            detection_id=fingerprint[:24],
            rule_name=policy_name,
            resource=resource,
            severity=severity,
            confidence=confidence,
            action=action or policy.action,
            reason="policy_match",
            evidence=evidence,
            timestamp=timestamp,
            tags=self._tags(resource, severity, context),
        )

        self._fingerprints.add(fingerprint)
        self._history.append(detection)

        return detection

    def detect(
        self,
        event: dict[str, Any],
    ) -> Detection | None:
        resource = str(event.get("resource", ""))

        if not resource:
            raise ValueError("Detection event requires a resource")

        context = event.get("context", {})

        if not isinstance(context, dict):
            raise TypeError("Detection event context must be a dictionary")

        return self.evaluate(
            resource,
            context,
        )

    def clear_history(self) -> None:
        self._history.clear()
        self._fingerprints.clear()

    @staticmethod
    def _severity(
        context: dict[str, Any],
        policy: Policy,
    ) -> str:
        value = context.get(
            "severity",
            policy.condition.get("severity", "medium"),
        )

        severity = str(value).lower()

        if severity not in DetectionEngine.VALID_SEVERITIES:
            raise ValueError(
                f"Invalid severity: {severity}"
            )

        return severity

    @staticmethod
    def _confidence(
        context: dict[str, Any],
        policy: Policy,
    ) -> float:
        value = context.get(
            "confidence",
            policy.condition.get("confidence", 1.0),
        )

        confidence = float(value)

        if not 0.0 <= confidence <= 1.0:
            raise ValueError(
                "confidence must be between 0.0 and 1.0"
            )

        return confidence

    @staticmethod
    def _tags(
        resource: str,
        severity: str,
        context: dict[str, Any],
    ) -> tuple[str, ...]:
        tags = {
            resource.lower(),
            f"severity:{severity}",
        }

        event_type = context.get("event_type")

        if event_type:
            tags.add(
                f"event:{str(event_type).lower()}"
            )

        return tuple(sorted(tags))

    @staticmethod
    def _fingerprint(
        policy_name: str,
        resource: str,
        context: dict[str, Any],
        action: str,
    ) -> str:
        payload = {
            "policy": policy_name,
            "resource": resource,
            "context": context,
            "action": action,
        }

        encoded = json.dumps(
            payload,
            sort_keys=True,
            default=str,
        ).encode("utf-8")

        return hashlib.sha256(encoded).hexdigest()
