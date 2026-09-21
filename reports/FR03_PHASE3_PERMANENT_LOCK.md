# FR-03 + PHASE 3 — PERMANENT LOCK RECORD

**Items:** FR-03 (Shannon entropy) · Build Guide Phase 3 (Database & Process Monitoring)
**Date:** 2026-09-18
**Suite at lock time:** 634 passed, 0 failed · front end 32/32 · E2E 30/30 · encoding+safety 22/22
**Config integrity:** hash unchanged by a full suite run

---

## 1. Scope

FR-03 covers Shannon entropy over modified files. Phase 3 covers SQLAlchemy +
SQLite storage for the five tables, psutil process snapshots, saving both as
they happen, and graceful access-denied handling.

## 2. Exact requirements (verbatim)

- **FSD FR-3:** "Calculate Shannon entropy of modified files."
- **Phase 1 Task 3:** "Implement Shannon entropy: a function that takes bytes and returns a number from 0 (totally predictable) to 8 (totally random)."
- **Phase 1 Task 4:** "Add a helper that reads the first 64 KB of a file and returns its entropy (sampling keeps it fast on big files)."
- **Phase 1 Done-when:** "A normal text file returns a low entropy (about 0–5), and a file full of random bytes returns close to 8.0."
- **Phase 3 Goal:** "Store everything you observe, and find out which program is responsible."
- **Phase 3 Tasks:** SQLAlchemy+SQLite tables for events/processes/scores/alerts/incidents; psutil snapshots of name, CPU %, memory, disk writes, executable path, parent process; "Save file events and process snapshots into the database as they happen."; "Handle 'access denied' from psutil gracefully."
- **Phase 3 Done-when:** "After running the monitor, your database contains rows for file events and process snapshots."

## 3. Every inspected file

`src/rdrs/entropy.py` · `src/rdrs/monitoring.py` (metadata/entropy) ·
`src/rdrs/burst_detection.py` (average_entropy → high_entropy) ·
`src/rdrs/storage.py` · `src/rdrs/telemetry.py` · `src/rdrs/runtime.py` ·
`src/rdrs/fileaccess.py`

## 4. Requirement → code mapping

| Requirement | Production code | Evidence |
|---|---|---|
| FR-3 entropy of modified files | `monitoring.calculate_file_metadata` → `metadata["entropy"]` | a modified 64 KB random file → `entropy = 7.997` in the event metadata |
| entropy 0.0–8.0 | `entropy.calculate_entropy` | `b""`→0.0, single byte→0.0, uniform 256→8.0 |
| first 64 KB helper | `entropy.calculate_file_entropy`, `MAX_SAMPLE_SIZE` | `== 64*1024`; 5 MB file sampled in <1 s |
| Done-when | same | text **3.407** (0–5 ✓), random **7.997** (≥7.9 ✓) |
| entropy drives detection | `BurstAnalysis.metrics.average_entropy` → `high_entropy` at 7.0 | 7.95 → fires; 3.20 → stays off |
| five tables | `EventRecord`, `ProcessRecord`, `ScoreRecord`, `AlertRecord`, `IncidentRecord` | all present; rows written |
| psutil six fields | `telemetry.ProcessTelemetry` → `ProcessRecord` | real row: name, cpu_percent, memory_percent, disk_write_bytes, executable_path, parent_pid |
| save as they happen | `record_file_event`, `record_processes` | rows appear during a live run |
| access denied graceful | per-process/per-attribute guards | pid 0 / pid 4 captured; capture never aborts |
| Phase 3 Done-when | live monitoring session | **10 event rows + 351 process rows** |

## 5. Locked Rules

§3 · §4 (no fabricated entropy or process values) · §5 real verification ·
§9 safety (read-only inspection; never blocks a rename) · §13 real tests.

## 6. Defects found and fixed (FR-03)

| # | Defect | Fix |
|---|---|---|
| 1 | `calculate_file_entropy` used a plain `path.open("rb")` — no `FILE_SHARE_DELETE` on Windows, so holding the handle refused any concurrent rename (`WinError 32`). | Uses `open_read_shared` via the new dependency-free `src/rdrs/fileaccess.py` (extracted to break the `entropy`↔`monitoring` import cycle). |
| 2 | `telemetry.py` carried a **divergent duplicate** `calculate_entropy` (and lacked the `[0, 8]` clamp). | Now imports the single shared implementation; identity asserted by a test. |

(Phase 3 defects — incomplete schema migration and the dashboard timestamp
wiring — were found and fixed during FR-05; recorded in
`FR05_PERMANENT_LOCK.md`.)

## 7. Tests and real runtime verification

| Layer | Result |
|---|---|
| `test_entropy.py` (15 tests, 4 new) | passed |
| Full suite | **634 passed, 0 failed** |
| Front end | **32 passed** |
| End-to-end | **ALL 30 CHECKS PASSED** |
| Encoding + safety | **ALL 22 CHECKS PASSED** |
| Config integrity | unchanged |

**Live probes:** `ptmp/verify_fr03_entropy.py` (entropy → metadata →
`high_entropy`, plus the rename-safety check) and
`ptmp/verify_phase3_done_when.py` (10 event rows + 351 process rows; a real
snapshot carrying all six guide fields).

## 8. No fixable defect remains

Every Phase 3 task, the Phase 3 Done-when, and FR-3's "modified files"
qualifier verified against real behaviour.

## 9. Score

**FR-03: 100/100 · Phase 3: 100/100**

## 10. Status

**FROZEN: YES · LOCKED: YES · 100/100: YES · REWRITE REQUIRED: NO · FUTURE REWRITE REQUIRED: NO**

**Complete-file locked:** `src/rdrs/entropy.py`, `src/rdrs/fileaccess.py`.

Phase 3's own files (`storage.py`, `telemetry.py`) are already locked complete
under FR-04/FR-05. `runtime.py` remains **not claimed** — FR-06…FR-12 still add
to it.
