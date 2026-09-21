# RDRS — Requirements Evidence Matrix (FR-01…FR-13, Phase 1…Phase 10)

> ## ⚠ CORRECTION — SUPERSEDED SECTIONS (added 2026-09-17 22:4x)
>
> This matrix was produced **before** the process-telemetry performance fix
> (source change at 21:26). Its **FR-04, FR-05 and Phase 3** rows are therefore
> **outdated** and are superseded by the current evidence:
>
> | Row | Statement in this matrix (outdated) | Current, accurate value |
> |---|---|---|
> | FR-04 | `capture_all()` takes ~26.5 s; "measured performance limitation … ~30 s telemetry period" | **Steady-state `capture_all()` = 1.75 s** (cold first call 6.86 s, one-off psutil warm-up). The 50 ms-per-process sleep is **gone**. |
> | FR-05 | live db `processes = 0` (short run) | live db **`processes = 1962`**; first rows at **t≈9 s** (was 0 rows before t=35 s) |
> | Phase 3 | "the process-monitor cycle is ~30 s … a run shorter than ~35 s persists no process rows" | cycle is now **~1.5 s capture + 5 s interval**; the loop captures **immediately** instead of waiting first |
> | — | `telemetry.py` "NOT lock-eligible" because of the latency | latency fixed; `telemetry.py` now **Candidate** |
>
> **Everything else in this matrix remains valid.** The authoritative current
> evidence is:
> `reports/evidence/REQUIREMENTS_EVIDENCE.txt` (regenerated),
> `reports/evidence/PROCESS_TELEMETRY_PERSISTENCE.txt` (regenerated),
> `reports/evidence/SETTINGS_E2E.txt`, and
> `reports/RDRS_PRODUCTION_FINAL_AUDIT.md`.
>
> **One further production fix was made after this matrix:** the file monitor
> opened files without `FILE_SHARE_DELETE`, so RDRS **blocked renames and
> deletes** of any file it was reading (WinError 32). Fixed via
> `open_read_shared()` in `monitoring.py`.

**Date:** 2026-09-17
**Workspace:** `D:\Samyak\Projects\Internmo\Project 1 RDRS\RDRS\New folder\RDRS-CLI`
**Interpreter:** `.venv\Scripts\python.exe` (Python 3.14.3) · Node 22.22.2
**Evidence:** `reports/evidence/REQUIREMENTS_EVIDENCE.txt` (full raw output),
`reports/evidence/PROCESS_TELEMETRY_PERSISTENCE.txt`

> **Verdict: 22 VERIFIED · 1 BLOCKED · 0 FAILED.**
> The project is **NOT FROZEN, NOT LOCKED, NOT 100/100** — Phase 10 cannot be
> executed in this environment (Docker daemon down), and one measured
> performance limitation is recorded against the FSD's "real-time monitoring"
> non-functional requirement.

---

## 1. Authoritative sources used

| Source | Location | Status |
|---|---|---|
| **FSD** — `RDRS_Functional_Specification_Document_FSD.docx` | `…\Project 1 RDRS\` (parent of `RDRS\`) | Read; text extracted to `ptmp/FSD_EXTRACTED.txt` |
| **Build Guide** — `COMPLETE HANDS-ON BUILD GUIDE` | `build_guide_text.txt` (repo root) | Read in full (216 lines) |
| **Locked Development Rules** | `…\Project 1 RDRS\RDRS_LOCKED_DEVELOPMENT_RULES.md` | Read in full |

**Explicitly excluded per Locked Rules §1:** the *125-Feature Theoretical
Maximum Master Spec* and its *Audit Rules* (both present in `docs/` as PDFs).
They are **not** used as sources anywhere in this matrix.

**FSD functional requirements (verbatim, FSD §3):**

| ID | Text |
|---|---|
| FR-1 | Load configuration from config.yaml. |
| FR-2 | Monitor file create/modify/delete/rename events in real time. |
| FR-3 | Calculate Shannon entropy of modified files. |
| FR-4 | Collect process metadata using psutil. |
| FR-5 | Store events, processes, alerts and incidents in SQLite. |
| FR-6 | Analyze events in a sliding time window. |
| FR-7 | Compute weighted threat score (0-100). |
| FR-8 | Generate alerts and incident records. |
| FR-9 | Copy evidence to quarantine (simulation mode). |
| FR-10 | Provide REST API endpoints. |
| FR-11 | Display live dashboard. |
| FR-12 | Generate JSON/CSV reports. |
| FR-13 | Support automated testing and Docker deployment. |

> The FSD writes these as `FR-1`…`FR-13`; the Locked Rules and this matrix use
> the zero-padded `FR-01`…`FR-13`. Same requirements.

**FSD non-functional requirements (§4):** real-time monitoring · modular Python
architecture · simulation mode by default · comprehensive logging · maintainable
and documented code.
**FSD §7 Assumptions & Constraints:** defensive use only, no malware execution,
testing only in a sandbox environment.

---

## 2. FR-01 … FR-13 evidence matrix

Legend — **Rewrite required**: whether the implementing file must be rewritten
before it can be frozen. **Lock eligibility**: whether the file(s) may be marked
`100/100 — VERIFIED — FROZEN` today.

| ID | Requirement (FSD §3) | Implementation (files / functions) | Verification command | Evidence | Actual result | Status | Remaining gap | Rewrite | Lock |
|---|---|---|---|---|---|---|---|---|---|
| **FR-01** | Load configuration from config.yaml | `src/rdrs/config.py` → `RuntimeConfig.load()`, `validate()`, `_merge_numeric_settings()`, `to_dict()`; consumed by `runtime.py::_apply_runtime_settings` | `python ptmp/requirements_evidence.py` (§FR-1) | `reports/evidence/REQUIREMENTS_EVIDENCE.txt` | Loads `config/rdrs.yaml` → name=`rdrs`, env=`test`, data=`data`, logs=`logs`, db=`data\rdrs.db`, timeout=`15.0`, level=`INFO`, persistence=`True`; 4 invalid inputs rejected (window=0, entropy=99, level=`NOT_A_LEVEL`, weight=−5) | **VERIFIED** | Path is `config/rdrs.yaml`, not the guide's literal root `config.yaml`. Functionally equivalent; documented deviation. | NO | Candidate |
| **FR-02** | Monitor create/modify/delete/rename in real time | `src/rdrs/monitoring.py` → `DirectoryMonitor`, `RealtimeFileMonitor`, `FileEvent`, `calculate_file_metadata()`; wired in `runtime.py::start` | `python ptmp/requirements_evidence.py` (§FR-2) | same | 6 live events captured; **all four** actions observed: `created`, `modified`, `renamed`, `deleted`; `FileEvent` carries `action, path, timestamp, extension, previous_path, new_path, metadata` | **VERIFIED** | None | NO | Candidate |
| **FR-03** | Calculate Shannon entropy of modified files | `src/rdrs/entropy.py` → `calculate_entropy()`, `calculate_file_entropy()`, `file_entropy()`, `MAX_SAMPLE_SIZE` | `python ptmp/requirements_evidence.py` (§FR-3) | same | repetitive text = **3.41** (guide: ~0–5); 20 KB random = **7.99** (guide: ~8.0); 5 MB file sampled in **0.005 s** (64 KB sample) | **VERIFIED** | None | NO | Candidate |
| **FR-04** | Collect process metadata using psutil | `src/rdrs/telemetry.py` → `ProcessMonitor.capture_all()`, `capture()`, `ProcessTelemetry.from_pid()` | `python ptmp/requirements_evidence.py` (§FR-4) | same + `PROCESS_TELEMETRY_PERSISTENCE.txt` | **390** real snapshots via psutil 7.2.2; every guide field present: `pid, name, cpu_percent, memory_percent, disk_write_bytes, executable_path, parent_pid` | **VERIFIED** | **Measured performance limitation** — `capture_all()` takes **26.5 s** (≈67 ms/process) because `from_pid()` calls `cpu_percent(interval=0.05)`, which sleeps 50 ms per process. See §5. | NO | **NOT eligible** (see §5) |
| **FR-05** | Store events, processes, alerts, incidents in SQLite | `src/rdrs/storage.py` → `DatabaseManager`, `EventRecord/ProcessRecord/ScoreRecord/AlertRecord/IncidentRecord`, `record_*`/`count_records` | `python ptmp/requirements_evidence.py` (§FR-5) | same | All five tables present (`events, processes, scores, alerts, incidents` + 4 auxiliary); live db: events=257, alerts=2, incidents=1, scores=2. Process write path proven separately (see FR-04/Phase 3) | **VERIFIED** | Live db shows `processes=0` because it was written by a short run; the write path is proven separately (381 rows at t=35 s). | NO | Candidate |
| **FR-06** | Analyze events in a sliding time window | `src/rdrs/burst_detection.py` → `BurstDetectionEngine`, `_build_analysis()`, `_purge_expired()`, `_window_now/_ingest_now/_observe_now` | `python ptmp/requirements_evidence.py` (§FR-6) | same | Window 60 s; quiet → 3 modified/min **NORMAL**; burst → 60 modified/min, entropy 7.9 → **WARNING**; window expires: 20 → **0** modified/min after +61 s | **VERIFIED** | None | NO | Candidate |
| **FR-07** | Compute weighted threat score (0-100) | `src/rdrs/burst_detection.py` (`DEFAULT_SCORING_WEIGHTS`), `src/rdrs/scoring.py` → `ThreatScore.total/.level`, `ThreatScoringEngine` | `python ptmp/requirements_evidence.py` (§FR-7) | same | Weights match the guide exactly (40/30/25/15/10); bands confirmed at the boundaries — 39→NORMAL, 40→WARNING, 69→WARNING, 70→CRITICAL; all signals = **100** (cap holds) | **VERIFIED** | None | NO | Candidate |
| **FR-08** | Generate alerts and incident records | `src/rdrs/response.py` → `IncidentResponseEngine.process()`; `runtime.py::_handle_file_event` (band dedup) | `python ptmp/requirements_evidence.py` (§FR-8) | same | One real burst → **2 scores, 2 alerts, 1 incident** at **CRITICAL**; alert severity CRITICAL with evidence keys `affected_files, level, score, signals, simulation_mode, suspect_process`; incident `threat_score=95.0`, `threat_level=CRITICAL`, `score_id=2`, `alert_id=2`, affected-file list populated | **VERIFIED** | None. (2 scores/alerts = one per severity band, WARNING then CRITICAL — not duplicates; dedup verified in the B-02 cycle.) | NO | Candidate |
| **FR-09** | Copy evidence to quarantine (simulation mode) | `src/rdrs/quarantine.py` → `QuarantineManager.quarantine()` (`shutil.copy2`) | `python ptmp/requirements_evidence.py` (§FR-9) | same | `simulation_mode=True`; copy created in vault; **original still present and byte-identical** (sha256 match); copy matches original; source contains `shutil.copy2` and **no** `move`/`remove`/`unlink` | **VERIFIED** | None | NO | Candidate |
| **FR-10** | Provide REST API endpoints | `src/rdrs/api.py` → FastAPI app; routes `/health /status /alerts /events /reports /processes /incidents /dashboard-data / /scan /settings /reports/generate /reports/{id}/{format}` | `python ptmp/requirements_evidence.py` (§FR-10) | same | **11 documented endpoints all reachable, zero 404s**; JSON verified on 8 read endpoints; `POST /scan` walked the sandbox and returned **461 real files**; `GET /settings` correctly 405 (POST-only per the guide) | **VERIFIED** | `/scan` and `/reports/generate` take parameters as **query params**, not JSON bodies. Acceptable under the FSD/guide wording; noted for API consumers. | NO | Candidate |
| **FR-11** | Display live dashboard | `src/rdrs/dashboard/index.html`; served by `api.py::dashboard()`; data from `dashboard_data.py` via `/dashboard-data` | `python ptmp/requirements_evidence.py` (§FR-11) | same | `GET /` 200, `text/html; charset=utf-8`, 76 339 bytes; **all 11 guide-required elements present** (threat level, score, recent alerts `dashboardAlerts`, recent events `dashboardEvents`, recent processes, top modified `topModified`, canvas CPU/entropy chart, status dot, start/stop/refresh controls); auto-refresh every 5 000 ms; dark theme (`--bg:#080c11`); no hardcoded ONLINE/OFFLINE; real payload keys `alerts, events, incidents, processes, score, summary, telemetry, top_modified` | **VERIFIED** | None | NO | Candidate |
| **FR-12** | Generate JSON/CSV reports | `src/rdrs/api.py` → `generate_report()`, `download_report()`, `reports()` | `python ptmp/requirements_evidence.py` (§FR-12) | same | `POST /reports/generate` 200 (json and csv) for real incident 1; `GET /reports/1/json` 200, `…/csv` 200; JSON has **13 keys** incl. `threat_score, threat_level, affected_files, suspect_process, timeline, recommendations, alerts, scores, evidence`; CSV **96 rows**, header `record_type,timestamp,incident_id,action,path,score,level,details` | **VERIFIED** | HTML/PDF are **optional** in the guide ("if you have time") and are **not implemented** — not counted as a gap. Reports are generate-then-download. | NO | Candidate |
| **FR-13** | Support automated testing and Docker deployment | `tests/` (27 modules); `Dockerfile`; `docker-compose.yml`; `README.md` | `python -m pytest tests -q`; `docker info` | `BUGFIX_FINAL_SUITE.txt`, `REQUIREMENTS_EVIDENCE.txt` | **Automated testing: 448 passed, 0 failed.** Docker artefacts present (Dockerfile with `EXPOSE 8000` + `HEALTHCHECK`; compose service `rdrs` with ports `8000:8000` and volumes for data/logs/config) | **VERIFIED (testing) / BLOCKED (Docker execution)** | Docker daemon unreachable and `docker compose` subcommand unavailable → the guide's "done when" (`docker compose up`) cannot run. See §6. | NO | **NOT eligible** (see §6) |

---

## 3. Phase 1 … Phase 10 evidence matrix

| Phase | Responsibility (Build Guide) | Implementation | Verification command | Evidence | Actual result | Status | Remaining gap | Rewrite | Lock |
|---|---|---|---|---|---|---|---|---|---|
| **Phase 1** | Project setup & entropy detector — config loader, 5 rotating logs, Shannon entropy, 64 KB sampling | `config.py`; `logging.py::initialize_rotating_logs` (`LOG_FILES`); `entropy.py` | `python ptmp/requirements_evidence.py` (§Phase-1) | `REQUIREMENTS_EVIDENCE.txt` | All five logs defined **and populated**: `system.log` 15 767 B, `events.log` 480 203 B, `alerts.log` 70 492 B, `errors.log` 136 B, `audit.log` 36 658 B; config present and parses; entropy covered by FR-03 (3.41 / 7.99) | **VERIFIED** | None | NO | Candidate |
| **Phase 2** | Real-time file monitoring — watchdog, 4 change kinds + timestamps, own event object, running count | `monitoring.py` → `DirectoryMonitor`, `RealtimeFileMonitor`, `FileEvent`, `event_count` | `python ptmp/requirements_evidence.txt` (§FR-2, §Phase-2) | same | `watchdog` used; `FileEvent` normalised; live capture of **all four** actions proven under FR-02 | **VERIFIED** | None | NO | Candidate |
| **Phase 3** | Database & process monitoring — SQLAlchemy/SQLite tables; psutil snapshots (name, CPU %, memory, disk writes, exe, parent); persist as they happen; handle access-denied | `storage.py`; `telemetry.py` (`capture_all` guards `NoSuchProcess/AccessDenied/ZombieProcess`; `from_pid` guards `AccessDenied` per attribute) | `python ptmp/requirements_evidence.py` (§Phase-3) + real 40 s session | `REQUIREMENTS_EVIDENCE.txt`, `PROCESS_TELEMETRY_PERSISTENCE.txt` | Five tables; live db has 261 event rows; `record_process()` write path proven and read back with `pid/name/cpu_percent/memory_percent/executable_path/parent_pid/disk_write_bytes`; **real 40 s monitoring session persisted 381 process rows at t=35 s** | **VERIFIED** | **Measured latency**: the process-monitor cycle is ~30 s (5 s wait + ~25 s capture), so a run shorter than ~35 s persists no process rows. Requirement met; latency risk recorded (§5). | NO | **NOT eligible** (see §5) |
| **Phase 4** | Detection engine — sliding window; files/min, renames, extension changes, avg entropy; compare to config thresholds | `burst_detection.py` | `python ptmp/requirements_evidence.py` (§FR-6) | same | Covered by FR-06: quiet NORMAL, burst exceeds thresholds, window expires after 61 s; all five thresholds read from config | **VERIFIED** | None | NO | Candidate |
| **Phase 5** | Threat scoring & incident response — weights, bands, on-Critical incident + affected files + suspect process + alert/audit logs; copy-only quarantine; simulation mode ON | `burst_detection.py`, `scoring.py`, `response.py`, `quarantine.py` | `python ptmp/requirements_evidence.py` (§FR-7/8/9) | same | Covered by FR-07 (weights/bands/cap), FR-08 (incident + alert with real evidence), FR-09 (copy-only, originals untouched, simulation ON) | **VERIFIED** | None | NO | Candidate |
| **Phase 6** | REST API — GET `/health /status /alerts /events /reports`; POST `/scan /settings`; JSON everywhere | `api.py` | `python ptmp/requirements_evidence.py` (§FR-10) | same | Covered by FR-10: 11 endpoints, no 404s, JSON verified, `/scan` returned 461 real files | **VERIFIED** | None | NO | Candidate |
| **Phase 7** | Dashboard — threat level, recent alerts, recent events, top modified files, CPU/entropy chart, fresh polling, dark theme | `dashboard/index.html` | `python ptmp/requirements_evidence.py` (§FR-11) | same | Covered by FR-11: all required elements present, 5 s polling, dark theme confirmed | **VERIFIED** | None | NO | Candidate |
| **Phase 8** | Reporting — JSON + CSV required; timeline, score, affected files, suspect process, recommendations | `api.py::generate_report` | `python ptmp/requirements_evidence.py` (§FR-12) | same | Covered by FR-12: both formats generated from real stored incident data with all required content | **VERIFIED** | HTML/PDF optional and absent (not a gap) | NO | Candidate |
| **Phase 9** | Testing — unit tests for entropy + scoring; one integration test producing a Critical incident; coverage ≥90 % | `tests/unit/`, `tests/integration/` | `python -m pytest tests -q --cov=rdrs` | `BUGFIX_FINAL_SUITE.txt`, `BUGFIX_COVERAGE.txt` | **448 passed, 0 failed**; **coverage 96.0 %** (guide target ≥90 %); 27 test modules incl. a populated `tests/integration/` | **VERIFIED** | Coverage 96 %, not 100 % (guide asks ≥90 %; met) | NO | Candidate |
| **Phase 10** | Deployment — Dockerfile, docker-compose.yml, README; real container build/run | `Dockerfile`, `docker-compose.yml`, `README.md` | `docker info`; `docker compose version` | `REQUIREMENTS_EVIDENCE.txt` | Artefacts present and valid (compose parses; service `rdrs`; ports `8000:8000`; volumes for data/logs/config; `HEALTHCHECK` present; README documents `docker compose`) | **BLOCKED** | Docker CLI 29.8.0 present but **daemon unreachable** (`npipe:////./pipe/dockerDesktopLinuxEngine` missing) and `docker compose` is an unknown subcommand → `docker compose up` **cannot execute**. | NO | **NOT eligible** (see §6) |

---

## 4. Status roll-up

### Verified (22)
`FR-01, FR-02, FR-03, FR-04, FR-05, FR-06, FR-07, FR-08, FR-09, FR-10, FR-11,
FR-12, FR-13 (testing half), Phase 1, Phase 2, Phase 3, Phase 4, Phase 5,
Phase 6, Phase 7, Phase 8, Phase 9`

### Partial (0 marked PARTIAL — one risk recorded instead)
No requirement is marked PARTIAL, because each requirement's own "done when"
criterion is met. However **one measured limitation** affects the FSD's
non-functional requirement *"real-time monitoring"* for process data — see §5.
That is a quality/performance risk, not a missing requirement, so it is reported
as a risk rather than downgrading FR-04 / FR-05 / Phase 3 to PARTIAL.

### Blocked (1)
`Phase 10` — Docker build/run cannot execute (daemon down, `compose` unavailable).

### Missing evidence (0)
Every claim above is backed by an executed command whose raw output is in
`reports/evidence/`. No requirement is asserted from source inspection alone.

---

## 5. Measured performance limitation (FR-04 / FR-05 / Phase 3)

This was found while gathering evidence and is reported because it is real,
measured, and affects the FSD's *"real-time monitoring"* NFR.

**Measurement** (`reports/evidence/PROCESS_TELEMETRY_PERSISTENCE.txt`):

```
psutil.process_iter() count      : 383 (0.004s)
capture_all() count              : 382
capture_all() duration (s)       : 25.72
per-process average (ms)         : 67.3
single cpu_percent(0.05) (s)     : 0.050   <-- sleeps per process
```

**Cause:** `ProcessTelemetry.from_pid()` calls
`process.cpu_percent(interval=0.05)`. psutil requires an interval for a
meaningful first reading, so this sleeps 50 ms **for every process**, making one
`capture_all()` cost ≈ *N* × 50 ms (≈19 s for 380 processes; measured 26 s
including the other per-process queries).

**Effect:** the runtime's `_process_monitor_loop` waits 5 s then calls
`capture_all()` once per cycle, so the effective telemetry period is ≈30 s. A
monitoring run shorter than ~35 s persists **zero** process rows — which is
exactly what a short verification run produced (`processes=0` in the live db).

**End-to-end proof that the path works** (real 40 s session):

```
t= 35s  process rows = 381
FINAL process rows : 381
sample row: pid=0 name='System Idle Process' cpu=0.0 mem=0.000048 exe='' parent=0 disk_write=0
```

**Assessment.** The Phase 3 "done when" criterion (*"your database contains rows
for file events and process snapshots"*) **is** satisfied, and the guide's
"handle access-denied gracefully" instruction **is** implemented (per-attribute
`AccessDenied` guards plus per-process `NoSuchProcess/AccessDenied/ZombieProcess`
guards in `capture_all`). So the requirement is met.

However a ~30 s process sampling period is not "real-time" in the ordinary sense
of the FSD's §4 NFR, and it makes process evidence lag behind file evidence in
the same incident. **Per Locked Rules §7 this is not a blocking defect** (nothing
documented fails to function), so **no code was changed** — the correct action is
to record it as a risk and flag it for a future, separately-justified change.

**Recommended (not performed):** replace `cpu_percent(interval=0.05)` with the
non-blocking `cpu_percent(interval=None)` sampler (priming on first call, which
is the documented psutil pattern for periodic sampling), and/or move process
capture to a background pool. That would take the cycle from ~30 s to well under
1 s. It requires its own defect evidence and verification cycle before being
applied to a frozen file.

---

## 6. Docker / Phase 10 blockers (reported separately)

```
$ docker --version
Docker version 29.8.0, build 88096ef

$ docker info
failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine;
check if the path is correct and if the daemon is running: open
//./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.

$ docker compose version
docker: unknown command: docker compose
```

- CLI installed; **daemon not running**; `compose` subcommand unavailable.
- Therefore the Build Guide Phase 10 "done when" (`docker compose up` starts the
  system) **was not executed** and **cannot be verified here**.

**Static checks performed instead** (all passing, but *not* a substitute for a
build):

| Check | Result |
|---|---|
| `Dockerfile` present | yes |
| `docker-compose.yml` present | yes |
| compose parses as valid YAML | yes — service `rdrs` |
| port mapping | `8000:8000` |
| volumes | `./data:/app/data`, `./logs:/app/logs`, `./config:/app/config:ro` |
| `EXPOSE 8000` | present |
| `HEALTHCHECK` | present (30 s interval, `/health`) |
| `CMD` | `["uvicorn", "rdrs.api:app", "--host", "0.0.0.0", "--port", "8000"]` |
| README documents `docker compose` | yes |

**Unverifiable risk to flag:** `Dockerfile` begins with a UTF-8 BOM
(`EF BB BF`), matching the project-wide BOM convention on every `.py` file. Most
Docker/BuildKit versions strip a leading BOM, but **this cannot be confirmed
without a daemon**. If a build ever fails with a parse error on the first
instruction, the BOM is the first thing to remove. Recorded as an open risk, not
a defect.

---

## 7. Exact commands executed and results

```bash
# --- workspace / import integrity -------------------------------------------
python -c "import rdrs; print(rdrs.__file__)"
#   -> ...\New folder\RDRS-CLI\src\rdrs\__init__.py
#   13 modules checked, all resolve to the canonical workspace
grep -rn "prefix_tree|backups|_BEFORE" src/rdrs/*.py src/rdrs/dashboard/index.html
#   -> no matches (no active source imports from backups)
cat .venv/Lib/site-packages/__editable__.rdrs-0.1.0.pth
#   -> D:\...\New folder\RDRS-CLI\src

# --- FSD extraction ----------------------------------------------------------
python -c "<zipfile+regex docx extract>"
#   -> ptmp/FSD_EXTRACTED.txt (1,583 chars, 47 lines, FR-1..FR-13 verbatim)

# --- per-requirement evidence ------------------------------------------------
python ptmp/requirements_evidence.py
#   -> reports/evidence/REQUIREMENTS_EVIDENCE.txt
#   -> VERIFIED=22  BLOCKED=1  FAILED=0   (exit 0)

# --- process telemetry cost + end-to-end persistence -------------------------
python -c "<capture_all timing + 40s real monitoring session>"
#   -> reports/evidence/PROCESS_TELEMETRY_PERSISTENCE.txt
#   -> capture_all() 25.72s / 382 procs; 381 process rows at t=35s

# --- regression + scans (required after changes; no source changed) ----------
python -m pytest tests -q --no-header -p no:cacheprovider
#   -> 448 passed, 8 warnings in 28.43s
node ptmp/dashboard_frontend_harness.mjs
#   -> 32 passed, 0 failed
python ptmp/encoding_safety_scan.py
#   -> ALL 22 CHECKS PASSED

# --- Phase 9 coverage --------------------------------------------------------
python -m pytest tests -q --cov=rdrs --cov-report=term
#   -> 448 passed; TOTAL 2805 stmts, 111 miss, 96%

# --- Docker limitation -------------------------------------------------------
docker --version           # Docker version 29.8.0
docker info                # daemon unreachable
docker compose version     # unknown command: docker compose
```

**Key raw results**

| Check | Result |
|---|---|
| `python ptmp/requirements_evidence.py` | `VERIFIED=22 BLOCKED=1 FAILED=0` (exit 0) |
| `python -m pytest tests -q` | `448 passed, 8 warnings in 28.43s` |
| `node ptmp/dashboard_frontend_harness.mjs` | `32 passed, 0 failed` |
| `python ptmp/encoding_safety_scan.py` | `ALL 22 CHECKS PASSED` |
| coverage | `96%` (guide target ≥90%) |
| Docker daemon | unreachable |

---

## 8. Files modified

**Source files modified in this task: NONE.**

Verified by modification time — the newest file under `src/` is
`src/rdrs/dashboard/index.html` at **19:25**, which predates this task (started
**20:14**). All three source files touched during the earlier B-01…B-07 cycle
(`burst_detection.py` 19:06, `runtime.py` 19:11, `dashboard/index.html` 19:25)
are unchanged by this work.

**Files created by this task (evidence only, no runtime effect):**

| File | Purpose |
|---|---|
| `ptmp/FSD_EXTRACTED.txt` | verbatim text of the FSD, for source-grounded mapping |
| `ptmp/requirements_evidence.py` | the per-requirement evidence harness |
| `reports/evidence/REQUIREMENTS_EVIDENCE.txt` | full raw harness output |
| `reports/evidence/PROCESS_TELEMETRY_PERSISTENCE.txt` | process-telemetry cost + end-to-end persistence proof |
| `reports/RDRS_REQUIREMENTS_EVIDENCE_MATRIX.md` | this document |

**Preserved untouched:** `config/rdrs.yaml`, `pyproject.toml`, `Dockerfile`,
`docker-compose.yml`, all of `src/rdrs/` (23 modules), `tests/`, and the
backups under `reports/backups/`.

---

## 9. Confirmation: verified code was not unnecessarily rewritten

- **No source file was edited in this task.** The only writes were new evidence
  harnesses and evidence text files.
- Locked Rules **§7** (do not rebuild/rewrite verified Phase 1–6 work) and
  **§6** (no later rewrite of frozen files) are respected: the B-01…B-07 fixes
  verified in the previous cycle are byte-identical.
- Locked Rules **§10** (do not change locked files unless the Rules permit) is
  respected. The one genuine issue found (process-telemetry latency) was **not**
  patched, because §7 permits modification of a frozen file *only* for a genuine
  blocking defect that prevents documented requirements from functioning — and
  the Phase 3 requirement **does** function. It is recorded as a risk instead.
- The three authoritative documents were used as the sole source of requirement
  text; the rejected 125-feature spec and its audit rules were **not** used
  (Locked Rules §1).

---

## 10. Lock eligibility

Locked Rules **§14** requires, before a file is marked
`100/100 — VERIFIED — FROZEN`: complete responsibility checked, FR-01→FR-13
impact checked, Phase 1→Phase 10 future integration checked, **no mandatory
future rewrite expected**, compiled/linted, tests run, real behaviour verified.

| Group | Lock eligibility | Reason |
|---|---|---|
| `entropy.py`, `config.py`, `logging.py` | **Candidate** | Requirement verified; no known gap or rewrite |
| `monitoring.py`, `storage.py`, `dashboard_data.py`, `api.py`, `dashboard/index.html` | **Candidate** | Requirement verified; no known gap or rewrite |
| `burst_detection.py`, `scoring.py`, `response.py`, `quarantine.py` | **Candidate** | Verified (incl. the B-01/B-02 fixes); no known gap or rewrite |
| `telemetry.py` | **NOT eligible** | Measured ~30 s capture cycle conflicts with the FSD's "real-time monitoring" NFR; a future change is foreseeable, so §14's "no mandatory future rewrite" cannot be affirmed |
| Project as a whole | **NOT eligible** | Phase 10 build/run is unexecuted (BLOCKED BY ENVIRONMENT); §16 requires Phase 1→10 COMPLETE |

**No file is marked FROZEN. The project is not marked 100/100, FROZEN or
LOCKED.** "Candidate" means the evidence supports freezing that file *once* the
project-level Phase 10 gate is resolved — it is not a freeze declaration.

---

## 11. Next action (evidence-based)

1. **Resolve the Docker gate so Phase 10 can be verified.** Start Docker Desktop
   (or provide a reachable daemon) and run `docker compose up --build`, then
   confirm `/health` responds from inside the container. Until then Phase 10
   stays BLOCKED and the project cannot be frozen.
2. **While the daemon is up, also confirm the `Dockerfile` BOM does not break
   the build** (§6). If it does, removing the leading BOM is the fix.
3. **Then address the process-telemetry latency** (§5) as its own
   evidence-backed change: switch to non-blocking `cpu_percent(interval=None)`,
   re-run the Phase 3 end-to-end proof, and confirm the cycle drops below ~1 s.
   This is the only known item that would prevent `telemetry.py` from being
   frozen.
4. **Only after 1–3:** re-run the full evidence harness and the regression,
   front-end, E2E and scan layers, then consider freezing file-by-file under
   §14. Do not freeze on the strength of this matrix alone.

Nothing in this report should be read as a completion claim beyond the evidence
shown. 22 requirements are VERIFIED against executed commands; 1 is BLOCKED; and
1 measured performance limitation is recorded and deliberately left unfixed.
