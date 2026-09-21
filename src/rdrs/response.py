from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .audit import AuditEngine
from .quarantine import QuarantineManager
from .scoring import ThreatLevel, ThreatScore
from .storage import DatabaseManager


@dataclass(frozen=True)
class ResponseResult:
    score_id: int
    alert_id: int | None
    incident_id: int | None
    quarantined_files: tuple[str, ...]
    level: ThreatLevel


class IncidentResponseEngine:
    """Phase 5 threat scoring response, alerting, incidents and evidence handling."""

    def __init__(
        self,
        database: DatabaseManager,
        *,
        simulation_mode: bool = True,
        quarantine_dir: str | Path = "data/quarantine",
        audit_engine: AuditEngine | None = None,
    ) -> None:
        self.database = database
        self.simulation_mode = bool(simulation_mode)
        self.audit = audit_engine or AuditEngine()
        self.quarantine = QuarantineManager(
            quarantine_dir,
            simulation_mode=self.simulation_mode,
        )

    def process(
        self,
        score: ThreatScore,
        *,
        affected_files: list[str] | None = None,
        suspect_process: dict[str, Any] | None = None,
        reason: str = "",
        signals: dict[str, Any] | None = None,
    ) -> ResponseResult:
        affected = self._normalize_files(affected_files or [])

        score_id = self.database.record_score(
            score=float(score.total),
            level=score.level.value,
            reason=reason,
        )

        alert_id: int | None = None
        incident_id: int | None = None
        quarantined_files: list[str] = []

        # ``signals`` records *which* detection signal fired, so the generated
        # incident report can explain the threat and derive recommendations
        # from the real evidence instead of the score alone. When the caller
        # does not supply them, they are derived from the score's own weights
        # (a non-zero weight means that signal fired).
        resolved_signals = (
            dict(signals)
            if signals is not None
            else {
                "rapid_encryption": bool(score.rapid_encryption),
                "mass_rename": bool(score.mass_rename),
                "high_entropy": bool(score.high_entropy),
                "cpu_spike": bool(score.cpu_spike),
                "unknown_program": bool(score.unknown_program),
            }
        )

        evidence = {
            "score": score.total,
            "level": score.level.value,
            "affected_files": affected,
            "suspect_process": suspect_process or {},
            "simulation_mode": self.simulation_mode,
            "signals": resolved_signals,
        }

        if score.level in {
            ThreatLevel.WARNING,
            ThreatLevel.CRITICAL,
        }:
            alert_id = self.database.record_alert(
                severity=score.level.value,
                message=f"RDRS threat score: {score.total}/100",
                evidence=evidence,
            )

        if score.level == ThreatLevel.CRITICAL:
            incident_id = self.database.record_incident(
                severity=score.level.value,
                description=(
                    reason
                    or "Critical ransomware threat detected"
                ),
                evidence=evidence,
                threat_score=float(score.total),
                threat_level=score.level.value,
                suspect_process=suspect_process or {},
                affected_files=affected,
                score_id=score_id,
                alert_id=alert_id,
            )

            # Evidence collection must never abort incident response. A file
            # that has already been renamed (".txt" -> ".locked") or removed by
            # the burst is *expected* ransomware behaviour, not a fatal error,
            # so unreadable entries are recorded as skipped and the incident
            # still stands.
            skipped_files: list[dict[str, str]] = []

            for file_path in affected:
                try:
                    result = self.quarantine.quarantine(file_path)
                except (FileNotFoundError, ValueError) as exc:
                    skipped_files.append(
                        {
                            "path": str(file_path),
                            "reason": str(exc),
                        }
                    )
                    continue
                except OSError as exc:
                    skipped_files.append(
                        {
                            "path": str(file_path),
                            "reason": f"OS error: {exc}",
                        }
                    )
                    continue

                if result.copied:
                    quarantined_files.append(
                        result.destination
                    )

            self.audit.record(
                actor="RDRS",
                action="critical_threat_response",
                result="incident_created_and_evidence_processed",
                success=True,
                details={
                    "score": score.total,
                    "level": score.level.value,
                    "incident_id": incident_id,
                    "alert_id": alert_id,
                    "affected_files": affected,
                    "quarantined_files": quarantined_files,
                    "skipped_files": skipped_files,
                    "simulation_mode": self.simulation_mode,
                },
            )

        elif score.level == ThreatLevel.WARNING:
            self.audit.record(
                actor="RDRS",
                action="warning_alert_created",
                result="alert_created",
                success=True,
                details={
                    "score": score.total,
                    "alert_id": alert_id,
                    "affected_files": affected,
                },
            )

        else:
            self.audit.record(
                actor="RDRS",
                action="normal_score_recorded",
                result="score_recorded",
                success=True,
                details={
                    "score": score.total,
                    "affected_files": affected,
                },
            )

        return ResponseResult(
            score_id=score_id,
            alert_id=alert_id,
            incident_id=incident_id,
            quarantined_files=tuple(quarantined_files),
            level=score.level,
        )

    @staticmethod
    def _normalize_files(
        files: list[str],
    ) -> list[str]:
        seen: set[str] = set()
        normalized: list[str] = []

        for value in files:
            path = str(value)

            if not path or path in seen:
                continue

            seen.add(path)
            normalized.append(path)

        return sorted(normalized)

