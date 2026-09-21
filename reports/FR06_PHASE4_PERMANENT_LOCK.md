# FR-06 + PHASE 4 — PERMANENT LOCK RECORD

**Items:** FR-06 (sliding time window) · Build Guide Phase 4 (Detection Engine)
**Date:** 2026-09-18
**Suite at lock time:** 643 passed, 0 failed · front end 32/32 · E2E 30/30 · encoding+safety 22/22
**Config integrity:** hash unchanged by a full suite run

---

## 1. Scope

FR-06 covers analysing events in a sliding time window. Phase 4 covers turning a
stream of single events into signals over that window and comparing them against
the configured thresholds.

## 2. Exact requirements (verbatim)

- **FSD FR-6:** "Analyze events in a sliding time window."
- **Phase 4 Goal:** "Turn a stream of single events into meaningful signals over a time window."
- **Task 1:** "Keep a **sliding window** of recent events (for example, the last 60 seconds)."
- **Task 2:** "Whenever a new event arrives, recompute: **files modified per minute**, **number of renames**, **number of extension changes** (such as .docx becoming .locked), and the **average entropy of newly written files**."
- **Task 3:** "Compare these against the **thresholds in config.yaml** to decide when a dangerous 'burst' is happening."
- **Done when:** "A rapid burst of file changes pushes these counters above their thresholds, while **normal activity stays below them**."

## 3. Every inspected file

`src/rdrs/burst_detection.py` · `src/rdrs/runtime.py` (engine construction) ·
`config.yaml` (thresholds) · `tests/unit/test_detection_window.py` (new)

## 4. Requirement → code mapping

| Requirement | Production code | Evidence |
|---|---|---|
| sliding window | `BurstDetectionEngine._purge_expired` — `cutoff = now - window_seconds` | expired events stop being counted |
| recompute on every event | `_build_analysis` called from `ingest`/`update_process` | all four values refresh per event |
| files modified per minute | `modified_rate` from `_unique_modified_files` | burst 60 files → rate ≥ 20/min |
| number of renames | `rename_count = len(rename_events)` | 12 renames → `rename_count == 12` |
| extension changes | `extension_change_count` via `_extension_changed` | `.docx → .locked` counted; same-extension rename not counted |
| average entropy | `average_entropy` from modified events' metadata | 4 files at 8.0 → average 8.0 |
| thresholds from config.yaml | `runtime` passes `self._config.detection_thresholds` into `BurstDetectionEngine(thresholds=…)` | engine thresholds equal the shipped `detection:` block |
| Done-when | burst vs quiet comparison | burst `detected=True` with all four counters over; quiet `detected=False` |

## 5. Locked Rules

§3 · §4 (counters derive from real events, never fabricated) · §5 real
verification · §13 real-implementation tests.

## 6. Defects found

**None.** The engine already implemented all four counters, the sliding purge and
config-sourced thresholds correctly. This gate was verification, not repair.

Two behaviours were checked and confirmed **correct**:

- `_is_modify` accepts `modify/modified/write/written`. `created` is not in the
  set, but a file written through the normal API emits **both** `created` and
  `modified`, so a newly written file is counted once via its `modified` event.
  `_unique_modified_files` de-duplicates by path, so the pair cannot
  double-count.
- The window clock is the event-stream clock (`_window_now`), not the wall
  clock, which is why the sliding expiry is testable deterministically.

## 7. Tests and real runtime verification

| Layer | Result |
|---|---|
| `test_detection_window.py` (new, 9 tests) | **9 passed** |
| Full suite | **643 passed, 0 failed** |
| Front end | **32 passed** |
| End-to-end | **ALL 30 CHECKS PASSED** |
| Encoding + safety | **ALL 22 CHECKS PASSED** |
| Config integrity | unchanged |

The new suite covers all four counters, sliding-window expiry, extension-change
detection, config-sourced thresholds (a stricter config changes the verdict,
proving the values are used and not hard-coded), and the Phase 4 Done-when.

## 8. No fixable defect remains

Every Phase 4 task and the Done-when condition verified against real behaviour.

## 9. Score

**FR-06: 100/100 · Phase 4: 100/100**

## 10. Status

**FROZEN: YES · LOCKED: YES · 100/100: YES · REWRITE REQUIRED: NO · FUTURE REWRITE REQUIRED: NO**

**Complete-file locked:** none claimed for this FR.

`src/rdrs/burst_detection.py` serves **FR-06 + FR-07 + FR-08** (window, scoring,
signals). FR-07 and FR-08 are verified next; the file becomes eligible for a
complete-file lock only after those gates pass, in case they surface a defect in
it. Claiming it now would be a regional lock.
