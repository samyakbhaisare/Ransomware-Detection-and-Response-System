# RDRS — REFRESH & DATA-CONSISTENCY VERIFICATION (ALL PAGES)

**Date:** 2026-09-18 · **Mode:** verification only — **no source file modified**, **no mutating request issued by me**
**Server:** `uvicorn rdrs.api:app` on `http://127.0.0.1:8000` (runtime RUNNING, healthy, 7/7 subsystems)

> **Note on the proxy.** A proxy is set in this shell
> (`http_proxy=http://127.0.0.1:56794`). A plain `curl http://127.0.0.1:8000/...`
> returns **502** — the proxy's error, not the app's. All checks below used
> `curl --noproxy '*'`.

---

## 1. Refresh behaviour — measured, not inferred

The dashboard's own `<script>` was executed in a Node VM with an instrumented
`fetch`, then for each page: switch to the page → call `refreshAll(true)`
(exactly what the Refresh button does) → record the request sequence and the
message shown.

| Page | Endpoints called (in order) | Message shown | Last refreshed |
|---|---|---|---|
| Dashboard | `/status`, `/dashboard-data` | `Dashboard refreshed.` | updated |
| Events | `/status`, `/dashboard-data`, `/events?limit=200` | `Dashboard refreshed.` | updated |
| Processes | `/status`, `/dashboard-data`, `/processes?limit=200` | `Dashboard refreshed.` | updated |
| Alerts | `/status`, `/dashboard-data`, `/alerts?limit=200` | `Dashboard refreshed.` | updated |
| Incidents | `/status`, `/dashboard-data`, `/incidents?limit=200` | `Dashboard refreshed.` | updated |
| Reports | `/status`, `/dashboard-data`, `/reports?limit=200` | `Dashboard refreshed.` | updated |
| Settings | `/status`, `/dashboard-data`, **`/status` (again)** | `Dashboard refreshed.` | updated |

**Answers to your specific questions**

| Question | Answer |
|---|---|
| Which endpoints are called? | Always `/status` + `/dashboard-data`, then one page-specific endpoint (Settings re-calls `/status`) |
| Does the current page's data refresh? | **Yes** — `refreshCurrentPage(page)` dispatches to the right loader |
| Does it wrongly say "Dashboard refreshed"? | **Yes, on all 7 pages** — see Defect 1 |
| Does it navigate to another page? | **No** — the active page is unchanged |
| New calculation or reload only? | **Reload only.** Two consecutive refreshes returned the same score (70.0); all endpoints are GETs. The score is computed by the detection engine in the background, not by Refresh |
| Does the timestamp update? | **Yes** — `Last refreshed HH:MM:SS` advances |
| Duplicate requests / side effects? | **Yes on Settings** (`/status` twice) — Defect 2. No other duplicates. An overlap guard skips a second refresh while one is in flight |

---

## 2. Timestamp consistency

| Timestamp | Value | Age | Verdict |
|---|---|---|---|
| System clock (local / UTC) | 2026-09-18 22:47:53 IST / 17:17:53 UTC | — | reference |
| Dashboard "Last refreshed" | 22:47:32 | ~20 s | **current** ✓ |
| "Last Score Update" (= `score.created_at`) | 2026-09-18T16:28:24Z | 49.5 min | **intentionally historical** (the burst) ✓ |
| Latest event | 2026-09-18T16:28:28Z | 49.4 min | historical (same burst) ✓ |
| Latest process snapshot | 2026-09-18T17:17:50Z | **3 s** | **live** ✓ |
| Latest alert | 2026-09-18T16:28:24Z | 49.5 min | historical ✓ |
| Latest incident | 2026-09-18T16:28:24Z | 49.5 min | historical ✓ |
| Report file names | `incident_1_20260918T165230Z.json/.csv` | 49 min | matches the incident ✓ |

All values are **mutually consistent**: the burst happened once at 21:58 IST,
producing one score, two alerts, one incident and 270 events; the process
monitor keeps writing live. Nothing is stale or mislabelled.

`process_count` grew 24546 → 65046 during the session (cumulative snapshot rows,
not unique processes — see §5).

---

## 3. Dashboard page

| Element | Rendered | Verdict |
|---|---|---|
| Threat Score / Level | 70.0 / CRITICAL | ✓ real |
| File Events / Alerts / Processes / Incidents | 270 / 2 / 24546 / 1 | ✓ real |
| Score Reason | "Ransomware-like burst detected; signals: rapid_encryption, mass_rename, extension_change" | ✓ derived from real signals |
| CPU History | real series with two spikes | ✓ |
| Entropy History | real series | ✓ |
| Recent File Events | 50 rows, Action + Path + Time | ✓ |
| Recent Alerts | 2 rows, Severity + Message + Time | ✓ |
| Live monitoring status | "RDRS monitoring RUNNING", green dot | ✓ |

---

## 4. Events page

Columns: `Action | Path | File Type | Entropy | Time` — all five present.
Ordering newest-first (API `order_by(created_at.desc())`). Timestamps render as
`18 Sept 2026, 9:58:28 pm`. 54 formatted timestamps rendered across the page; no
mojibake. Real-time updates arrive via the 5 s poll (verified: `lastRefreshed`
advanced and counters changed between renders).

## 5. Processes page

Columns: `PID | Name | CPU % | Memory % | Disk Writes | Executable | Parent` —
all six guide fields.

**"Process count" meaning:** `process_count` is the **cumulative number of
snapshot rows** in the `processes` table (one row per process per capture
cycle), not the number of currently-running unique processes. It therefore grows
indefinitely while the monitor runs (24546 → 65046 in one session). The
dashboard's "Processes" card shows this cumulative figure, which can be
confusing — worth noting, though it is a label/interpretation question rather
than a functional defect.

## 6. Alerts and incidents

- 2 alerts (CRITICAL 70/100, WARNING 40/100), 1 incident (CRITICAL, 70.0, open).
- No duplicate or stale records: one burst produced exactly one incident and two
  alerts.
- Refresh reloads both correctly.

## 7. Reports

- `/reports` → count 1, incident 1 with `threat_score` 70.0 and the real signal
  list.
- Generation and download verified in the previous report (JSON 200
  `application/json`; CSV 200 `text/csv; charset=utf-8`).
- **33 report files** exist in `data/reports/` — accumulated from repeated
  verification runs, including many duplicates of the same incident
  (`incident_1_20260918T165230Z.json`, `…165231Z.json`, `…172025Z.json`). Each
  generation writes a new timestamped file; nothing prunes them. Not a defect,
  but the directory grows without bound.

## 8. Settings (read-only inspection)

The five form fields were compared against `config.yaml` — **all five match**:

| Form field | config.yaml | `/status` |
|---|---|---|
| Name | `rdrs` | `rdrs` |
| Environment | `test` | `test` |
| Log level | `INFO` | `INFO` |
| Shutdown timeout | `15.0` | `15.0` |
| Persistence | `true` | `true` |

**Not surfaced in the form** (though exposed by `/status`): `data_dir`,
`logs_dir`, **`watch_paths`**, `db_path`, `detection_thresholds`,
`scoring_weights`. In particular the watch folders — the primary protection
setting — cannot be seen or edited in the UI.

**No POST was issued.** `/settings` was never called during this verification.

## 9. Responsive UI

| Width | Result |
|---|---|
| 1600 px (desktop) | ✓ full layout, all cards, charts, sidebar |
| 900 px (tablet) | ✓ cards reflow to 2 columns |
| 650 px | ✓ controls fit on one row |
| 560 px | ✓ controls fit |
| 430 px | ✓ fits, but the Refresh button ends flush at the viewport edge |
| **390 px (mobile)** | ✗ **the Refresh button and the "Last refreshed" label are clipped** — see Defect 3 |

Nav becomes a 2-column grid and the sidebar stacks above the content at ≤650 px,
which works correctly.

## 10. Frontend / API

- **API mappings:** all 13 paths in the dashboard's `API` map exist as real
  routes. No dead control.
- **Generic messages:** `Dashboard refreshed.` is hard-coded (Defect 1).
- **4xx/5xx:** none from the app during this verification. The only 4xx observed
  was `GET /favicon.ico → 404` (no favicon shipped — cosmetic).
- **Browser console:** **not captured.** The `agent-browser` plugin states
  "macOS or Linux (Windows not supported)"; no Playwright/CDP client is
  installed, and Chrome's `--enable-logging` yields 0 console lines. The JS
  demonstrably ran (fully populated DOM), but that is an inference, not a
  captured console log. **Not reported as clean.**

---

## 11. Genuine defects

| # | Severity | Defect | File / location |
|---|---|---|---|
| **1** | Low (cosmetic) | Refresh always says **"Dashboard refreshed."**, even on Events / Processes / Alerts / Incidents / Reports / Settings | `dashboard/index.html`, `refreshAll()` — `showMessage("Dashboard refreshed.", "success")` |
| **2** | Low | On the Settings page, Refresh issues **`/status` twice** (`refreshAll` calls it, then `loadSettings()` calls it again) | `refreshAll()` + `loadSettings()` |
| **3** | Medium | At **390 px** the header controls and "Last refreshed" **overflow and are clipped**; `.main { overflow-x: hidden }` hides the overflow instead of wrapping | `dashboard/index.html` `.main` / `.topbar` / `.controls` |
| **4** | **High** | **`POST /settings` strips `watch_paths` and all comments from `config.yaml`** (found in the previous report; re-confirmed) | `config.py` `to_config_dict()` omits `watch_paths` |
| **5** | **High (new)** | **`POST /settings` also writes `persistence.enabled: false`** whenever the form's select is empty, because `saveSettings` computes `form.get("persistence_enabled") === "true"` — `""` (which `loadSettings` sets when the config is unusable) becomes `false`. Observed: the file went from `enabled: true` to `enabled: false` | `dashboard/index.html` `saveSettings()` + `loadSettings()` |
| **6** | Trivial | `GET /favicon.ico` → 404 | no favicon asset |

### On the config damage during this session — stated honestly

`config.yaml` was rewritten at **22:51:02**, changing `persistence.enabled` to
`false` and stripping `watch_paths` and the comments. The access log for that
run shows **3 × `POST /settings` → 200**.

**I could not attribute those POSTs to a specific action.** I ran only GET
requests, and a controlled re-test — fresh log, restore config, load the
dashboard **once** in headless Chrome — produced **only GETs** (`/status`,
`/dashboard-data`, `/`) with **no `POST /settings`**. So the dashboard does
**not** auto-save settings; I am reporting the observation without inventing an
explanation for it.

**I restored `config.yaml` to its locked content** (sha256 `ca4afe3f8ec171b5`):
`persistence.enabled: true`, `watch_paths: ['data']`, comments present, and every
runtime/detection/scoring/logging value matching
`reports/backups/config/rdrs.yaml.original`. No source file was touched.

This is now the **third** time a settings write has damaged the shipped config.

---

## 12. False positives / expected behaviour (checked and cleared)

| Observation | Verdict |
|---|---|
| `/favicon.ico` 404 | Cosmetic only; no favicon shipped |
| Process count 24546 → 65046 | Cumulative snapshot rows, not unique processes — grows by design |
| Score/events/alerts ~49 min old | Correct: one burst at 21:58 IST; nothing has happened since |
| Table "rows=1" on unvisited pages | Empty-state placeholder row; those pages load on click |
| `HEAD /` 405 | Correct for a GET-only route |
| `started_at` is a raw epoch float in `/status` | Not displayed in the UI; minor API-consistency note only |

---

## 13. Recommended fixes (awaiting your approval)

1. **`config.py` `to_config_dict()`** — include `watch_paths`. *(Defect 4, high)*
2. **`dashboard/index.html` `saveSettings()`** — only send
   `persistence_enabled` when the select holds a real value, so an unloaded form
   cannot silently disable persistence. *(Defect 5, high)*
3. **Guard live `POST /settings`** — snapshot `config.yaml` before writing and
   restore on failure, so a bad save cannot silently destroy the shipped
   configuration. *(Prevents the damage seen three times)*
4. **`refreshAll()` message** — use the current page's name, e.g.
   "Events refreshed." *(Defect 1)*
5. **Settings refresh** — have `refreshAll` reuse the `/status` response instead
   of re-requesting it. *(Defect 2)*
6. **Mobile header** — allow `.topbar`/`.controls` to wrap at ≤430 px (or drop
   `overflow-x: hidden` so content can scroll). *(Defect 3)*
7. **Optional** — ship a `favicon.ico`; prune old report files; clarify the
   "Processes" card label (snapshots vs unique processes).

---

## 14. Screenshots

| File | Purpose |
|---|---|
| `ptmp/ui_shots/local_live.png` | Desktop 1600×1150, live |
| `ptmp/ui_shots/local_900.png` | Tablet 900 px |
| `ptmp/ui_shots/local_390.png` | Mobile 390 px (shows the clipped controls) |
| `ptmp/ui_shots/header390b.png` | Mobile 390 px, header detail |
| `ptmp/ui_shots/w430.png`, `w560.png`, `w650.png` | Width sweep |

All are genuine PNGs written by headless Chrome.

**No source code, configuration or locked file was modified, and no mutating
request was sent during this verification. Awaiting your approval before any
change is made.**
