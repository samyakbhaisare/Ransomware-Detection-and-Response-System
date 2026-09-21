from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping


class ThreatLevel(str, Enum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class ThreatScore:
    rapid_encryption: int = 0
    mass_rename: int = 0
    high_entropy: int = 0
    cpu_spike: int = 0
    unknown_program: int = 0

    @property
    def total(self) -> int:
        return min(
            100,
            self.rapid_encryption
            + self.mass_rename
            + self.high_entropy
            + self.cpu_spike
            + self.unknown_program,
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
