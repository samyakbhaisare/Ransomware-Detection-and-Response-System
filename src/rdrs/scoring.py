from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Mapping


class ThreatLevel(str, Enum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class ThreatScore:
    rapid_encryption: float = 0.0
    mass_rename: float = 0.0
    high_entropy: float = 0.0
    cpu_spike: float = 0.0
    unknown_program: float = 0.0

    def __post_init__(self) -> None:
        for name in (
            "rapid_encryption",
            "mass_rename",
            "high_entropy",
            "cpu_spike",
            "unknown_program",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or value < 0
            ):
                raise ValueError(
                    f"Threat-score contribution {name} must be a finite, "
                    "non-negative number"
                )

    @property
    def total(self) -> float:
        return max(
            0.0,
            min(
                100.0,
                self.rapid_encryption
                + self.mass_rename
                + self.high_entropy
                + self.cpu_spike
                + self.unknown_program,
            ),
        )

    @property
    def level(self) -> ThreatLevel:
        if self.total >= 70:
            return ThreatLevel.CRITICAL
        if self.total >= 40:
            return ThreatLevel.WARNING
        return ThreatLevel.NORMAL


class ThreatScoringEngine:
    DEFAULT_WEIGHTS = {
        "rapid_encryption": 40,
        "mass_rename": 30,
        "high_entropy": 25,
        "cpu_spike": 15,
        "unknown_program": 10,
    }

    def __init__(
        self,
        weights: Mapping[str, int] | None = None,
    ) -> None:
        self.weights = dict(self.DEFAULT_WEIGHTS)

        if weights is not None:
            unknown = set(weights) - set(self.DEFAULT_WEIGHTS)
            if unknown:
                raise ValueError(
                    f"Unknown scoring weights: {sorted(unknown)}"
                )

            for name, value in weights.items():
                value = int(value)

                if value < 0:
                    raise ValueError(
                        f"Weight must be non-negative: {name}"
                    )

                self.weights[name] = value

    def calculate(
        self,
        *,
        rapid_encryption: bool = False,
        mass_rename: bool = False,
        high_entropy: bool = False,
        cpu_spike: bool = False,
        unknown_program: bool = False,
    ) -> ThreatScore:
        return ThreatScore(
            rapid_encryption=(
                self.weights["rapid_encryption"]
                if rapid_encryption else 0
            ),
            mass_rename=(
                self.weights["mass_rename"]
                if mass_rename else 0
            ),
            high_entropy=(
                self.weights["high_entropy"]
                if high_entropy else 0
            ),
            cpu_spike=(
                self.weights["cpu_spike"]
                if cpu_spike else 0
            ),
            unknown_program=(
                self.weights["unknown_program"]
                if unknown_program else 0
            ),
        )
