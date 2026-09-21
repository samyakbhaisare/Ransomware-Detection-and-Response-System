# RDRS — REPORT DOWNLOAD VERIFICATION

**Date:** 19 September 2026 · **Mode:** verify only — **no code modified, no report deleted**
**Server:** `uvicorn rdrs.api:app` on `127.0.0.1:8000`

---

## VERDICT

**The download endpoint itself is reliable — 0 failures in 2,034 requests.**
**The failure is real, but it is the GENERATE step that fails, not the download.**

| | |
|---|---|
| `GET /reports/{id}/{format}` (the actual download) | **PASS — 0 failures in 2,034 requests** |
| `POST /reports/generate` (what the UI button does *first*) | **FAIL — 35 × HTTP 500** |
| Frontend error handling for a failed generate | **FAIL — no error shown, message hangs** |
| Report retention | **FAIL in this environment — files not deleted** |

I am **not** claiming the download feature passes, because the user-visible action
(click the report button) does fail intermittently. The defect is in the step
that runs before the download.

---

## 1. Tests requested — results

### 1.1 · 20 consecutive JSON downloads — **PASS**

```
 1..20   status=200  bytes=122599  valid=True
Content-Type        : application/json
Content-Disposition : attachment; filename="incident_2_20260919T132730Z.json"
status distribution : {200: 20}   FAILURES: 0
```

### 1.2 · 20 consecutive CSV downloads — **PASS**

```
 1..20   status=200  bytes=88442  rows=171  valid=True
Content-Type        : text/csv; charset=utf-8
Content-Disposition : attachment; filename="incident_2_20260919T132728Z.csv"
status distribution : {200: 20}   FAILURES: 0
```

### 1.3 · Concurrency — **PASS**

| Test | Requests | Result |
|---|---|---|
| 20 concurrent JSON | 20 | all **200**, one identical body size (122599) |
| 20 concurrent CSV | 20 | all **200**, one identical body size (88442) |
| Downloads during 80 concurrent generations | **1954** | all **200** |

No failures, no truncated bodies, no duplicate/partial files.

### 1.4 · File existence before download — **PASS**

Every download had a matching file on disk. The endpoint globs
`incident_{id}_*.{format}` and serves the newest match; when none exists it
returns a clean **404** with a helpful message ("Generate the report first"),
verified for incidents 999 / 998 / 0.

### 1.5 · Downloaded files complete and valid — **PASS**

JSON parsed as a dict containing `incident`; CSV parsed to **171 rows** with a
multi-column header. Body sizes were byte-identical across all repeats.

### 1.6 · Invalid inputs — **PASS**

| Request | Result |
|---|---|
| `/reports/2/xml` | 400 (format must be json or csv) |
| `/reports/2/JSON` | 400 |
| `/reports/abc/json` | 422 |
| `/reports/2/` | 404 |

---

## 2. THE FAILURE — root cause evidence

### 2.1 What actually fails

Every 500 in the server log, by endpoint:

```
 18  "POST /reports/generate?incident_id=1&format=csv"   500
 17  "POST /reports/generate?incident_id=1&format=json"  500
  8  "GET  /dashboard-data"                              500
  0  "GET  /reports/{id}/{format}"                        ← the download route never 500'd
```

**43 ASGI exceptions total.** The UI button does `POST /reports/generate` and
*then* triggers the download, so a generate 500 looks exactly like "the download
button failed".

Reproduction, with the runtime STOPPED:

```
POST /reports/generate?incident_id=1&format=json -> 500   (3/3 attempts)
GET  /reports/1/json                             -> 200   (3/3 attempts)
GET  /dashboard-data                             -> 200
```

### 2.2 ROOT CAUSE 1 — `prune_reports` lets a delete failure escape (**defect in code added during the fix cycle**)

Traceback:

```
File "src\rdrs\api.py", line 1105, in generate_report
File "src\rdrs\api.py", line  774, in prune_reports
...
_exit_bulk_guard_control(
    result.stderr.strip()
    or "[safe-delete][SAFE_DELETE_BULK_GUARD_ERROR] exit %d" % result.returncode
)
```

`prune_reports` (`api.py:773-777`) is:

```python
try:
    path.unlink()
    removed.append(path)
except OSError:          # <-- too narrow
    continue
```

The delete guard raises from `_exit_bulk_guard_control`, **not** `OSError`, so it
propagates out of `prune_reports` → out of `generate_report` → **HTTP 500**.

Log evidence: **35 × `SAFE_DELETE_BULK_CONFIRM_REQUIRED`** and
**35 × `SAFE_DELETE_BULK_GUARD_ERROR`** — matching the 35 failed generates.

My own docstring on that function claims:

> *"a failed delete is skipped, never raised, so cleanup cannot break report
> generation"*

**It can.** That is a defect I introduced in the consolidated fix cycle.

**Why it looks intermittent:** the guard trips only after a burst of deletions.
While the runtime is running and the guard has not tripped, generates succeed;
once it trips, *every* generate 500s until the process restarts.

### 2.3 ROOT CAUSE 2 — `close_if_stopped` destroys the database handle (pre-existing)

```
AttributeError: 'NoneType' object has no attribute 'connect'
  src\rdrs\api.py:946 dashboard_data
  src\rdrs\dashboard_data.py:529 complete
  src\rdrs\dashboard_data.py:115 latest_score

RuntimeError: Database not initialized
  src\rdrs\storage.py:479 _require_engine
```

`close_if_stopped()` (`api.py:101-110`) sets `runtime._database = None` whenever
the state is not RUNNING/DEGRADED, and runs in the `finally` of **7 read
endpoints**. Those 8 `/dashboard-data` 500s are this defect, and it is the same
cause as the **409 on `POST /monitor/start`** reported earlier.

### 2.4 ROOT CAUSE 3 — the frontend shows no error (**item 10 FAILS**)

`generateReport()` has **no try/catch**:

```js
showMessage(`Generating ${format.toUpperCase()} report...`);
const result = await request(...);        // throws on 500
showMessage(`${format} report generated...`);
```

and the click handler is `() => generateReport(...)` — also no catch.

`request()` **does** throw on non-2xx (`if (!response.ok) throw new Error(...)`),
so a 500 becomes an **unhandled promise rejection**: the message bar stays on
**"Generating JSON report…" forever** and **no error is ever shown**.

There is also **no front-end test** covering a failed report generation.

---

## 3. Retention does not work in this environment

`prune_reports` is meant to cap files at 20 per incident. Measured:

```
incident 1: 20 files before the race test  ->  55  ->  63 files after
```

In isolation the function is correct (30 files → 20). In the live server every
`unlink()` is blocked by the delete guard, so nothing is removed and the
directory grows. The guard is sandbox tooling rather than RDRS code, but the
effect is that **the retention policy silently does nothing** — worth knowing
before claiming B-07 is fixed.

---

## 4. Answers to the specific checks

| # | Check | Result |
|---|---|---|
| 1 | 20 consecutive JSON | **PASS** — 20/20 × 200 |
| 2 | 20 consecutive CSV | **PASS** — 20/20 × 200 |
| 3 | HTTP status per request | recorded for all 2,034 requests |
| 4 | Headers / Content-Disposition | **correct** — `attachment; filename="…"`, right media types |
| 5 | File exists before download | **yes** in every case; 404 with a clear message when absent |
| 6 | Does runtime failure affect downloads? | **Downloads: no.** Generate and `/dashboard-data`: **yes — 500** |
| 7 | Backend logs for failed requests | captured — 43 exceptions, 70 delete-guard lines, exact frames above |
| 8 | Files complete and valid | **yes** — JSON parses, CSV 171 rows, byte-identical repeats |
| 9 | Concurrent downloads | **no failures** — 2,014 concurrent/rapid requests, all 200 |
| 10 | Frontend handles download errors | **NO — fails.** No try/catch; message hangs; nothing shown |

---

## 5. Defects found

| ID | Defect | Severity | File / line | Status |
|---|---|---|---|---|
| **D-1** | `prune_reports` catches only `OSError`; a non-OSError delete failure escapes and turns `POST /reports/generate` into a **500** | **High** | `api.py:773-777` | **Confirmed** |
| **D-2** | `close_if_stopped` nulls `runtime._database` → DB routes return 500 and `POST /monitor/start` returns 409 | **Critical** | `api.py:101-110` | **Confirmed** (reported earlier) |
| **D-3** | `generateReport()` has no error handling — a failed generate leaves "Generating…" forever and shows no error | **Medium** | `dashboard/index.html` `generateReport` | **Confirmed** |
| **D-4** | Report retention deletes nothing in this environment (delete guard) | **Low** | `api.py:715-780` | **Confirmed (environment-dependent)** |
| **D-5** | Frontend uses `incident.id ?? incident.incident_id` for the download URL — correct today because the two ids coincide, but it would target the wrong incident if they ever diverge | **Low** | `dashboard/index.html` `renderReports` | **Probable** |

---

## 6. Reproduction steps

```
# 1. Start the server, then STOP the runtime
curl --noproxy '*' -X POST http://127.0.0.1:8000/monitor/stop

# 2. The generate step (what the report button does first) fails
curl --noproxy '*' -o /dev/null -w '%{http_code}\n' \
     -X POST 'http://127.0.0.1:8000/reports/generate?incident_id=1&format=json'
# -> 500

# 3. The download route itself still works
curl --noproxy '*' -o /dev/null -w '%{http_code}\n' \
     http://127.0.0.1:8000/reports/1/json
# -> 200

# 4. In the dashboard: click JSON on the Reports page -> the message sticks at
#    "Generating JSON report..." and no error appears.
```

For D-1 specifically: issue enough `POST /reports/generate` calls for the delete
guard to trip, then every subsequent generate returns 500.

---

## 7. Files created (no code touched, no reports deleted)

`ptmp/verify_downloads.py` · `ptmp/verify_download_stress.py` ·
`ptmp/verify_download_race.py` · `ptmp/dl_final.log` · `ptmp/restart_test.log`

Report files were **only added**, never removed. `data/reports/` grew from 22 to
63 files purely through the generate calls this test issued.

---

## 8. Recommended fixes (awaiting approval)

1. **D-1** — broaden the delete guard in `prune_reports` so **any** failure to
   delete is skipped, keeping the promise its docstring already makes. *(High)*
2. **D-2** — stop `close_if_stopped` nulling the database handle, and let
   `start()` re-initialise it. Fixes both the 500s and the 409 restart failure.
   *(Critical)*
3. **D-3** — wrap `generateReport` in try/catch and surface the error in the
   message bar. *(Medium)*
4. **D-4** — either accept the guard's behaviour and document it, or log when a
   prune cannot delete so the growth is visible. *(Low)*
5. **D-5** — use `incident.incident_id` explicitly for the download URL. *(Low)*
