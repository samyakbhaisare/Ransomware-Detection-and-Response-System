# RDRS — Production Implementation & Final Audit

> ## ⚠ REVISION 2 (2026-09-17 22:45) — corrections applied
>
> **Superseded statements in Revision 1:**
>
> 1. **"capture_all() 1.52 s"** was correct for the **steady state** but was not
>    qualified. The honest figures are:
>    **cold first capture ≈ 6.9 s** (a one-off psutil internal warm-up) and
>    **steady state ≈ 1.75 s**. The earlier `REQUIREMENTS_EVIDENCE.txt` reported
>    only 7.1 s, which was the cold number — both files have been corrected and
>    the harness now measures and reports the two separately.
> 2. **Test count is now 479**, not 476: three tests were added for a further
>    production fix found during this audit (see item 3).
> 3. **New production fix:** the file monitor opened files **without**
>    `FILE_SHARE_DELETE`, so while RDRS read a file to classify it, **any other
>    process was refused permission to rename or delete that file**
>    (`WinError 32`). A detector must never obstruct the activity it observes —
>    a ransomware burst renames files while RDRS reads them. Fixed with
>    `open_read_shared()` in `monitoring.py`, which opens with
>    `FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE` on Windows.
>    This was found because the E2E run began failing on a rename race; the fix
>    is the root cause, not a retry in the test.
>
> Corrected evidence files: `reports/evidence/REQUIREMENTS_EVIDENCE.txt`,
> `reports/evidence/PROCESS_TELEMETRY_PERSISTENCE.txt` (both regenerated),
> `reports/evidence/SETTINGS_E2E.txt` (new).

**Date:** 2026-09-17
**Workspace:** `D:\Samyak\Projects\Internmo\Project 1 RDRS\RDRS\New folder\RDRS-CLI`
**Status:** 22 VERIFIED · 1 BLOCKED · 0 FAILED — **NOT FROZEN, NOT LOCKED, NOT 100/100**

---

## 1. Production files actually modified

| File | Change | Why |
|---|---|---|
| `src/rdrs/telemetry.py` | **Rewritten capture path** — new `CpuSampler`, batched `process_iter(attrs=...)`, pid-0 exclusion, `from_pid(cpu_percent=…)` | The 26-second capture (priority 10) |
| `src/rdrs/runtime.py` | `prime()` on start; loop captures immediately then waits; suspect selection skips kernel pseudo-processes; bulk persist | Same, plus a false-positive fix |
| `src/rdrs/storage.py` | New `record_processes()` (one transaction) and `count_records_many()` (one snapshot); `_process_record()` extracted | Persistence cost + torn counters |
| `src/rdrs/dashboard_data.py` | `summary()` now reads all counters from a single snapshot | Torn status snapshot |
| `src/rdrs/api.py` | `get_runtime()` returns the **live** singleton; `lifespan` uses it | Stale-reference bug that corrupted a config file |
| `src/rdrs/monitoring.py` | New `open_read_shared()`; entropy sample read through it | RDRS blocked renames/deletes of files it was reading |
| `Dockerfile`, `docker-compose.yml`, `requirements.txt` | UTF-8 BOM removed | Deployment risk (see §5) |
| `config/rdrs.yaml` | **Restored to its original bytes** (hash `341f8d46`) | See §3 — net zero change |

**Not modified:** `response.py`, `quarantine.py`, `scoring.py`, `burst_detection.py`, `monitoring.py`, `entropy.py`, `config.py`, `logging.py`, `audit.py`, `health.py`, `policy.py`, `workers.py`, `events.py`, `cli.py`, `config_engine.py`, `detection.py`, `__init__.py`, `pyproject.toml`, and `dashboard/index.html`.

**Tests:** `tests/unit/test_telemetry_performance.py` (new, 14), `tests/unit/test_production_fixes.py` (new, 14); 2 existing tests adapted to the new loop/capture shape (no assertion weakened).

---

## 2. Features genuinely implemented

### 2.1 Process telemetry performance — the headline fix (priority 10)

The defect was **not** merely slow; it was functionally fatal to process telemetry.

`ProcessTelemetry.from_pid()` called `psutil.Process.cpu_percent(interval=0.05)`, which **sleeps 50 ms per process**. Measured: **25.72 s** for 382 processes. The monitor called it once per cycle, so the effective period was ~30 s and any run shorter than ~35 s persisted **zero** process rows.

Three changes, each measured:

| Change | Effect |
|---|---|
| Non-blocking `CpuSampler` — derives the percentage from the `(wall_time, cpu_seconds)` delta between cycles | removes the 50 ms sleep entirely |
| Batched `process_iter(attrs=…)` instead of individual accessors; `memory_percent` derived from one `virtual_memory()` reading | **8.06 s → 1.33 s** for ~385 processes |
| `record_processes()` — one transaction for the whole capture instead of one per row | removes ~385 commits per cycle |

**Measured result:**

```
                        BEFORE              AFTER
cold first capture      25.72s              6.86s   (one-off psutil warm-up)
steady-state capture    25.72s              1.75s   (16x faster)
first process rows      none before t=35s   t~9s
E2E process_count       0                   1962
```

**The 25.72 s figure was not "cold" — every capture cost that much**, because
the 50 ms sleep was paid per process on every cycle. The 6.86 s cold figure is
a *different, smaller* one-off cost (psutil's internal process-info warm-up)
that exists independently of this fix; it is reported separately so the two are
never conflated.

Correctness preserved: every Build Guide Phase 3 field is still collected (`pid, name, parent_pid, cpu_percent, memory_percent, disk_write_bytes, executable_path`, plus `command_line, cwd, started_at`), and the first observation of a process reports `0.0` — "not yet measurable", never a fabricated figure.

### 2.2 False-positive fix: the System Idle Process

While measuring I found that pid 0 (`System Idle Process`) reports **~100 % CPU** under delta sampling, because its `cpu_times` track *idle* time. The monitor nominates the highest-CPU process as the suspect, so pid 0 would have been selected **every cycle** and fired the `cpu_spike` signal continuously — a permanent false positive worth +15 score.

Fixed in two places: `CpuSampler.NON_MEASURABLE_PIDS` reports 0.0 for pid 0, and `RuntimeManager._is_measurable_process()` excludes known-invalid pids from suspect selection (an *unknown* pid is kept, so evidence is never silently discarded). Verified: the top-CPU rows are now real user processes.

### 2.3 Settings: apply → persist → survive restart (priority 6)

`POST /settings` now demonstrably round-trips. Verified against a throwaway config:

```
1. initial       : {"name": "rdrs", "environment": "test", "log_level": "INFO"}
2. POST /settings: 200
3. /status       : {"name": "rdrs-prod", "environment": "production",
                    "log_level": "DEBUG", "shutdown_timeout": 25.0}
4. file on disk  : {"name": "rdrs-prod", "environment": "production",
                    "log_level": "DEBUG", "shutdown_timeout": 25.0}
5. invalid name  : 400
6. bad log level : 400
7. after restart : {"name": "rdrs-prod", "environment": "production",
                    "log_level": "DEBUG", "shutdown_timeout": 25.0}
```

Real config hash before and after: **unchanged**.

### 2.4 Torn status snapshot

`summary()` issued five independent `COUNT` queries, each in its own transaction, so a concurrent writer could interleave a score and its incident and produce counters that look inconsistent while the data is correct. Added `count_records_many()` which counts all tables inside **one** transaction; `summary()` uses it.

### 2.5 Runtime lifecycle

`start → stop → restart` verified: `start` 200→RUNNING, `stop` 200→STOPPED, restart 200→RUNNING, stopping when already stopped 409, starting when already running 409.

### 2.6 Stale import reference (priority 11)

`api.runtime_manager` is bound once at import. After `RuntimeManager.reset_singleton()` it is **stale**, and a stale runtime can point at a *different* configuration file — which is exactly how a settings write landed in the wrong file during testing. `get_runtime()` now resolves through `RuntimeManager()` (the live singleton) and `lifespan` uses the same accessor.

---

## 3. An incident during testing — full disclosure

While testing settings persistence I used `RuntimeManager.reset_singleton()` and then pointed the runtime at a temp config. Because `api.runtime_manager` was stale, `POST /settings` wrote to the **real** `config/rdrs.yaml`, changing `name`, `environment`, `shutdown_timeout` and `log_level`.

**Detected immediately**, because the test asserted on the temp file and it was unchanged. I:

1. restored `config/rdrs.yaml` from the values captured in `reports/evidence/REQUIREMENTS_EVIDENCE.txt`;
2. verified the restore is **byte-exact** — sha256 `341f8d46ca63aa6d…`, identical to the hash recorded at the start of the earlier cycle;
3. copied it to `reports/backups/config/rdrs.yaml.original`;
4. fixed the root cause (§2.6);
5. added `test_real_config_is_intact` and `test_real_config_matches_its_backup` so the suite now fails if this ever recurs.

Detection thresholds and scoring weights were **never** affected — verified against the backup.

---

## 4. Tests actually executed and their results

| Layer | Command | Result |
|---|---|---|
| Full suite | `python -m pytest tests -q` | **476 passed, 0 failed** (was 448; +28 new) |
| New: telemetry | `pytest tests/unit/test_telemetry_performance.py` | **14 passed** |
| New: production fixes | `pytest tests/unit/test_production_fixes.py` | **14 passed** |
| Coverage | `pytest --cov=rdrs` | **96 %** (guide target ≥ 90 %) |
| Front end (executed JS) | `node ptmp/dashboard_frontend_harness.mjs` | **32 passed, 0 failed** |
| End-to-end | `python ptmp/e2e_bugfix_verification.py` | **ALL 30 CHECKS PASSED** |
| Encoding + safety | `python ptmp/encoding_safety_scan.py` | **ALL 22 CHECKS PASSED** |
| Requirements audit | `python ptmp/requirements_evidence.py` | **22 VERIFIED · 1 BLOCKED · 0 FAILED** |

The new tests assert **behaviour, not timings**, so they do not become flaky:

- `test_from_pid_does_not_call_the_blocking_cpu_accessor` — monkeypatches `psutil.Process.cpu_percent` to raise; if the 50 ms sleep ever returns, the test fails deterministically.
- `test_capture_all_uses_the_batched_attr_pass` — asserts the single `process_iter(attrs=…)` pass.
- `test_cpu_sampler_computes_real_delta` — 0.25 CPU-seconds over 0.5 wall-seconds = 50 %.
- `test_cpu_sampler_excludes_system_idle_process`, `test_suspect_selection_excludes_pid_zero`.
- `test_record_processes_persists_a_whole_capture_in_one_call`.
- `test_settings_are_persisted_and_survive_a_restart`, `test_get_runtime_returns_the_live_singleton`, `test_dashboard_summary_uses_one_snapshot`, `test_deployment_files_have_no_bom`.

**Two tests were adapted, none weakened.** `test_process_monitor_loop_one_cycle` and `test_process_monitor_capture_all_skips_unreadable_processes` asserted the old implementation shape (wait-then-capture; `from_pid` raising). They now assert the same guarantees against the new shape — the unreadable-process test is strictly stronger, as it also proves the capture continues past a dying process.

---

## 5. Remaining genuine blockers

### 5.1 Docker / Build Guide Phase 10 — BLOCKED BY ENVIRONMENT (not fixable here)

```
$ docker --version
Docker version 29.8.0, build 88096ef
$ docker info
failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine
$ docker compose version
docker: unknown command: docker compose
```

The daemon is not running and the `compose` subcommand is unavailable, so the guide's Phase 10 "done when" (`docker compose up` starts the system) **cannot be executed**. No amount of code work changes this — it needs a running daemon.

**Mitigation applied:** removed the UTF-8 BOM from `Dockerfile`, `docker-compose.yml` and `requirements.txt`. A leading BOM is never required in any of these and is a plausible build/parse failure; this removes the risk without needing a daemon. Statically re-verified afterwards: `Dockerfile` starts with `FROM python:3.13-slim`, `EXPOSE 8000`, `HEALTHCHECK` present; compose parses and still declares service `rdrs`, ports `8000:8000`, and the data/logs/config volumes. **This is a risk reduction, not a Phase 10 pass.**

### 5.2 Stale duplicate checkout — NOT removed (needs your decision)

`D:\Samyak\Projects\Internmo\Project 1 RDRS\RDRS\RDRS-CLI` is an older full copy of the project. The import path is already correct — the `.pth` points at this workspace and all 13 modules resolve here — so it is inert. I did **not** delete it: it is a full project copy outside the workspace and removing it is destructive. Recommend archiving it so it can never be selected as the import target again.

### 5.3 `setuptools` missing from the venv

`pip install -e .` fails with `BackendUnavailable: Cannot import 'setuptools.build_meta'`. The editable install is currently correct (`.pth` written directly), but any future `pip install -e .` will fail until setuptools is installed.

---

## 6. FSD / Build Guide audit — final result

| Requirement | Status | Key evidence |
|---|---|---|
| FR-1 configuration | VERIFIED | loads + validates; 4 invalid inputs rejected |
| FR-2 monitoring | VERIFIED | all four actions captured live (`created/modified/renamed/deleted`) |
| FR-3 entropy | VERIFIED | text 3.41, random 7.99, 5 MB sampled in 0.003 s |
| FR-4 psutil metadata | VERIFIED | **383 snapshots in 7.1 s** (was 26.5 s); all guide fields present |
| FR-5 SQLite storage | VERIFIED | 5 tables; **live db processes=1928** (was 0) |
| FR-6 sliding window | VERIFIED | quiet NORMAL; burst exceeds thresholds; expires after 61 s |
| FR-7 weighted score | VERIFIED | 40/30/25/15/10; bands confirmed at 39/40/69/70; cap 100 |
| FR-8 alerts + incidents | VERIFIED | one burst → 2 scores, 2 alerts, **1 incident** at CRITICAL |
| FR-9 quarantine | VERIFIED | copy-only; original byte-identical and still present |
| FR-10 REST API | VERIFIED | 11 endpoints, no 404s; `/scan` returned 461 real files |
| FR-11 dashboard | VERIFIED | all guide elements, 5 s polling, dark theme, real payload |
| FR-12 reports | VERIFIED | JSON (13 keys) + CSV (96 rows) from a real incident |
| FR-13 testing / Docker | **PARTIAL** | testing VERIFIED (476 passed); Docker execution BLOCKED |
| Phase 1 | VERIFIED | config loader + 5 rotating logs populated |
| Phase 2 | VERIFIED | watchdog monitor, normalised `FileEvent` |
| Phase 3 | VERIFIED | 5 tables; process write path + real session persistence |
| Phase 4 | VERIFIED | via FR-6 |
| Phase 5 | VERIFIED | via FR-7/FR-8/FR-9 |
| Phase 6 | VERIFIED | via FR-10 |
| Phase 7 | VERIFIED | via FR-11 |
| Phase 8 | VERIFIED | via FR-12 |
| Phase 9 | VERIFIED | 476 passed, **96 %** coverage |
| **Phase 10** | **BLOCKED** | Docker daemon unreachable |

---

## 7. Submission readiness

**NOT READY for final submission — one blocker remains.**

Everything implementable in this environment is implemented and verified: **22 requirements VERIFIED, 0 FAILED**, 476 tests passing, 96 % coverage, front end executed (not inspected), end-to-end pipeline proven, encoding and safety scans clean.

The single blocker is **Phase 10**, which requires a running Docker daemon. Under Locked Rule §16 the project cannot be declared complete while Phase 1→10 is not all complete, and under §14 no file should be marked FROZEN while a required phase is unexecuted.

**Exact next action:**

1. Start Docker Desktop (or make a daemon reachable) and confirm with `docker info`.
2. Run `docker compose up --build`, then verify `http://127.0.0.1:8000/health` responds from the container. This closes Phase 10 and, with it, FR-13's Docker half.
3. If the build fails on the first instruction, the BOM removal in §5.1 has already eliminated the most likely cause; re-check the `FROM` line and the `requirements.txt` first line.
4. Re-run `python ptmp/requirements_evidence.py` and the full suite; then consider freezing file-by-file under Rule §14.
5. Separately (not a blocker): archive the stale checkout at `…\RDRS\RDRS\RDRS-CLI`, and install `setuptools` into the venv so `pip install -e .` works again.

**Do not mark the project FROZEN, LOCKED or 100/100 until step 2 succeeds.**
