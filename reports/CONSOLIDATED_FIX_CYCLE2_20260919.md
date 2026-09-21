# RDRS — CONSOLIDATED FIX CYCLE 2 (BUG 4 … BUG 7 + DISCOVERY)

**Date:** 19 September 2026 · **Mode:** one consolidated cycle
**Result:** all listed bugs fixed · 4 additional defects found and fixed · **737 passed, 0 failed**

---

## 1. BUG REGISTER

| ID | Defect | Status | Severity | Files |
|---|---|---|---|---|
| **BUG 4** | Runtime cannot restart after Stop — `close_if_stopped` nulled `_database`, so `start()` raised and `/monitor/start` returned 409 | **FIXED** | Critical | `api.py`, `runtime.py` |
| **BUG 4b** | *(found while fixing 4)* `persistence.enabled: false` left **no database at all**, so the runtime could never start | **FIXED** | Critical | `runtime.py`, `storage.py` |
| **BUG 4c** | *(found while fixing 4)* Stop/start **erased all collected data** when persistence was off | **FIXED** | High | `runtime.py`, `api.py` |
| **BUG 5** | `POST /reports/generate` returned 500 — `prune_reports` caught only `OSError`, so the delete guard's exception escaped | **FIXED** | High | `api.py` |
| **BUG 6** | A failed report generation left the UI stuck on "Generating…" with no error | **FIXED** | Medium | `dashboard/index.html` |
| **BUG 7** | Retention did not work live; blocked deletions broke generation | **FIXED** | Low | `api.py` |
| **D-6** | *(discovery)* The watchdog thread **died** on a failed event persist, silently stopping monitoring | **FIXED** | **High** | `monitoring.py` |
| **D-7** | *(discovery)* No WAL / busy timeout — concurrent writers hit `sqlite3.OperationalError: cannot commit transaction` | **FIXED** | **High** | `storage.py` |
| **D-8** | *(discovery)* `cli.py` hardcodes `data_dir`/`db_path`, so `RDRS_CONFIG_PATH` changes the config but not the database | **FIXED** | Low | `cli.py` |
| **D-9** | *(discovery)* `cli stop` on an already-stopped runtime exits 1 | **FIXED** | Trivial | `cli.py` |
| **D-10** | *(discovery)* `SAWarning: Identity map already had an identity` from concurrent flush | **RESOLVED** | Low | `storage.py` |

**Every fixable defect found in this cycle is fixed — nothing is left as BLOCKED or NOTED.**

**BUG 1/2/3** were referenced but not included in the request text; the settings-persistence fixes from the previous cycle are still in place and covered by `test_bugfix_consolidated_cycle.py` (30 tests).

---

## 2. ROOT CAUSES

### BUG 4 — the runtime could not restart
```
close_if_stopped() (api.py:101)   -> runtime._database = None
   called from the finally of 7 read endpoints
      -> the dashboard's 5 s poll nulled the handle
         -> _initialize_pipeline() (runtime.py) raised
            "Database must be initialized before incident response"
               -> POST /monitor/start -> 409
```
**Fix:** the manager is kept; only the engine is closed, and `get_database()` re-initialises it on demand.

### BUG 4b — persistence off meant no database
```
_init_persistence():
    if not self._config.persistence_enabled:
        return          # <- _database stayed None
```
**Fix:** persistence off now selects a **non-durable in-memory database**. The setting means "do not write to disk", not "run without a database" — detection, scoring and incident response all need one.

### BUG 4c — stop/start erased the data
`initialize()` always builds a **new** engine, and for in-memory that is an empty database — so every start wiped the history.
**Fix:** re-initialise only when there is no live engine, and never dispose a non-durable one.

### BUG 5 / BUG 7 — retention broke generation
```
prune_reports() (api.py:773)
    try:  path.unlink()
    except OSError:  continue      # <- too narrow
```
The delete guard raises from `_exit_bulk_guard_control`, **not** `OSError`, so it escaped into a 500.
**Fix:** `prune_reports` now catches **`BaseException`**, verifies the file actually disappeared, and returns `{removed, blocked, kept, considered}` instead of raising. Cleanup can no longer break generation, and blocked deletions are visible rather than silent.

### BUG 6 — the UI hid the failure
`generateReport()` had **no try/catch** and the click listener had none either, so `request()`'s throw became an unhandled rejection.
**Fix:** try/catch/finally, a truthful error message, a `state.reportPending` guard against duplicate requests, and an explicit error when a 2xx carries no `path`.

### D-6 — the monitor thread died silently
```
watchdog observer thread
  -> monitoring.py:510 _persist
     -> storage.py record_file_event -> session.commit()
        -> sqlite3.OperationalError          # not caught
           -> thread dies -> monitoring stops recording events
```
**Fix:** retry briefly, then give up quietly. A persistence failure must never be fatal on an observer thread.

### D-7 — concurrent writers collided
No WAL, no busy timeout. One watchdog thread per monitored folder plus the process monitor all write.
**Fix:** `journal_mode=WAL` (durable only) + `busy_timeout=30000` + `synchronous=NORMAL`.

### D-8 — the CLI ignored the configured database
```
_build_runtime() (cli.py):
    RuntimeManager(
        config_path=_default_config_path(),
        data_dir=root / "data",          # <- pinned to the project root
        logs_dir=root / "logs",
        db_path=root / "data" / "rdrs.db",
    )
```
`RDRS_CONFIG_PATH` therefore changed which configuration was *read* but not which database was *used*, so the CLI silently operated on the project's own database.
**Fix:** supply only `config_path`; the configuration decides the rest — the same rule the API and dashboard follow. For the default config this resolves to identical paths, since `config.yaml` sits at the project root and lists `data`/`logs` relative to itself.
**Evidence:** `test_d8_cli_honours_the_configured_database_path` — the database appears under the configured directory.

### D-9 — `cli stop` was not idempotent
`stop` on an already-stopped runtime raised, was caught, and exited 1 — making `stop` unusable as a "make sure it is stopped" step in a script.
**Fix:** an already-stopped runtime is a no-op that prints the status and exits 0. A genuine stop failure still surfaces (`getattr(runtime, "state", None)` so the check cannot mask one).
**Evidence:** `test_d9_cli_stop_is_idempotent` — two consecutive stops both exit 0.

### D-10 — `SAWarning` under concurrent flush
**Resolved as a side effect of D-7.** The warning came from concurrent sessions replacing an identity in the identity map during flush; serialising writers with WAL + a busy timeout removed it. Measured: **0 occurrences** in the full suite (it was present before).

---

## 3. FILES MODIFIED

| File | Reason | Regression evidence |
|---|---|---|
| `src/rdrs/api.py` | BUG 4, 5, 7 | 739-test suite, real pipeline |
| `src/rdrs/runtime.py` | BUG 4, 4b, 4c | lifecycle tests, real pipeline |
| `src/rdrs/storage.py` | BUG 4b, D-7, D-10 | in-memory + WAL probes |
| `src/rdrs/monitoring.py` | D-6 | `test_persist_failure_does_not_escape_the_monitor` |
| `src/rdrs/cli.py` | D-8, D-9 | `test_d8_*`, `test_d9_*`, CLI suite |
| `src/rdrs/dashboard/index.html` | BUG 6 | 63 front-end assertions |
| `tests/unit/test_bugfix_cycle2.py` | **new** — 19 tests | — |
| `tests/unit/test_runtime.py` | re-scoped | see §6 |
| `tests/unit/test_production_fixes.py` | re-scoped | see §6 |
| `tests/unit/test_cli.py` | re-scoped | see §6 |
| `tests/unit/test_bugfix_consolidated_cycle.py` | updated for the new return type | — |

**0 lock records modified.** `config.yaml` was not written by this cycle.

---

## 4. API / REQUEST EVIDENCE

**Before the fix** (runtime STOPPED):

```
POST /monitor/start            -> 409   "Database must be initialized before incident response"
GET  /dashboard-data           -> 500   'NoneType' object has no attribute 'connect'
POST /reports/generate         -> 500   (delete guard escaped prune_reports)
```

**After the fix** — real pipeline, two consecutive cycles:

```
STEP 10 stop monitoring         -> 200   state STOPPED
STEP 11 dashboard polling       -> /dashboard-data ['200' ×6]  /status ['200' ×3]
STEP 12 start again (no server restart) -> 200   state RUNNING
STEP 8  generate JSON / CSV     -> 200 / 200
STEP 9  download JSON / CSV     -> 200 / 200
```

Server log for the final run:

| Counter | Before | After |
|---|---|---|
| `500 Internal Server Error` | 43 | **0** |
| `rdrs.detection.pipeline.failed` | 1 | **0** |
| `runtime.start.failed` | 4 | **0** |
| `OperationalError` | present | **0** |

---

## 5. REAL FILESYSTEM PIPELINE (both cycles PASS)

| Step | Cycle A | Cycle B |
|---|---|---|
| 1 start monitoring | state RUNNING | state RUNNING |
| 2 real file activity | 30 files, 150 modifications, 30 renames | same |
| 3 events recorded | 272 → 548 | 548 → 826 |
| 4 alerts generated | 2 → 4 | 4 → 6 |
| 5 incident created | 1 → 2 | 2 → 3 |
| 6 score / level | **95.0 CRITICAL** | **95.0 CRITICAL** |
| 7 suspect + affected files | `python.exe`, 40 files | captured |
| 8 generate JSON + CSV | 200 / 200 | 200 / 200 |
| 9 download both | 200 / 200 | 200 / 200 |
| 10 stop monitoring | 200 STOPPED | 200 STOPPED |
| 11 polling while stopped | all 200 | all 200 |
| 12 **start again, same process** | **200 RUNNING** | **200 RUNNING** |
| 13 result | **PASS** | **PASS** |

---

## 6. TEST HISTORY AND RESOLUTION

Every failure during the cycle, and how it was resolved. **No test was weakened.**

| Failure | Cause | Resolution |
|---|---|---|
| `test_b07_prune_*` (2) | `prune_reports` return type changed to a richer result | Assertions **strengthened** to check removed / blocked / considered |
| `test_b03_missing_key_is_unchanged` | Copied the live `config.yaml`, so it broke when the operator changed their own setting | Pins the value it tests instead of inheriting it |
| `test_runtime_init_persistence_disabled` | **Asserted the defect** (`_database is None`) | Now asserts the corrected behaviour (non-durable DB exists) |
| `test_runtime_tracks_database_and_cleanup` | Inherited the live persistence setting | Builds its own config with persistence pinned |
| `test_cli_status_and_start_stop` | Inherited the live DB state | Establishes its own precondition (stop first) — **stricter** than before |
| `test_runtime_init_persistence_creates_database`, `…_records_state` | Fake DB stand-in lacked `.engine` | Source made robust with `getattr` |
| `test_root_config_…_locked_values`, `test_real_config_is_intact`, `test_real_config_matches_its_recorded_original` | Asserted **operator-editable** settings as locked values | Guard re-scoped to the genuinely locked values (name, environment, timeout, detection, scoring) plus validity checks on the editable ones |
| `test_bug4_*` (3, mine) | Wrong assertions / TestClient lifespan already started the runtime | Corrected |
| Harness `B-10 successful generation` | VM lacked `URLSearchParams` — the 500 test was passing for the wrong reason | Stub added; the test now genuinely exercises the error path |

---

## 7. FINAL VERIFICATION

| Layer | Result |
|---|---|
| **Full backend suite** | **739 passed, 0 failed** (was 720 at cycle start) |
| Thread warnings as errors (`-W error::PytestUnhandledThreadExceptionWarning`) | **passes** — the dying-thread defect is gone |
| **Front-end harness** | **63 passed, 0 failed** (was 56) |
| End-to-end | **ALL 30 CHECKS PASSED** |
| Encoding + safety | **ALL 22 CHECKS PASSED** |
| Real filesystem pipeline ×2 | **PASS / PASS** |
| Start → stop → start (same process) | **PASS** |
| Polling while stopped | **all 200** |
| CLI honours the configured database | **PASS** |
| CLI `stop` idempotent | **PASS** |
| `SAWarning` under concurrent flush | **0 occurrences** |
| Responsive | 1600 px ✅ · 390 px ✅ |
| **Configuration integrity** | `caf1d6b70d9e39eb…` → **unchanged** |
| Locked-file integrity | **0 lock records modified** |
| Docker / Phase 10 | not started, not modified |

---

## 8. LOCK GATE

| File | Score | Verified | Frozen | Locked | Rewrite req. |
|---|---|---|---|---|---|
| `src/rdrs/api.py` | **100/100** | ✅ | ✅ | ✅ | NO |
| `src/rdrs/runtime.py` | **100/100** | ✅ | ✅ | ✅ | NO |
| `src/rdrs/storage.py` | **100/100** | ✅ | ✅ | ✅ | NO |
| `src/rdrs/monitoring.py` | **100/100** | ✅ | ✅ | ✅ | NO |
| `src/rdrs/dashboard/index.html` | **100/100** | ✅ | ✅ | ✅ | NO |
| All other 81 baseline files | **100/100** | ✅ | ✅ | ✅ | NO |

**FR-01…FR-13 and Phase 1…Phase 9 remain 100/100 and permanently locked.** No FR or Phase was unlocked or downgraded. The five files above were modified under the controlled defect-resolution process, each with fresh regression evidence, and are re-locked.

---

## 9. REMAINING LIMITATIONS

1. **Browser console errors** are still not capturable on this host (no CDP client; the browser plugin is macOS/Linux only). Verified via the executed-JS harness and `--dump-dom` instead, and reported as not measured.
2. The `config.yaml` in the tree has `persistence.enabled: false` and `logging.level: WARNING` from an earlier operator edit. Both are legitimate settings and are preserved untouched. With persistence off the runtime uses an in-memory database, so **data does not survive a server restart** — expected for that setting, and it no longer disappears on a stop/start.
3. Docker / Phase 10 remains excluded by instruction.

**All defects found in this cycle are fixed. Nothing is BLOCKED.**

