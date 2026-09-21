# FR-07 + FR-08 + PHASE 5 — PERMANENT LOCK RECORD

**Items:** FR-07 (weighted threat score) · FR-08 (alerts and incident records) · Build Guide Phase 5 (Threat Scoring & Incident Response)
**Date:** 2026-09-18
**Suite at lock time:** 661 passed, 0 failed · front end 32/32 · E2E 30/30 · encoding+safety 22/22
**Config integrity:** hash unchanged by a full suite run

---

## 1. Scope

FR-07 covers the transparent 0–100 weighted score. FR-08 covers alert and
incident records. Phase 5 covers the scoring rules, the level bands, the
Critical response (incident + affected files + suspect process + alert/audit
logs), copy-only quarantine and simulation mode.

FR-09 (quarantine) is Phase 5's fourth task and was locked separately in
`FR09` — see the FR-09 work recorded earlier in the run.

## 2. Exact requirements (verbatim)

- **FSD FR-7:** "Compute weighted threat score (0-100)."
- **FSD FR-8:** "Generate alerts and incident records."
- **Phase 5 Goal:** "Combine the signals into a single 0–100 score using simple, transparent rules — then respond. (No datasets or training needed; the scoring is plain weighted rules you control.)"
- **Task 1:** "Give each signal a weight (all configurable): rapid encryption 40, mass rename 30, high entropy 25, CPU spike 15, unknown program 10. Add the weights of the signals that fired and **cap the total at 100**."
- **Task 2:** "Map the score to a level: **Normal (0–40), Warning (40–70), Critical (70–100)**."
- **Task 3:** "On Critical: create an **incident record**, save the **list of affected files** and the **suspect process**, and write to the **alert and audit logs**."
- **Task 4:** "Copy the evidence files into a **quarantine folder** — copy only, never change or delete the originals."
- **Task 5:** "Keep **simulation mode ON by default**: if process termination is switched on, only LOG what it would do; do not actually kill anything."
- **Done when:** "A simulated burst produces a **Critical score**, an **incident record**, an **alert-log entry**, and **copied evidence** — with the **original files untouched**."

## 3. Every inspected file

`src/rdrs/scoring.py` · `src/rdrs/burst_detection.py` (signal weights) ·
`src/rdrs/response.py` · `src/rdrs/quarantine.py` · `src/rdrs/storage.py` ·
`src/rdrs/logging.py` (alert/audit logs) · `src/rdrs/runtime.py` (response wiring) ·
`config.yaml` (weights) · `tests/unit/test_scoring_incidents.py` (new)

## 4. Requirement → code mapping

| Requirement | Production code | Evidence |
|---|---|---|
| FR-7 weighted score | `scoring.ThreatScore.total` | all five signals → 100 |
| weights 40/30/25/15/10, configurable | `config.yaml` `scoring.weights`; `ThreatScoringEngine.DEFAULT_WEIGHTS` | shipped weights equal the guide exactly |
| cap at 100 | `total = min(100, sum)` | uncapped sum 120 → 100 |
| FR-7 level bands | `ThreatScore.level` (`>=70` CRITICAL, `>=40` WARNING, else NORMAL) | 39→NORMAL, 40→WARNING, 69→WARNING, 70→CRITICAL, 100→CRITICAL |
| FR-8 alert record | `DatabaseManager.record_alert` → `AlertRecord` | rows written |
| FR-8 incident record | `record_incident` → `IncidentRecord` | one incident per burst |
| Task 3 affected files | `IncidentRecord.affected_files_json` | **74** files stored in the live burst |
| Task 3 suspect process | `IncidentRecord.suspect_process_json` | suspect recorded |
| Task 3 alert log | `logging` → `alerts.log` | non-empty after a Critical |
| Task 3 audit log | `logging` → `audit.log` | non-empty after a Critical |
| Task 4 quarantine | `quarantine.QuarantineManager` | 4 copies; originals byte-identical |
| Task 5 simulation mode | `IncidentResponseEngine(simulation_mode=True)` default | `simulation_mode is True` |
| Task 5 no termination | no `.kill(`/`.terminate(`/`os.kill(`/`taskkill`/`TerminateProcess` in `src/rdrs` | asserted by a test |
| Done-when | live burst through the runtime | score **100 / CRITICAL**, 1 incident, alert-log ✓, audit-log ✓, 4 copies, **originals untouched ✓** |

## 5. Locked Rules

§3 · §4 (scores computed, never hard-coded) · §5 real verification ·
§9 safety (simulation on; no process termination; originals preserved) ·
§12 (reports/incidents from real stored data) · §13 real tests.

## 6. Defects found

**None in production code.** All Phase 5 requirements were already implemented
correctly; this gate was verification.

Two of my own **test** assumptions were wrong and were corrected (not the
product): `ThreatScore` has no `from_total()` constructor (the level bands are
now tested by building exact totals from the guide's weights), and
`ResponseResult` exposes `.level`/`.incident_id`, not `.score`.

## 7. Tests and real runtime verification

| Layer | Result |
|---|---|
| `test_scoring_incidents.py` (new, 18 tests) | **18 passed** |
| Full suite | **661 passed, 0 failed** |
| Front end | **32 passed** |
| End-to-end | **ALL 30 CHECKS PASSED** |
| Encoding + safety | **ALL 22 CHECKS PASSED** |
| Config integrity | unchanged |

**Live probes:** `ptmp/verify_phase5_scoring.py` reproduced the Phase 5
Done-when end-to-end through the real runtime.

## 8. No fixable defect remains

Every Phase 5 task and the Done-when condition verified against real behaviour.

## 9. Score

**FR-07: 100/100 · FR-08: 100/100 · Phase 5: 100/100**

## 10. Status

**FROZEN: YES · LOCKED: YES · 100/100: YES · REWRITE REQUIRED: NO · FUTURE REWRITE REQUIRED: NO**

**Complete-file locked:** `src/rdrs/scoring.py`, `src/rdrs/response.py`,
`src/rdrs/quarantine.py`, and now **`src/rdrs/burst_detection.py`** — its
documented responsibility is FR-06 + FR-07 + FR-08, all three now verified, and
no later FR adds to it.
