# RDRS — SUBMISSION STATUS REPORT

**Date:** 20 September 2026
**Scope:** submission preparation. **No source, configuration, test, report or lock record was modified.**
**Config at the time of writing:** `shutdown_timeout: 20.0` · `watch_paths: ['data']` · `sha256 886eebd4663beec6…`

---

## 1. PROJECT STATE

| Item | Value |
|---|---|
| Package | `rdrs` v0.1.0 |
| Layout | `src/rdrs/` (production) · `tests/` (suite) · `reports/` (evidence) |
| Console entry point | `rdrs = "rdrs.cli:main"` |
| Root README | `README.md` (pre-existing, comprehensive) |
| Docs | `docs/` (2 PDFs + 1 MD) |
| Docker | `Dockerfile` + `docker-compose.yml` present — **Phase 10 excluded by instruction, not exercised** |

### Application health at the time of writing

```
state: RUNNING   healthy: True
subsystems: config, logging, database, detection, response, file_monitor, process_monitor
shutdown_timeout: 20.0
```

| Endpoint | Status |
|---|---|
| `/` `/health` `/status` `/events` `/processes` | 200 |
| `/alerts` `/incidents` `/reports` `/dashboard-data` `/docs` | 200 |

**The application starts and serves correctly.**

### Settings fix confirmed present

| Marker in `src/rdrs/dashboard/index.html` | Occurrences |
|---|---|
| `dirtySettings` | 7 |
| `wireSettingsFieldTracking` | 2 |
| `markSettingsPolled` | 2 |
| `settingsHaveUnsavedEdits` | 2 |
| Old polling call `await loadSettings(status, false)` | **0** |

---

## 2. START / DEMO COMMANDS

All commands run from the project root:

```
D:\Samyak\Projects\Internmo\Project 1 RDRS\RDRS\New folder\RDRS-CLI
```

### Install

```bash
pip install -r requirements.txt
```

### Start the API + dashboard (the demo entry point)

```bash
uvicorn rdrs.api:app --host 127.0.0.1 --port 8000
```

* **Dashboard:** http://127.0.0.1:8000/
* **Interactive API docs:** http://127.0.0.1:8000/docs

> The repository's `README.md` shows `--host 0.0.0.0`. `127.0.0.1` is recommended
> for the demo: RDRS is a local defensive tool and binds to loopback by default.

### CLI

```bash
python -m rdrs.cli status     # print state + subsystem health
python -m rdrs.cli start      # start monitoring
python -m rdrs.cli stop       # stop monitoring (idempotent)
python -m rdrs.cli watch      # live event count; Ctrl+C to stop
python -m rdrs.cli watch --path <folder> --interval 1 --duration 30
```

### Suggested demo flow

1. `uvicorn rdrs.api:app --host 127.0.0.1 --port 8000`
2. Open http://127.0.0.1:8000/ and press **Start Monitoring**.
3. Create rapid file activity inside the watched folder (`data/`):
   write **more than 20 distinct files** in under a minute, then rename them to
   a different extension (e.g. `.locked`).
   > The `rapid_encryption` signal needs >20 distinct modified files per minute.
   > Fewer than 20 reaches WARNING (≈55); 30 reaches CRITICAL (≈95).
4. Watch the dashboard: **File Events** climb, **Alerts** appear, a **CRITICAL
   Incident** is created with a suspect process and affected files.
5. On the **Reports** page press **JSON** / **CSV** to generate, then download.
6. Open **Settings**, change **Shutdown Timeout**, press **Save Settings**, wait
   through a poll cycle, and confirm the value holds.

---

## 3. VERIFIED FEATURES

### Verification executed after the Settings fix

| Layer | Command | Result |
|---|---|---|
| **Real browser (Chrome via CDP)** | `python ptmp/verify_settings_browser.py` | **10 passed, 0 failed** |
| **Front-end harness** | `node ptmp/dashboard_frontend_harness.mjs` | **72 passed, 0 failed** |
| **Encoding + safety scan** | `python ptmp/encoding_safety_scan.py` | **22 passed, 0 failed** |

**Browser verification (real Chrome, desktop 1600 px and mobile 390 px):**

| Check | Result |
|---|---|
| Settings page opens showing the backend value (15) | PASS |
| Operator types 20 | PASS |
| **Edit survives 12 s of background polling (2+ cycles)** | **PASS** |
| Page reports the edit as unsaved | PASS |
| Save reports success | PASS |
| Field holds the saved value | PASS |
| Survives further polling | PASS |
| Survives a full page reload | PASS |
| Survives navigation away and back | PASS |
| Desktop + mobile screenshots captured | PASS |

**Settings behaviour in both runtime states:**

| State | Result |
|---|---|
| STOPPED | Editable; 15 → 20 saved and persisted across polling, reload and navigation |
| RUNNING | `POST /settings` → 200; no unnecessary blocking |

**Encoding + safety scan** confirmed the no-weakening guards: detection thresholds
and scoring weights **unchanged** (`rapid_encryption 40 / mass_rename 30 /
high_entropy 25 / cpu_spike 15 / unknown_program 10`).

### Feature summary (FR-01 … FR-13)

Implemented per the project's own README and requirement matrix:

| FR | Area | Status |
|---|---|---|
| FR-01 | Configuration loading & validation | Implemented |
| FR-02 | Real-time file monitoring (create/modify/rename/delete) | Implemented |
| FR-03 | Shannon entropy analysis | Implemented |
| FR-04 | Process telemetry (`psutil`) | Implemented |
| FR-05 | SQLite persistence | Implemented |
| FR-06 | Sliding-window burst detection | Implemented |
| FR-07 | Weighted 0–100 threat scoring | Implemented |
| FR-08 | Alerts & incident records | Implemented |
| FR-09 | Evidence quarantine (copy-only) | Implemented |
| FR-10 | REST API | Implemented |
| FR-11 | Web dashboard | Implemented |
| FR-12 | Report generation (JSON/CSV) | Implemented |
| FR-13 | Logging & audit trail | Implemented |

> **This table reflects the project's existing requirement documentation.** It is
> **not** a fresh 100/100 re-verification — see §4.

---

## 4. HONESTLY UNVERIFIED

Stated plainly so nothing is over-claimed:

| Item | Position |
|---|---|
| **Backend pytest suite** | **NOT re-run after the Settings change.** Last confirmed count is **739 passed**, taken *before* the dashboard-only edit. The change is frontend-only, so the suite is not expected to be affected — but that is an expectation, **not a measurement**. It could not be re-run: the sandbox denied it twice because pytest's own temp-directory garbage collection trips the bulk-delete guard (`...\Temp\pytest-of-SAMYAK\garbage-<uuid>`). |
| **E2E script (30 checks)** | **NOT run.** It attempts sandbox file deletions, which were denied. Not a product defect. |
| **Browser console errors** | **Not captured.** A CDP client is now available, but console capture was not wired. |
| **FR-01…FR-13 / Phase 1…Phase 9 lock status** | **Carried over, not freshly re-verified** in this cycle. |
| **Phase 10 / Docker** | Excluded by instruction. Not started, not modified, not exercised. |
| **Test-count reconciliation** | Backend **739** (pre-change, unverified this cycle) · harness **72** (post-change, verified) · encoding **22** (post-change, verified) · browser **10** (post-change, verified). |

---

## 5. LOCK GATE — POSITION

**No file, feature, FR or Phase is claimed as `100/100`, `FROZEN`, or
`PERMANENTLY LOCKED` on the basis of this cycle's evidence.**

| Item | Position |
|---|---|
| `src/rdrs/dashboard/index.html` | Modified for the Settings fix. Passes harness 72/72 and browser 10/10, **but the backend suite has not been re-run since the change** — so it is **not** re-locked on fresh evidence |
| Backend source files | **Unchanged** by this cycle |
| FR-01…FR-13 / Phase 1…Phase 9 | Previously reported 100/100; **not re-verified this cycle** |
| Phase 10 / Docker | Not started, not modified |

Re-locking the dashboard file requires the backend suite to pass first.

---

## 6. KNOWN LIMITATIONS

1. **Backend suite re-run blocked by the sandbox delete guard.** Resolvable by
   approving pytest's temp path or using a different `--basetemp`.
2. **`persistence.enabled: false`** in the current configuration. The runtime
   then uses a non-durable **in-memory** database: monitoring, detection, scoring
   and incidents all work, but **data does not survive a process restart**. It
   *does* survive a stop/start within the same process. Set **Persistence =
   Enabled** in Settings for durable storage.
3. **`shutdown_timeout` is 20.0**, changed from the original 15 by the Settings
   verification itself. A pre-change backup exists at
   `reports/backups/config/config.yaml.pre-settings-fix-20260920` (value 15).
   Nothing was restored.
4. **`websockets`** was added to the project venv to drive Chrome over CDP for
   the browser verification.
5. **`cli.py` and the browser test** are unaffected by, but worth knowing:
   the CLI now honours the configuration's `data_dir`/`logs_dir`/`db_path`
   rather than pinning them to the project root.

---

## 7. SUBMISSION READINESS

**The Settings persistence defect is fixed and verified end to end in a real
browser.** The application starts, serves all endpoints, and the dashboard
demonstrates the full detection pipeline.

**Ready for submission on the verified scope**, with one honest caveat: the
backend pytest suite has not been re-run since the dashboard change, so the
backend result is a carried-over **739 passed** rather than a fresh measurement.
Re-running it is the single remaining step needed before any lock claim.
