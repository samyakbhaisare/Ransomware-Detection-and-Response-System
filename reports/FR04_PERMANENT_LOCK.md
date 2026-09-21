# FR-04 — PERMANENT LOCK RECORD

**Item:** FR-04 — Collect process metadata using psutil
**Date:** 2026-09-18
**Suite at lock time:** 607 passed, 0 failed · front end 32/32 · E2E 30/30 · encoding+safety 22/22
**Workspace:** `…\RDRS\New folder\RDRS-CLI` (canonical — all modules resolve here)

---

## 1. Item name and scope

FR-04 covers collecting live process metadata with **psutil** and carrying it
through persistence, detection, the API and the dashboard.

**Exact requirement text**

- **FSD FR-4:** "Collect process metadata using psutil."
- **FSD §4 (NFR):** "Real-time monitoring", "Modular Python architecture",
  "Simulation mode by default", "Comprehensive logging".
- **Build Guide Phase 3** — "Use the psutil library to take snapshots of running
  processes: **name, CPU %, memory, disk writes, executable path, and parent
  process**." · "Save file events and process snapshots into the database as they
  happen." · "Handle 'access denied' from psutil gracefully; some system
  processes are protected." · Done when: *"After running the monitor, your
  database contains rows for file events and process snapshots."*

---

## 2. Every inspected file

| # | File | Responsibility | Inspected |
|---|---|---|---|
| 1 | `src/rdrs/telemetry.py` | psutil capture: `ProcessTelemetry`, `CpuSampler`, `ProcessTree`, `ProcessMonitor`, `UserSessionTelemetry`, `NetworkConnectionTelemetry`, `FileMetadata`, `FileArtifact`, `TelemetrySnapshot`, `EntropyHistory` | yes — every class and method |
| 2 | `src/rdrs/runtime.py` | process-monitor thread: `_start_process_monitor` (prime), `_process_monitor_loop`, `_is_measurable_process`, bulk persistence | yes |
| 3 | `src/rdrs/storage.py` | `ProcessRecord` schema, `_process_record`, `record_process`, `record_processes`, `list_processes` | yes |
| 4 | `src/rdrs/burst_detection.py` | `update_process`, `latest_cpu_percent`, `cpu_spike` signal, suspect retention | yes |
| 5 | `src/rdrs/dashboard_data.py` | `recent_processes` payload | yes |
| 6 | `src/rdrs/api.py` | `GET /processes` | yes |
| 7 | `src/rdrs/dashboard/index.html` | `renderProcesses`, process page table | yes |
| 8 | `src/rdrs/fileaccess.py` | `open_read_shared` — supports the capture path | yes |
| 9 | `src/rdrs/entropy.py` | entropy used by file metadata | yes |

---

## 3. FSD requirement → code mapping

| Requirement | Code | Verified |
|---|---|---|
| FR-4 "Collect process metadata using psutil" | `telemetry.ProcessMonitor.capture_all` → `psutil.process_iter(attrs=…)` | 392 real rows |
| Real-time monitoring (NFR) | `runtime._process_monitor_loop` — prime, then capture immediately, then 5 s interval | first rows t≈9 s (was 35 s) |
| Modular architecture (NFR) | capture / storage / detection / API / UI each in its own module | import graph acyclic |
| Simulation mode by default (NFR) | no process termination anywhere (see §5) | verified |
| Comprehensive logging (NFR) | `process.monitor.started`, `process.telemetry.failed`, `runtime.*` | observed in live logs |

---

## 4. Build Guide Phase 3 → code mapping

The guide names **six** fields. All six verified against real captured data
(392 rows from a live monitoring session):

| Required field | Code | Real-data evidence |
|---|---|---|
| name | `ProcessTelemetry.name` → `ProcessRecord.name` | 392/392 rows |
| CPU % | `cpu_percent` ← `CpuSampler` delta | 42/392 non-zero (busy processes only — correct) |
| memory | `memory_rss` + `memory_percent` | 392/392 rows |
| disk writes | `disk_write_bytes` ← `io_counters` | 275/392 rows |
| executable path | `executable_path` ← `exe()` | 389/392 rows |
| parent process | `parent_pid` ← `ppid()` | 390/392 rows |

| Other Phase 3 instruction | Code | Verified |
|---|---|---|
| "Save … process snapshots into the database as they happen" | `runtime` → `storage.record_processes` (one transaction per capture) | rows appear at t≈9 s and every ~6 s after |
| "Handle 'access denied' … gracefully" | per-process guard in `capture_all`, `AccessDenied` caught in `_cpu_seconds` / `from_pid` | pid 0 and pid 4 captured; capture never aborts |
| Done when: DB contains process snapshot rows | `ProcessRecord` table | 392 rows in a live session; 1938 in the E2E run |

**Integration proven end-to-end:** a real snapshot fed through the live path →
`update_process` → `cpu_spike` **fires at 95 %** and stays **off at 3 %** → a
CRITICAL burst creates an incident whose `suspect_process` holds the complete
metadata: `pid, name, parent_pid, command_line, cwd, started_at, cpu_percent,
memory_rss, memory_percent, disk_write_bytes, executable_path`.

---

## 5. Locked Rule verification

| Rule | Requirement | Verified |
|---|---|---|
| §1 | only FSD / Build Guide / Locked Rules as sources | the rejected 125-feature spec was not used |
| §3 | 100/100 only | this record |
| §4 | **no fake logic / hard-coded status** | the fabricated `privilege="user"` was removed (defect 1) |
| §5 | real verification only | every claim above from live runs, not inspection |
| §9 | **safety — never kill a real process** | no `.kill(` / `.terminate(` / `os.kill(` / `taskkill` / `TerminateProcess` / `wait_procs` anywhere in `src/rdrs`; `SIGTERM` appears only as a graceful-shutdown *handler* |
| §11 | every UI control hits a real API | the process table renders `GET /processes` output; a wiring test asserts every frontend key exists in the API payload |
| §13 | tests test the real implementation | 40 tests against live psutil/DB, 7 proven to fail pre-fix |

---

## 6. Defects found and exact fixes

| # | Defect | File | Fix |
|---|---|---|---|
| 1 | `UserSessionTelemetry.current()` hard-coded `privilege="user"`, and it is persisted to `user_sessions` — a fabricated value becoming stored evidence | `telemetry.py` | added `is_elevated()` (`IsUserAnAdmin` on Windows, `geteuid()==0` on POSIX); privilege now detected |
| 2 | `FileArtifact` MIME table wrong for 4 of 6 common types (`.jpg`/`.jpeg` → `image/png`; `.json`, `.pdf` → `application/octet-stream`) | `telemetry.py` | replaced with `mimetypes.guess_type` |
| 3 | Divergent duplicate `calculate_entropy` alongside `rdrs.entropy`'s (and missing the `[0,8]` clamp) | `telemetry.py` | now imports the single shared implementation |
| 4 | `FileMetadata.from_path` used `path.open("rb")` — no `FILE_SHARE_DELETE` on Windows, so hashing a file blocked every concurrent rename (`WinError 32`) | `telemetry.py` | uses `open_read_shared`; proven by a 24 MB concurrent-rename test |
| 5 | Dashboard Parent column read `process.parent_process \|\| process.parent_name`, but the API sends `parent_pid` → the column always rendered "—" | `dashboard/index.html` | reads `process.parent_pid`; header (7) now matches rendered cells (7) |
| 6 | Dead imports introduced/left behind: `ThreadPoolExecutor` (`telemetry.py`), `os` (`monitoring.py`); pre-existing `Counter` (`burst_detection.py`) | 3 files | removed; all 24 modules now import-clean |

---

## 7. Tests and real runtime verification

| Layer | Command | Result |
|---|---|---|
| FR-04 suite | `pytest tests/unit/test_telemetry_metadata.py` | **40 passed** |
| Full suite | `pytest tests -q` | **607 passed, 0 failed** |
| Front end (executed JS) | `node ptmp/dashboard_frontend_harness.mjs` | **32 passed** |
| End-to-end | `python ptmp/e2e_bugfix_verification.py` | **ALL 30 CHECKS PASSED** |
| Encoding + safety | `python ptmp/encoding_safety_scan.py` | **ALL 22 CHECKS PASSED** |
| Compile | `py_compile` on all 10 touched modules | 0 failures |
| Imports | all 24 `rdrs` modules | clean, canonical |
| Live probe | 392-row capture; six fields present; persistence across restart | verified |
| Live probe | real snapshot → `cpu_spike` at 95 %, off at 3 %; incident carries full suspect | verified |
| Live probe | no process-termination call in `src/rdrs` | verified |

**Test discrimination (the tests are not tautological):** running the FR-04 suite
against a reconstructed pre-fix `telemetry.py` produced **7 failures**; against
the pre-fix frontend keys the wiring test provably fails
(`parent_process`, `parent_name` absent from the API).

---

## 8. Evidence that no fixable defect remains

- All six guide fields collected, persisted, exposed and rendered from real data.
- No blocking read, no fabricated value, no duplicated algorithm, no dead import
  in any FR-04 file.
- Every frontend key the process table reads exists in the API payload.
- No process-termination call exists anywhere in `src/rdrs`.
- Nothing in FR-04 is marked BLOCKED; nothing was left as a known issue.

**Known non-defect (documented, deliberately unchanged):**
`runtime.py` uses `getattr(detector, "update_processes", None)` and falls back
to `update_process(snapshot.__dict__.copy())`. `update_processes` does not
exist, so the `getattr` never matches and the fallback is the real, working
path. The dead `getattr` is misleading but harmless; working code was not
rewritten. Recorded here so the next reader does not mistake it for a defect.

---

## 9. Exact score

**FR-04: 100/100**

---

## 10. Permanent status

**FROZEN: YES**
**LOCKED: YES**
**100/100: YES**
**REWRITE REQUIRED: NO**
**FUTURE REWRITE REQUIRED: NO**

Per Locked Rule §14, for every FR-04 file: complete responsibility checked;
FR-01→FR-13 impact checked; Phase 1→Phase 10 integration checked; no mandatory
future rewrite expected; compiled; tests run; real behaviour verified.

**Locked files:** `src/rdrs/telemetry.py` · `src/rdrs/fileaccess.py` ·
the FR-04 regions of `src/rdrs/runtime.py`, `src/rdrs/storage.py`,
`src/rdrs/burst_detection.py`, `src/rdrs/dashboard_data.py`,
`src/rdrs/api.py` · `src/rdrs/dashboard/index.html`.

> **Scope note.** This locks the **FR-04 requirement and the FR-04 code paths**.
> Files such as `runtime.py`, `storage.py`, `api.py` and `index.html` also serve
> other FRs, so they are locked *as they stand for FR-04*; a later FR may
> legitimately extend them, which is an extension, not an FR-04 rewrite.
