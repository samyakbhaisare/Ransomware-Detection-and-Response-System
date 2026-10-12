from __future__ import annotations

import math
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from time import time
from typing import Any, Callable, Deque, Mapping, Sequence


DEFAULT_THRESHOLDS = {
    "window_seconds": 60.0,
    "modified_files_per_minute": 20.0,
    "renames": 10.0,
    "extension_changes": 5.0,
    "average_entropy": 7.0,
    "cpu_percent": 80.0,
}

DEFAULT_SCORING_WEIGHTS = {
    "rapid_encryption": 40.0,
    "mass_rename": 30.0,
    "high_entropy": 25.0,
    "cpu_spike": 15.0,
    "unknown_program": 10.0,
}


def _default_clock() -> float:
    return time()


def _as_timestamp(value: Any) -> float:
    if isinstance(value, Mapping):
        value = value.get("timestamp")

    if value is None:
        return _default_clock()

    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.timestamp()

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)

    if isinstance(value, str):
        text = value.strip()

        try:
            return float(text)
        except ValueError:
            pass

        if text.endswith("Z"):
            text = text[:-1] + "+00:00"

        parsed = datetime.fromisoformat(text)

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return parsed.timestamp()

    raise TypeError(
        f"Unsupported timestamp type: {type(value).__name__}"
    )


def _action(event: Mapping[str, Any]) -> str:
    return str(event.get("action", "")).strip().lower()


def _path_extension(path: Any) -> str:
    if not path:
        return ""
    return Path(str(path)).suffix.lower()


def _is_modify(event: Mapping[str, Any]) -> bool:
    return _action(event) in {
        "modify",
        "modified",
        "write",
        "written",
    }


def _is_rename(event: Mapping[str, Any]) -> bool:
    return _action(event) in {
        "rename",
        "renamed",
        "move",
        "moved",
    }


def _extension_changed(event: Mapping[str, Any]) -> bool:
    if not _is_rename(event):
        return False

    old_path = event.get("previous_path")
    new_path = event.get("new_path")

    if old_path is not None and new_path is not None:
        return _path_extension(old_path) != _path_extension(new_path)

    old_ext = event.get("old_extension")
    new_ext = event.get("new_extension")

    if old_ext is not None and new_ext is not None:
        return str(old_ext).lower() != str(new_ext).lower()

    return False


def _entropy(event: Mapping[str, Any]) -> float | None:
    metadata = event.get("metadata")

    if not isinstance(metadata, Mapping):
        return None

    value = metadata.get("entropy")

    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        value = float(value)

        if 0.0 <= value <= 8.0:
            return value

    return None


def _copy_event(event: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(event)

    metadata = result.get("metadata")

    if isinstance(metadata, Mapping):
        result["metadata"] = dict(metadata)

    return result


def _process_has_identity(process: Mapping[str, Any] | None) -> bool:
    if not process:
        return False

    name = str(process.get("name") or "").strip()
    executable = str(
        process.get("executable_path")
        or process.get("exe")
        or ""
    ).strip()

    return bool(name or executable)


@dataclass(frozen=True)
class BurstMetrics:
    modified_files_per_minute: float
    rename_count: int
    extension_change_count: int
    average_entropy: float
    event_count: int
    cpu_percent: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ThreatScoreBreakdown:
    rapid_encryption: float = 0.0
    mass_rename: float = 0.0
    high_entropy: float = 0.0
    cpu_spike: float = 0.0
    unknown_program: float = 0.0

    @property
    def total(self) -> float:
        return min(
            100.0,
            max(
                0.0,
                self.rapid_encryption
                + self.mass_rename
                + self.high_entropy
                + self.cpu_spike
                + self.unknown_program,
            ),
        )

    @property
    def score(self) -> float:
        return self.total

    def to_dict(self) -> dict[str, float]:
        result = asdict(self)
        result["total"] = self.total
        return result


@dataclass(frozen=True)
class BurstAnalysis:
    metrics: BurstMetrics
    score: ThreatScoreBreakdown
    detected: bool
    level: str
    signals: tuple[str, ...]
    affected_files: tuple[str, ...]
    events: tuple[dict[str, Any], ...]
    process: dict[str, Any] | None
    timestamp: float
    window_seconds: float

    @property
    def dangerous_burst(self) -> bool:
        return self.detected and self.score.total >= 40.0


    @property
    def rapid_encryption(self) -> bool:
        return "rapid_encryption" in self.signals

    @property
    def mass_rename(self) -> bool:
        return "mass_rename" in self.signals

    @property
    def high_entropy(self) -> bool:
        return "high_entropy" in self.signals

    @property
    def cpu_spike(self) -> bool:
        return "cpu_spike" in self.signals

    @property
    def unknown_program(self) -> bool:
        return "unknown_program" in self.signals

    @property
    def threat_score(self) -> float:
        return self.score.total

    @property
    def suspect_process(self) -> dict[str, Any] | None:
        return self.process

    @property
    def event_count(self) -> int:
        return self.metrics.event_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "detected": self.detected,
            "level": self.level,
            "timestamp": self.timestamp,
            "window_seconds": self.window_seconds,
            "metrics": self.metrics.to_dict(),
            "score": self.score.to_dict(),
            "threat_score": self.threat_score,
            "signals": list(self.signals),
            "rapid_encryption": self.rapid_encryption,
            "mass_rename": self.mass_rename,
            "high_entropy": self.high_entropy,
            "cpu_spike": self.cpu_spike,
            "unknown_program": self.unknown_program,
            "affected_files": list(self.affected_files),
            "suspect_process": self.suspect_process,
            "process": self.process,
            "events": list(self.events),
        }

    def dict(self) -> dict[str, Any]:
        return self.to_dict()


class BurstDetectionEngine:
    """
    RDRS sliding-window detection engine.

    The engine consumes real file-monitor events and optional process
    telemetry supplied by ProcessMonitor.

    Phase 4:
        - sliding event window
        - modification rate
        - rename count
        - extension-change count
        - average entropy
        - configurable thresholds

    Phase 5 compatibility:
        - transparent signal weights
        - score capped at 100
        - Normal / Warning / Critical mapping
        - affected-file and process evidence propagation

    Process collection itself remains the responsibility of ProcessMonitor.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] | None = None,
        thresholds: Mapping[str, float] | None = None,
        scoring_weights: Mapping[str, float] | None = None,
    ) -> None:
        self._clock = clock or _default_clock
        self.thresholds = self._validate_thresholds(thresholds)
        self.scoring_weights = self._validate_weights(scoring_weights)

        self._events: Deque[dict[str, Any]] = deque()
        self._process: dict[str, Any] | None = None
        self._lock = RLock()

        # The sliding window is measured against the *event stream*, never
        # against the sampling clock. See ``_window_now`` for why.
        self._window_anchor: float | None = None

        self._last_analysis = self._build_analysis(
            self._window_now()
        )

    def _window_now(self) -> float:
        """Return the window clock without advancing it.

        Used when recomputing an analysis that is not itself a time
        observation (e.g. a process-telemetry refresh), so the window is
        neither advanced nor allowed to expire anything spuriously.
        """
        if self._window_anchor is not None:
            return self._window_anchor

        return self._clock()

    def _advance_window(self, candidate: float) -> float:
        """Advance the window clock monotonically and return it.

        The clock only ever moves forward, so a late-arriving stale event can
        never shrink the window.
        """
        if (
            self._window_anchor is None
            or candidate > self._window_anchor
        ):
            self._window_anchor = candidate

        return self._window_anchor

    def _observe_now(self) -> float:
        """Advance the window to the sampling clock ("assess as of now").

        This is used by :meth:`evaluate`, which is an explicit request to
        re-assess the current instant. Honouring the sampling clock here is
        what lets an idle window expire on real elapsed time.
        """
        return self._advance_window(self._clock())

    def _ingest_now(self, event_timestamp: float) -> float:
        """Return the window clock for an event that is being ingested.

        The window is anchored on the *event stream* here, never on the
        sampling clock. Anchoring on ``time.time()`` silently discarded every
        event whose timestamp lagged the sampling clock by more than
        ``window_seconds``: the event was appended and then immediately purged
        by :meth:`_purge_expired` in the same operation.

        That is exactly what a historical replay does -- the Build Guide
        section 15 burst persisted in the database carries timestamps from
        when it ran, and re-feeding them through the detector must reproduce
        the detection. It is also what a live burst can do, because the
        watchdog queue delivers events whose timestamp was captured before the
        analysis runs, so the lag grows exactly when detection matters most.

        A real 232-event burst that completed in 5.74 seconds scored 0.0
        because every one of its events was purged on arrival.
        """
        return self._advance_window(event_timestamp)

    @staticmethod
    def _validate_thresholds(
        supplied: Mapping[str, float] | None,
    ) -> dict[str, float]:
        values = dict(DEFAULT_THRESHOLDS)

        if supplied:
            values.update(supplied)

        result: dict[str, float] = {}

        for key in DEFAULT_THRESHOLDS:
            if key not in values:
                raise ValueError(
                    f"Missing detection threshold: {key}"
                )

            try:
                number = float(values[key])
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Invalid detection threshold: {key}"
                ) from exc

            if not math.isfinite(number):
                raise ValueError(
                    f"Detection threshold must be finite: {key}"
                )

            if number < 0:
                raise ValueError(
                    f"Detection threshold cannot be negative: {key}"
                )

            result[key] = number

        if result["window_seconds"] <= 0:
            raise ValueError(
                "window_seconds must be greater than zero"
            )

        return result

    @staticmethod
    def _validate_weights(
        supplied: Mapping[str, float] | None,
    ) -> dict[str, float]:
        values = dict(DEFAULT_SCORING_WEIGHTS)

        if supplied:
            values.update(supplied)

        result: dict[str, float] = {}

        for key in DEFAULT_SCORING_WEIGHTS:
            if key not in values:
                raise ValueError(
                    f"Missing scoring weight: {key}"
                )

            try:
                number = float(values[key])
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Invalid scoring weight: {key}"
                ) from exc

            if not math.isfinite(number):
                raise ValueError(
                    f"Scoring weight must be finite: {key}"
                )

            if number < 0:
                raise ValueError(
                    f"Scoring weight cannot be negative: {key}"
                )

            result[key] = number

        return result

    def _purge_expired(self, now: float) -> None:
        """Drop events that have fallen out of the sliding window.

        ``now`` is always the event-stream clock from :meth:`_window_now`,
        never the raw wall clock.
        """
        cutoff = now - self.thresholds["window_seconds"]

        # Fast path: events normally arrive in timestamp order, so expired
        # entries sit at the left edge.
        while self._events:
            event_timestamp = _as_timestamp(
                self._events[0].get("timestamp")
            )

            if event_timestamp < cutoff:
                self._events.popleft()
            else:
                break

        # A late-arriving stale event lands at the right edge, behind live
        # entries, so the left-edge loop alone would retain it forever and let
        # it pollute the counters. Sweep it out.
        if any(
            _as_timestamp(event.get("timestamp")) < cutoff
            for event in self._events
        ):
            kept = [
                event
                for event in self._events
                if _as_timestamp(event.get("timestamp")) >= cutoff
            ]

            self._events.clear()
            self._events.extend(kept)

    def _unique_modified_files(
        self,
        events: list[dict[str, Any]],
    ) -> set[str]:
        paths: set[str] = set()

        for event in events:
            if not _is_modify(event):
                continue

            path = event.get("path")

            if path:
                paths.add(str(path))

        return paths

    def _affected_files(
        self,
        events: list[dict[str, Any]],
    ) -> tuple[str, ...]:
        result: list[str] = []

        for event in events:
            metadata = event.get("metadata")

            path = event.get("path")
            new_path = event.get("new_path")

            if path:
                include = True

                if isinstance(metadata, Mapping):
                    exists = metadata.get("exists")
                    is_file = metadata.get("is_file")

                    if exists is False or is_file is False:
                        include = False

                if include:
                    path_string = str(path)

                    if path_string not in result:
                        result.append(path_string)

            if new_path:
                new_path_string = str(new_path)

                if new_path_string not in result:
                    result.append(new_path_string)

        return tuple(result)

    @staticmethod
    def _is_burst_participant(
        event: Mapping[str, Any],
        signals: Sequence[str],
    ) -> bool:
        """Return whether an event took an action that drove a fired signal.

        Signals are derived from specific actions - ``rapid_encryption`` and
        ``high_entropy`` from modifications, ``mass_rename`` and
        ``extension_change`` from renames. When a burst has fired, a file that
        merely had its metadata touched inside the same sliding window (a log
        line, an unrelated note) did not participate in the burst and must not
        be reported as affected or quarantined as evidence.
        """
        signal_names = set(signals)

        if _is_rename(event):
            return bool(
                signal_names
                & {"mass_rename", "extension_change"}
            )

        if _is_modify(event):
            return bool(
                signal_names
                & {"rapid_encryption", "high_entropy"}
            )

        return False

    def _burst_affected_files(
        self,
        events: list[dict[str, Any]],
        signals: Sequence[str],
    ) -> tuple[str, ...]:
        """Affected files restricted to genuine burst participants.

        When no signal fired the analysis is informational, so every touched
        file is reported (the historical contract relied on by callers and
        tests). Once signals fire, the set narrows to the files that actually
        drove those signals, which is what makes incident evidence and
        quarantine precise instead of absorbing incidental window activity.
        """
        if not signals:
            return self._affected_files(events)

        participants = [
            event
            for event in events
            if self._is_burst_participant(
                event,
                signals,
            )
        ]

        reported = self._affected_files(participants)

        # A burst must never report an empty evidence set when files were
        # genuinely touched; fall back to the full window in that case.
        if reported:
            return reported

        return self._affected_files(events)

    def _build_analysis(self, now: float) -> BurstAnalysis:
        self._purge_expired(now)

        events = list(self._events)

        modified_events = [
            event
            for event in events
            if _is_modify(event)
        ]

        modified_files = self._unique_modified_files(events)

        rename_events = [
            event
            for event in events
            if _is_rename(event)
        ]

        extension_events = [
            event
            for event in rename_events
            if _extension_changed(event)
        ]

        entropy_values = [
            value
            for event in modified_events
            if (value := _entropy(event)) is not None
        ]

        window_seconds = self.thresholds["window_seconds"]

        modified_rate = (
            len(modified_files)
            * 60.0
            / window_seconds
        )

        average_entropy = (
            sum(entropy_values)
            / len(entropy_values)
            if entropy_values
            else 0.0
        )

        signals: list[str] = []

        if (
            modified_rate
            >= self.thresholds["modified_files_per_minute"]
        ):
            signals.append("rapid_encryption")

        if len(rename_events) >= self.thresholds["renames"]:
            signals.append("mass_rename")

        if (
            len(extension_events)
            >= self.thresholds["extension_changes"]
        ):
            signals.append("extension_change")

        if (
            entropy_values
            and average_entropy
            >= self.thresholds["average_entropy"]
        ):
            signals.append("high_entropy")

        process = (
            dict(self._process)
            if self._process is not None
            else None
        )

        if process is not None:
            cpu = process.get("cpu_percent")

            if isinstance(cpu, (int, float)) and not isinstance(
                cpu, bool
            ):
                if float(cpu) >= self.thresholds["cpu_percent"]:
                    signals.append("cpu_spike")

            if not _process_has_identity(process):
                signals.append("unknown_program")

        score = ThreatScoreBreakdown(
            rapid_encryption=(
                self.scoring_weights["rapid_encryption"]
                if "rapid_encryption" in signals
                else 0.0
            ),
            mass_rename=(
                self.scoring_weights["mass_rename"]
                if "mass_rename" in signals
                else 0.0
            ),
            high_entropy=(
                self.scoring_weights["high_entropy"]
                if "high_entropy" in signals
                else 0.0
            ),
            cpu_spike=(
                self.scoring_weights["cpu_spike"]
                if "cpu_spike" in signals
                else 0.0
            ),
            unknown_program=(
                self.scoring_weights["unknown_program"]
                if "unknown_program" in signals
                else 0.0
            ),
        )

        total = score.total

        if total >= 70.0:
            level = "CRITICAL"
        elif total >= 40.0:
            level = "WARNING"
        else:
            level = "NORMAL"

        metrics = BurstMetrics(
            cpu_percent=(
                float(process.get("cpu_percent", 0.0))
                if isinstance(process, Mapping)
                and isinstance(process.get("cpu_percent"), (int, float))
                else 0.0
            ),
            modified_files_per_minute=modified_rate,
            rename_count=len(rename_events),
            extension_change_count=len(extension_events),
            average_entropy=average_entropy,
            event_count=len(events),
        )

        return BurstAnalysis(
            metrics=metrics,
            score=score,
            detected=bool(signals),
            level=level,
            signals=tuple(signals),
            affected_files=self._burst_affected_files(
                events,
                signals,
            ),
            events=tuple(
                _copy_event(event)
                for event in events
            ),
            process=process,
            timestamp=now,
            window_seconds=window_seconds,
        )

    def add_event(
        self,
        event: Mapping[str, Any],
    ) -> BurstAnalysis:
        if not isinstance(event, Mapping):
            raise TypeError("event must be a mapping")

        copied = _copy_event(event)

        if copied.get("timestamp") is None:
            copied["timestamp"] = self._clock()

        _as_timestamp(copied["timestamp"])

        with self._lock:
            self._events.append(copied)

            # Anchor the window on the event's own timestamp so a lagging
            # event is never purged in the same operation that ingests it.
            now = self._ingest_now(
                _as_timestamp(copied["timestamp"])
            )

            self._last_analysis = self._build_analysis(now)

            return self._last_analysis

    def ingest(
        self,
        event: Mapping[str, Any],
    ) -> BurstAnalysis:
        return self.add_event(event)

    def process_event(
        self,
        event: Mapping[str, Any],
    ) -> BurstAnalysis:
        return self.add_event(event)

    def evaluate(self) -> BurstAnalysis:
        with self._lock:
            self._last_analysis = self._build_analysis(
                self._observe_now()
            )

            return self._last_analysis

    def analyze(self) -> BurstAnalysis:
        return self.evaluate()

    def analyse(self) -> BurstAnalysis:
        return self.evaluate()

    def update_process(
        self,
        process: Mapping[str, Any] | None,
    ) -> BurstAnalysis:
        if process is not None and not isinstance(
            process,
            Mapping,
        ):
            raise TypeError(
                "process must be a mapping or None"
            )

        with self._lock:
            self._process = (
                dict(process)
                if process is not None
                else None
            )

            self._last_analysis = self._build_analysis(
                self._window_now()
            )

            return self._last_analysis

    def set_process(
        self,
        process: Mapping[str, Any] | None,
    ) -> BurstAnalysis:
        return self.update_process(process)

    def reset(self) -> None:
        with self._lock:
            self._events.clear()
            self._process = None
            self._window_anchor = None
            self._last_analysis = self._build_analysis(
                self._window_now()
            )

    @property
    def window_started_at(self) -> datetime | None:
        """Earliest event timestamp currently inside the sliding window.

        Used to attribute the events that produced a burst to the incident
        created from that burst, so Phase 8 report timelines are genuinely
        incident-scoped. Returns ``None`` when the window is empty.
        """
        with self._lock:
            if not self._events:
                return None

            earliest = min(
                _as_timestamp(
                    event.get("timestamp")
                )
                for event in self._events
            )

        return datetime.fromtimestamp(
            earliest,
            tz=timezone.utc,
        )


    def clear(self) -> None:
        self.reset()

    @property
    def window_size(self) -> int:
        with self._lock:
            self._purge_expired(self._window_now())
            return len(self._events)

    @property
    def event_count(self) -> int:
        return self.window_size

    @property
    def latest_process(self) -> dict[str, Any] | None:
        return self.process

    @property
    def latest_cpu_percent(self) -> float:
        process = self.process
        if isinstance(process, Mapping):
            value = process.get("cpu_percent", 0.0)
            if isinstance(value, (int, float)):
                return float(value)
        return 0.0

    @property
    def process(self) -> dict[str, Any] | None:
        with self._lock:
            return (
                dict(self._process)
                if self._process is not None
                else None
            )

    @property
    def last_analysis(self) -> BurstAnalysis:
        with self._lock:
            return self._last_analysis

    @property
    def events(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(
                _copy_event(event)
                for event in self._events
            )

    def update_thresholds(
        self,
        thresholds: Mapping[str, float],
    ) -> BurstAnalysis:
        new_thresholds = self._validate_thresholds(
            thresholds
        )

        with self._lock:
            self.thresholds = new_thresholds
            self._last_analysis = self._build_analysis(
                self._clock()
            )

            return self._last_analysis

    def update_scoring_weights(
        self,
        scoring_weights: Mapping[str, float],
    ) -> BurstAnalysis:
        new_weights = self._validate_weights(
            scoring_weights
        )

        with self._lock:
            self.scoring_weights = new_weights
            self._last_analysis = self._build_analysis(
                self._clock()
            )

            return self._last_analysis

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "thresholds": dict(self.thresholds),
                "scoring_weights": dict(
                    self.scoring_weights
                ),
                "window_size": len(self._events),
                "analysis": self._last_analysis.to_dict(),
                "process": (
                    dict(self._process)
                    if self._process is not None
                    else None
                ),
            }


__all__ = [
    "BurstAnalysis",
    "BurstDetectionEngine",
    "BurstMetrics",
    "ThreatScoreBreakdown",
    "DEFAULT_THRESHOLDS",
    "DEFAULT_SCORING_WEIGHTS",
]





