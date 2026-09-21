# FR-12 + PHASE 8 — PERMANENT LOCK RECORD

**Items:** FR-12 (JSON/CSV reports) · Build Guide Phase 8 (Reporting)
**Date:** 2026-09-18
**Suite at lock time:** 690 passed, 0 failed · front end 41/41 · E2E 30/30 · encoding+safety 22/22
**Config integrity:** hash unchanged by a full suite run

---

## 1. Scope

FR-12 covers generating incident reports. Phase 8 covers the JSON and CSV
formats (required), the report contents, and the Done-when.

## 2. Exact requirements (verbatim)

- **FSD FR-12:** "Generate JSON/CSV reports."
- **Phase 8 Goal:** "Produce a clear report of an incident that someone could read or file."
- **Task 1:** "Generate an incident report in **JSON and CSV (required)**. Add HTML and PDF (using reportlab) **if you have time**."
- **Task 2:** "Include: **a timeline of events, the threat score, the list of affected files, the suspect process, and recommendations**."
- **Done when:** "After an incident you can generate at least a **JSON report and a CSV report file**."

## 3. Every inspected file

`src/rdrs/api.py` (`build_incident_report`, `write_json_report`,
`write_csv_report`, `generate_report`, `download_report`, `reports`,
`report_directory`) · `src/rdrs/storage.py` (`list_events_for_incident`,
`attach_events_to_incident_by_paths`) · `src/rdrs/runtime.py` (attachment) ·
`tests/unit/test_reports_phase8.py` (new)

## 4. Requirement → code mapping

| Requirement | Production code | Evidence |
|---|---|---|
| JSON report (required) | `write_json_report` via `POST /reports/generate?format=json` | 200; `incident_1_…json` written |
| CSV report (required) | `write_csv_report` via `format=csv` | 200; `incident_1_…csv`, 82 rows |
| timeline of events | `build_incident_report` → `list_events_for_incident` | **20 events** |
| threat score | `_resolve_score` | **80.0** / CRITICAL |
| affected files | dedicated column, then evidence fallback | **52 files** |
| suspect process | `_resolve` from the incident's suspect payload | **`python.exe`** |
| recommendations | `recommendations` | **8 entries** |
| report listing | `GET /reports` | count == len(incidents) |
| report download | `GET /reports/{incident_id}/{format}` | JSON 200 `application/json`; CSV 200 `text/csv` |
| HTML/PDF | optional per the guide | unsupported format is refused (400/422), never faked |

**Key CSV columns:** `record_type, timestamp, incident_id, action, path, score,
level, details`.

## 5. Locked Rules

§3 · §4 (reports built from stored data; no fabricated sections) · §5 real
verification · §12 (reports from real stored data) · §13 real tests.

## 6. Defects found

**None in production code.** Both required formats and all five contents were
already implemented correctly.

**Three findings were my own probe's errors**, corrected rather than "fixed" in
the product:

1. The download route is `GET /reports/{incident_id}/{format}` (path
   parameters), not `?path=` — my 404 was a wrong URL.
2. A synthetic burst fed through `runtime._handle_file_event` does **not**
   persist events — persistence is the file monitor's job (it is constructed
   with `database=`). Without real files the incident had nothing to attach, so
   the timeline was legitimately empty. The test now writes real files.
3. An incident has no suspect process if the burst runs before the process
   monitor's first capture; the test now waits for process rows.

## 7. Tests and real runtime verification

| Layer | Result |
|---|---|
| `test_reports_phase8.py` (new, 9 tests) | **9 passed** |
| Full suite | **690 passed, 0 failed** |
| Front end | **41 passed** |
| End-to-end | **ALL 30 CHECKS PASSED** |
| Encoding + safety | **ALL 22 CHECKS PASSED** |
| Config integrity | unchanged |

**Live probe** (`ptmp/verify_phase8_reports.py`): JSON + CSV generated for a real
incident; the JSON carried timeline 20, score 80.0, 52 affected files,
`python.exe`, 8 recommendations; both downloads returned 200 with the right
media type.

## 8. No fixable defect remains

Every Phase 8 task and the Done-when condition verified against real behaviour.

## 9. Score

**FR-12: 100/100 · Phase 8: 100/100**

## 10. Status

**FROZEN: YES · LOCKED: YES · 100/100: YES · REWRITE REQUIRED: NO · FUTURE REWRITE REQUIRED: NO**

**Complete-file locked:** `src/rdrs/api.py` and `src/rdrs/dashboard_data.py` —
FR-12 was the last FR adding to them. Every route, projection and report path is
now verified, so the complete-file lock is valid (no regional lock).

`src/rdrs/runtime.py` remains the only unclaimed file; FR-13 (testing) does not
add to it, so it is locked in the FR-13 record.
