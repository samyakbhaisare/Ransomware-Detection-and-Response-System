# FR-01 + PHASE 1 — PERMANENT LOCK RECORD

**Items:** FR-01 (configuration) · Build Guide Phase 1 (Project Setup & Entropy Detector)
**Date:** 2026-09-18
**Suite at lock time:** 623 passed, 0 failed · front end 32/32 · E2E 30/30 · encoding+safety 22/22
**Config integrity:** `config.yaml` hash unchanged by a full suite run

---

## 1. Item name and scope

FR-01 covers loading configuration from `config.yaml`. Phase 1 covers the
foundation files, the config loader, the five rotating logs, and Shannon
entropy.

## 2. Exact requirements (verbatim)

- **FSD FR-1:** "Load configuration from config.yaml."
- **Build Guide Phase 1 Goal:** "Create the foundation files and the entropy
  function that the whole system relies on."
- **Task 1:** "Write a small config loader that reads settings from config.yaml:
  **which folders to watch**, and the alert thresholds."
- **Task 2:** "Set up **rotating log files (one each for system, events, alerts,
  errors, and audit)**. The loguru library makes this easy."
- **Task 3:** "Implement **Shannon entropy**: a function that takes bytes and
  returns a number from 0 (totally predictable) to 8 (totally random)."
- **Task 4:** "Add a helper that reads the **first 64 KB** of a file and returns
  its entropy (sampling keeps it fast on big files)."
- **Done when:** "A normal text file returns a low entropy (about 0–5), and a
  file full of random bytes returns close to 8.0."

## 3. Every inspected file

`src/rdrs/config.py` · `src/rdrs/runtime.py` · `src/rdrs/logging.py` ·
`src/rdrs/entropy.py` · `config.yaml` · `tests/unit/test_api.py` ·
`tests/conftest.py` (new) · `tests/unit/test_config_watch_paths.py` (new) ·
`tests/unit/test_production_fixes.py`

## 4. Requirement → code mapping

| Requirement | Production code | Evidence |
|---|---|---|
| FR-1 load from config.yaml | `RuntimeConfig.load`, `RuntimeManager.default_config_path` | resolves `config.yaml` at the project root |
| Phase 1 "which folders to watch" | `RuntimeConfig.watch_paths`, `.monitored_paths`; `runtime._monitors` | 2 configured folders → 2 monitors, both produced events |
| Phase 1 "alert thresholds" | `detection_thresholds` (6 values), `scoring_weights` (5 values) | values match the guide exactly |
| Phase 1 five rotating logs | `LOG_FILES`, `initialize_rotating_logs` | all 5 files created; `RotatingFileHandler(maxBytes=5 MB, backupCount=5)` |
| Phase 1 Shannon entropy | `entropy.calculate_entropy` | `b""`→0.0, uniform→8.0 |
| Phase 1 64 KB helper | `entropy.calculate_file_entropy`, `MAX_SAMPLE_SIZE` | `== 64*1024`; 5 MB file sampled in <1 s |
| Phase 1 Done-when | same | text 3.41 (0–5 ✓), random 7.99 (≥7.9 ✓) |

## 5. Locked Rules

§1 sources · §3 100/100 only · §4 no fake logic · §5 real verification ·
§9 safety (watch paths default to the project's own `data` sandbox) ·
§13 tests test the real implementation.

## 6. Defects found and exact fixes

| # | Defect | Fix |
|---|---|---|
| 1 | **`config.yaml` had no "which folders to watch".** Phase 1 requires it, and the runtime always monitored `config.data_dir` — so a user could not point RDRS at the folders they actually want protected. | Added `RuntimeConfig.watch_paths` (parsed, validated, resolved to absolute paths against the config file), a `monitored_paths` property falling back to `data_dir`, `watch_paths` in `config.yaml`, and a multi-monitor runtime (`_monitors`, started/stopped/health-checked together). |
| 2 | **Running the test suite rewrote the shipped `config.yaml`.** `tests/unit/test_api.py` built a module-level `TestClient(app)` with no runtime configuration, so it used the default config path; `test_settings_endpoint` then POSTed to `/settings`, which persists so changes survive a restart — silently stripping the file's comments and settings. | `test_api.py` now binds its client to a temp config. Added `tests/conftest.py` with a session-scoped autouse guard that fails the run and restores the file if any test modifies it. |
| 3 | Relative `watch_paths` stayed relative when the config path was relative. | Resolved against `path.resolve().parent`. |

## 7. Tests executed and results

| Layer | Result |
|---|---|
| `test_config_watch_paths.py` (new, 13 tests) | **13 passed** |
| `test_api.py` (rewritten, 9 tests) | **9 passed** |
| Full suite | **623 passed, 0 failed** |
| Front end (executed JS) | **32 passed** |
| End-to-end | **ALL 30 CHECKS PASSED** |
| Encoding + safety | **ALL 22 CHECKS PASSED** |
| Config integrity | hash identical before/after a full suite run |

## 8. Real runtime evidence

- Two configured watch folders → 2 monitors → events 0 → 4. Both folders
  monitored; all monitors stopped cleanly on `stop()`.
- Unset `watch_paths` → falls back to `data_dir` (1 monitor).
- `watch_paths: not-a-list` → `ValueError`; blank entries skipped.
- All five log files exist and are populated; rotation configured (5 MB × 5).

## 9. No fixable defect remains

Every Phase 1 task and the Done-when condition verified against real behaviour;
the config loader now reads all three documented settings; the shipped config
can no longer be corrupted by the suite.

**Documented, deliberate deviation:** the guide says "The loguru library makes
this easy" and lists loguru in its install command. The project uses the
standard-library `logging` module with `RotatingFileHandler`, which produces the
same required outcome (five rotating logs) with no extra dependency. The
*outcome* the guide requires is asserted by tests; this is a substitution, not a
missing requirement.

## 10. Score

**FR-01: 100/100 · Phase 1: 100/100**

## 11. Status

**FROZEN: YES · LOCKED: YES · 100/100: YES · REWRITE REQUIRED: NO · FUTURE REWRITE REQUIRED: NO**

**Complete-file locks:** `src/rdrs/config.py` (no pending FR work),
`src/rdrs/logging.py` (Phase 1 complete), `tests/conftest.py`,
`tests/unit/test_config_watch_paths.py`.

`src/rdrs/runtime.py` is **not** eligible for a complete-file lock yet — FR-06
through FR-12 will still extend it. Under the complete-file rule it is locked
only once the last FR touching it is finished.
