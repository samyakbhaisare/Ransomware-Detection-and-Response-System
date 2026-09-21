# FR-02 + PHASE 2 — PERMANENT LOCK RECORD

**Items:** FR-02 (real-time file monitoring) · Build Guide Phase 2 (Real-Time File Monitoring)
**Date:** 2026-09-18
**Suite at lock time:** 630 passed, 0 failed · front end 32/32 · E2E 30/30 · encoding+safety 22/22
**Config integrity:** hash identical before/after a full suite run

---

## 1. Item name and scope

FR-02 covers watching folders and reacting the instant a file changes.
Phase 2 covers the watchdog monitor, the four event kinds, the normalised event
object, and the live running count.

## 2. Exact requirements (verbatim)

- **FSD FR-2:** "Monitor file create/modify/delete/rename events in real time."
- **Phase 2 Goal:** "Watch folders and react the instant a file changes."
- **Task 1:** "Use the **watchdog** library to watch the **folders listed in config.yaml**."
- **Task 2:** "Capture **four kinds** of change with their timestamps: create, modify, rename/move, and delete."
- **Task 3:** "Turn each raw event into your own small object holding: **event type, file path, time, and file extension**."
- **Task 4:** "**Print a running count of events** so you can see the monitor working live."
- **Command:** `python -m app.main --watch ./data`
- **Done when:** "Creating, renaming, or deleting files in a watched folder **updates your live event count**."

## 3. Every inspected file

`src/rdrs/monitoring.py` · `src/rdrs/runtime.py` · `src/rdrs/cli.py` ·
`src/rdrs/config.py` (watch_paths) · `tests/unit/test_monitoring_excludes.py` ·
`tests/unit/test_cli.py`

## 4. Requirement → code mapping

| Requirement | Production code | Evidence |
|---|---|---|
| watchdog library | `monitoring.DirectoryMonitor` → `watchdog.observers.Observer` | live capture verified |
| watch folders from config.yaml | `RuntimeConfig.monitored_paths` → `runtime._monitors` | 2 configured folders → 2 monitors, both produced events |
| four kinds + timestamps | `_WatchdogHandler.on_created/on_modified/on_deleted/on_moved` → `FileEvent` | created / modified / renamed / deleted all captured live |
| event object: type, path, time, extension | `FileEvent(action, path, timestamp, extension, previous_path, new_path)` | all four fields present and populated |
| **running count printed live** | `cli._watch` — echoes each change then reprints `[events: N]` | count rose 0 → 6 while files were created/renamed/deleted |
| `--watch ./data` form | `cli` accepts `watch` and `--path/--watch PATH` | `rdrs watch --path <dir>` runs |
| Done-when | same | count series `[0,0,0,0,2,3,4,4,5,5,6,6,6]` |

## 5. Locked Rules

§1 sources · §3 100/100 · §4 no fake logic (the count is the real observed
total) · §5 real verification (live filesystem, not mocks) · §9 safety
(simulation only; the watch command only reads) · §13 real-implementation tests.

## 6. Defects found and exact fixes

| # | Defect | Fix |
|---|---|---|
| 1 | **Exclude *patterns* did not work.** The guide asks for "exclude patterns"; matching compared whole path components for equality, so `*.tmp` never matched `skip.tmp`. | `is_excluded_path()` uses `fnmatch` against the file name **and** every component. |
| 2 | **RDRS observed its own output.** The monitor watched `data/` but excluded only the SQLite files, so quarantining evidence or writing a report produced detection input. | `monitor_excludes` now covers `quarantine`, `reports`, `*.tmp`, `*.part`, `*.swp`. Verified: those writes produce 0 events; real sandbox activity still detected. |
| 3 | **Phase 2 Task 4 was unimplemented.** `event_count` was tracked internally and shown on the dashboard, but there was no way to *see the monitor working live* from the command line, and no `watch` command existed. | Added `cli._watch` and the `watch` command: echoes each change (type, path, time, extension) and reprints the running total; supports `--path/--watch`, `--interval`, `--duration`; always stops its monitors. |

## 7. Tests and real runtime verification

| Layer | Result |
|---|---|
| `test_monitoring_excludes.py` (33 tests) | passed; 2 proven to fail pre-fix |
| `test_cli.py` (22 tests incl. 6 new watch tests) | passed |
| Full suite | **630 passed, 0 failed** |
| Front end | **32 passed** |
| End-to-end | **ALL 30 CHECKS PASSED** |
| Encoding + safety | **ALL 22 CHECKS PASSED** |
| Config integrity | unchanged by a full suite run |

**Live Phase 2 probe** (`ptmp/verify_phase2_watch.py`): running `cli._watch`
against a real folder produced
`created → modified → renamed → deleted`, each with its extension, and the
running count rose `0 → 6`.

## 8. Real runtime evidence

- All four actions captured with timestamps and extensions.
- Nested directories, unicode names, empty files, extensionless files and
  dotfiles all captured.
- A 30-file burst delivered 30/30 distinct files (matches a raw-watchdog
  baseline).
- Excludes filter live events; the quarantine/report feedback loop is closed.
- `stop()` releases every monitor; a bounded run can be repeated cleanly.

## 9. No fixable defect remains

Every Phase 2 task and the Done-when condition verified against real behaviour.

**Documented, deliberate non-defect:** watchdog reports a **cross-directory
move** as `on_deleted` + `on_created`, not `on_moved` (proven against a raw
watchdog baseline; same-directory renames *do* emit `on_moved`, which RDRS
handles and classifies as `renamed`). RDRS therefore classifies a cross-dir move
as delete+create. A delete+create correlation heuristic was deliberately **not**
added: it could inflate `mass_rename` into false CRITICALs, and all four
required event kinds are reported faithfully.

## 10. Score

**FR-02: 100/100 · Phase 2: 100/100**

## 11. Status

**FROZEN: YES · LOCKED: YES · 100/100: YES · REWRITE REQUIRED: NO · FUTURE REWRITE REQUIRED: NO**

---

## 12. Complete-file lock eligibility (the shared-file rule)

You asked me to resolve this properly rather than defer it. The honest position:

| File | Eligible for a complete-file lock now? | Why |
|---|---|---|
| `src/rdrs/monitoring.py` | **YES — locked complete** | its documented responsibility is exactly FR-02 + FR-03 file reading; no later FR adds to it |
| `src/rdrs/cli.py` | **YES — locked complete** | status/start/stop/watch are the complete documented CLI surface |
| `src/rdrs/config.py` | **YES — locked complete** (FR-01) | no pending FR work |
| `src/rdrs/logging.py` | **YES — locked complete** (Phase 1) | no pending FR work |
| `src/rdrs/runtime.py` | **NO — not claimed** | FR-06 (window), FR-07 (scoring), FR-08 (incidents), FR-10 (API), FR-11 (dashboard) and FR-12 (reports) all still add to it |
| `src/rdrs/storage.py` | **YES — locked complete** (FR-05) | no pending FR work |
| `src/rdrs/api.py`, `dashboard_data.py`, `dashboard/index.html` | **NO — not claimed** | FR-10/11/12 will extend them |

**A shared file cannot be honestly locked complete while a later FR still has
documented work to add to it** — that is precisely the regional lock the rule
forbids. Those files will be locked complete the moment their last contributing
FR is finished, and until then I will not claim a lock I cannot support. The
remaining FR work is being done in the same continuous pass, not deferred.
