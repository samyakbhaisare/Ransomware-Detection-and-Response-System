# RDRS — LOCAL UI VERIFICATION REPORT

**Date:** 2026-09-18
**Mode:** verification only — **no source file was modified**
**Docker:** not started (per instruction)

---

## 1. Backend startup result

| Item | Result |
|---|---|
| Project root | `D:\Samyak\Projects\Internmo\Project 1 RDRS\RDRS\New folder\RDRS-CLI` |
| Interpreter | `.venv\Scripts\python.exe` — Python 3.14.3 |
| Import target | `rdrs` resolves to `<root>\src\rdrs\__init__.py` (canonical) |
| uvicorn / fastapi | 0.52.4 / 0.141.1 |
| Command | `python -m uvicorn rdrs.api:app --host 127.0.0.1 --port 8000` |
| Startup | **SUCCESS** — "Application startup complete", runtime state `RUNNING` |
| Subsystems | config, logging, database, detection, response, file_monitor, process_monitor — all registered |
| Process monitor | primed 365 processes at start |
| Run duration | 7 m 12 s, stopped cleanly at the end of verification |

**UI URL:** `http://127.0.0.1:8000/` (API docs at `/docs`)

## 2. Routes tested

| Route | Method | Status | Content-Type |
|---|---|---|---|
| `/` | GET | **200** | `text/html; charset=utf-8` |
| `/health` | GET | **200** | `application/json` |
| `/status` | GET | **200** | `application/json` |
| `/events` | GET | **200** | `application/json` |
| `/processes` | GET | **200** | `application/json` |
| `/alerts` | GET | **200** | `application/json` |
| `/incidents` | GET | **200** | `application/json` |
| `/reports` | GET | **200** | `application/json` |
| `/dashboard-data` | GET | **200** | `application/json` |
| `/docs` | GET | **200** | HTML |
| `/openapi.json` | GET | **200** | JSON |
| `/redoc` | GET | **200** | HTML |
| `/scan?path=data` | POST | **200** | JSON |
| `/settings` | POST | **200** | JSON |
| `/reports/generate?format=json&incident_id=1` | POST | **200** | JSON |
| `/reports/generate?format=csv&incident_id=1` | POST | **200** | JSON |
| `/reports/1/json` | GET | **200** | `application/json` |
| `/reports/1/csv` | GET | **200** | `text/csv; charset=utf-8` |
| `/nope` | GET | **404** | (correct) |
| `/` | HEAD | **405** | Method Not Allowed — correct for a GET-only route |

**No 5xx responses occurred at any point.** No 4xx other than the deliberate
404 probe and the 405 HEAD.

## 3. UI verification (real browser)

Browser automation was performed with **headless Chrome** (`chrome.exe`, real
rendering, real JS execution, real network calls) — not a simulated DOM.

### 3.1 Dashboard loading — PASS

`dashboard.png` (1600×1100) shows the fully rendered dashboard: sidebar
(Dashboard, Events, Processes, Alerts, Incidents, Reports, Settings), header
with Start/Stop/Refresh controls, "RDRS monitoring RUNNING" with a green
indicator, and "Last refreshed 22:20:15".

### 3.2 Summary counters — PASS (live values from the API)

| Element | Rendered value |
|---|---|
| Threat Score | `70.0` |
| Threat Level | `CRITICAL` (red) |
| File Events | `270` |
| Alerts | `2` |
| Processes | `11960` → `14301` → **`18590`** across successive renders |
| Incidents | `1` |
| Score Reason | "Ransomware-like burst detected; signals: rapid_encryption, mass_rename, extension_change" |
| Last Score Update | `18 Sept 2026, 9:58:24 pm` |

The **process count rising between renders is the live monitor working** — not a
static value.

### 3.3 Live event count — PASS

`File Events` = 270, and `dashboardEvents` rendered **50 rows** with real paths
and timestamps. The `[events: N]` CLI counter was verified in the FR-02 work.

### 3.4 Process monitoring data — PASS

`dashboardProcesses` rendered **60 rows**; `processPageTable` columns are
`PID | Name | CPU % | Memory % | Disk Writes | Executable | Parent` — all six
guide-required fields. Live API sample: `svchost.exe`, `memory_percent`
0.0905, `disk_write_bytes` 102400, `executable_path`
`C:\Windows\System32\svchost.exe`, `parent_pid` 1540.

### 3.5 Events table, timestamps and encoding — PASS

| Table | Columns | Rows rendered |
|---|---|---|
| `dashboardEvents` | Action, Path, **Time** | 50 |
| `dashboardAlerts` | Severity, Message, **Time** | 2 |
| `eventsPageTable` | Action, Path, File Type, Entropy, **Time** | (loads on page open) |
| `alertsPageTable` | Severity, Message, Score, **Time** | (loads on page open) |
| `processPageTable` | PID, Name, CPU %, Memory %, Disk Writes, Executable, Parent | (loads on page open) |
| `incidentsPageTable` | ID, Severity, Status, Score, **Time** | 1 |

- **54 formatted timestamps** rendered, e.g. `18 Sept 2026, 9:58:24 pm`
- **No mojibake** (`Ã©`, `â€™`, `�`, `Â` all absent)
- `<meta charset="UTF-8">` present; response header `charset=utf-8`
- Em-dash placeholder `—` renders correctly

### 3.6 Dashboard refresh / polling — PASS

`setInterval(…, 5000)`; `lastRefreshed` advanced between renders
(`22:20:15` → `22:21:09` → `22:22:33`), and counters changed, so the page is
genuinely self-updating.

### 3.7 Dark theme and responsive layout — PASS

- Dark theme applied: `:root { --bg: #080c11 }`; screenshots confirm dark
  surfaces with light text.
- **Responsive:** at a 900 px viewport (`dashboard_narrow.png`) the card grid
  reflows to two columns, the sidebar persists, and all text stays legible.

### 3.8 Report generation and download — PASS

Generate JSON → 200, CSV → 200. Download `/reports/1/json` → 200
`application/json`; `/reports/1/csv` → 200 `text/csv; charset=utf-8`.

### 3.9 Browser console errors — **NOT CAPTURED (capability limitation)**

This could **not** be verified directly, and I am not going to claim otherwise:

- The bundled `agent-browser` plugin's own README states **"macOS or Linux
  (Windows not supported)"** — this host is Windows.
- Neither `playwright` (Python or Node) nor a websocket client
  (`websocket-client`, `websockets`) is installed, so Chrome DevTools Protocol
  could not be driven to subscribe to `Runtime.consoleAPICalled`.
- Chrome's `--enable-logging=stderr` produced **0 console lines** for the page.

**What I verified instead:** the rendered DOM (122,299 chars) is fully populated
with live API data — `CRITICAL`, `70.0`, `270`, `2`, `14301`, `1`, 54 timestamps,
50 event rows. A JavaScript exception during the refresh cycle would leave those
elements at their initial/empty state. So the script **demonstrably ran to
completion**, but that is an inference from the DOM, **not** a captured console
log. A real console check needs a browser-automation tool on a supported OS.

## 4. Defects found

### DEFECT 1 — `POST /settings` silently destroys `watch_paths` (HIGH)

**Severity:** high — silent loss of monitored folders (a protection regression).

**Reproduced live.** `POST /settings` with `{"log_level":"INFO"}` returned 200,
and the on-disk `config.yaml` afterwards had:

- `watch_paths` key **removed**
- **all comments stripped**

**Root cause:** `RuntimeConfig.save()` writes `self.to_config_dict()`, and
`to_config_dict()` (`src/rdrs/config.py:496-529`) renders the `runtime` section
with only `name, environment, data_dir, logs_dir, shutdown_timeout` — it **omits
`watch_paths`**. `to_dict()` (used by the API response) *does* include it, so
the API reports a setting the saved file loses.

**Measured impact** (temp config with `watch_paths: [data, logs]`):

```
before save : watch_paths ['data','logs']  -> monitored ['data','logs']
after  save : watch_paths None             -> comments gone
after reload: watch_paths []               -> monitored ['data']
CONFIGURED FOLDERS LOST: True
```

So after any settings save, a restart silently stops monitoring the user's
configured folders and falls back to `data_dir`. Comments are destroyed by
`yaml.safe_dump` (expected for a round-trip save, but the data loss is not).

**Affected file:** `src/rdrs/config.py` (line 496-529) — currently **permanently
locked**, so per your rules I have **not** changed it.

**Also observed (not a defect):** `POST /settings` returning 422 in my first
probe was **my own curl quoting**, not the API — a file-based payload returned
200.

### DEFECT 2 — verification damaged `config.yaml` (my own action, repaired)

My `POST /settings` probe persisted to the **live** `config.yaml`, triggering
Defect 1 against the shipped file. I restored `config.yaml` to its locked
content (sha256 `ca4afe3f8ec171b5`), verified `watch_paths` and comments are
back and that every locked value still matches
`reports/backups/config/rdrs.yaml.original`. **No source file was touched.**

**Note for the record:** this is the *second* time a settings write has damaged
the shipped config. The `tests/conftest.py` guard protects the **test suite**,
but nothing protects a **live `POST /settings` call**.

## 5. Items checked and found correct (not defects)

| Observation | Verdict |
|---|---|
| `HEAD /` → 405 | Correct: the route is GET-only; browsers use GET |
| `/scores` → 404 | Not a Phase 6 endpoint; nothing calls it |
| Page-level tables show 1 empty row on first load | Expected: those pages load on click; their APIs return 200 |
| `charset` "not found" in my first check | My check was case-sensitive; `<meta charset="UTF-8">` is present |
| Table headers appearing misaligned | My regex matched the wrong `<thead>`; correct pairing shows every table has a **Time** column |

## 6. Console / API errors

- **API 5xx:** none observed across every route and page load.
- **API 4xx:** only the deliberate `/nope` (404) and `HEAD /` (405) probes.
- **Browser console:** not captured — see §3.9. Not reported as "clean".

## 7. Screenshots

| File | Viewport | Size |
|---|---|---|
| `ptmp/ui_shots/dashboard.png` | 1600×1100 | 104,047 bytes |
| `ptmp/ui_shots/dashboard2.png` | 1600×1100 (console capture run) | 103,482 bytes |
| `ptmp/ui_shots/dashboard_narrow.png` | 900×1000 (responsive) | 59,049 bytes |

All are genuine PNGs written by headless Chrome. No image was generated or
altered by me.

## 8. Is the application ready for manual review?

**Yes for manual review — with one caveat to be aware of before you exercise
Settings.**

Working and verified live: backend startup, all 18 routes, dashboard rendering,
live counters, live event count, process telemetry, tables, timestamps, UTF-8,
self-refreshing polling, dark theme, responsive layout, report generation and
download.

**Caveat:** do **not** press Save on the Settings page (or call
`POST /settings`) until Defect 1 is fixed — it will strip `watch_paths` and the
comments from `config.yaml`, and a restart will then monitor only `data_dir`.
If you do trigger it, `config.yaml` can be restored from the content in
`reports/backups/config/rdrs.yaml.original` plus the `watch_paths` block.

**Not verified:** browser console cleanliness (no tooling on Windows).

## 9. What I did not do

- Did not modify, rewrite, unlock or downgrade any locked file.
- Did not start Docker.
- Did not fix Defect 1 — awaiting your approval.
- Did not invent any result; the one thing I could not measure is stated as
  not measured.
