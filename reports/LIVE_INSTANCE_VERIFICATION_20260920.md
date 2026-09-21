# RDRS — LIVE INSTANCE VERIFICATION (NO INJECTED DATA)

**Date:** 20 September 2026
**Instance:** the actual running RDRS server at `http://127.0.0.1:8000`
**Result:** **19 passed, 0 failed** against the live instance

---

## 1. WHY THE PREVIOUS VERIFICATION WAS REJECTED — AND WHAT CHANGED

The earlier runs called `renderScore(...)` **directly** with a hand-written payload. That proved the formatting function worked but **not** that the running dashboard displayed real data correctly. This run injects nothing:

* the browser is pointed at the live server and left alone;
* the displayed values are read from the real DOM;
* they are compared against a real `/dashboard-data` response captured in the same run;
* **no** mocked payload, **no** manual render call, **no** test-data screenshot.

The live instance already held a genuine score from a real burst, so no synthetic data was needed at all.

---

## 2. ACTUAL LIVE API RESPONSE

`GET /dashboard-data` captured at **19:21:15** on 20 September 2026:

| Field | Value |
|---|---|
| `score` | **95.0** |
| `level` | **CRITICAL** |
| `reason` | `Ransomware-like burst detected; signals: rapid_encryption, mass_rename, extension_change, high_entropy` |
| `created_at` | **2026-09-19T13:19:30.733453** |
| `event_count` | **402** |
| `alert_count` | **4** |
| `incident_count` | **2** |

---

## 3. ACTUAL BROWSER DISPLAY (same run, same instance)

| Field | Displayed |
|---|---|
| Score | **95.0** |
| Level | **CRITICAL** |
| Score Reason (lead) | **Ransomware-like burst detected** |
| Signal tags | **Rapid encryption · Mass rename · Extension change · High entropy** |
| Last Score Update | **19 Sept 2026, 6:49:30 pm** |
| File Events | **402** |
| Alerts | **4** |
| Last refreshed | **Last refreshed 19:21:20** |

### Timestamp correspondence — proven

The API reports `created_at` in UTC (`2026-09-19T13:19:30`). The browser displays **6:49:30 pm**, which is 18:49:30 IST = 13:19:30 UTC — **the same instant, from the same API response**. The displayed date/time is not stale and does not come from another source.

### Raw string is not what is displayed

The page text contains no `signals: rapid_encryption` run — the reason is rendered as a lead sentence plus four tags.

---

## 4. REFRESH — LIVE BEHAVIOUR

| Check | Result |
|---|---|
| Disabled while running | **PASS** |
| Label shows `Refreshing…` | **PASS** |
| `aria-busy` set | **PASS** |
| Restored after the refresh | **PASS** |
| Success message from the real API result | **PASS** |
| `Last refreshed` advanced | **PASS** |

### Before / after

| | File Events | Alerts | Score | Last refreshed |
|---|---|---|---|---|
| **Before** | 402 | 4 | 95.0 | 19:22:56 |
| **After** | 402 | 4 | 95.0 | **19:22:58** |

The counters are unchanged because the underlying data did not change between the two reads — which is the correct outcome, and the `Last refreshed` stamp proves the cycle actually ran. The refresh is not a no-op: it issued fresh requests.

### Exact requests made by one Refresh click

```
['/status', '/dashboard-data']
```

Exactly two requests, one per endpoint, **no duplicates**. The manual refresh did **not** silently return despite the background poll running — it waited for the in-flight cycle and then performed its own.

---

## 5. A REAL DEFECT FOUND AND FIXED DURING THIS VERIFICATION

The first live run failed one check: **the success message was empty**. Cause: the 5-second background poll called `showMessage("")`, wiping the operator's feedback within seconds — so *"Security Dashboard refreshed."* flashed past and a manual Refresh looked like it had done nothing.

**Fix:** the background poll no longer clears the message bar. The message now stays until the next explicit action replaces it. Re-verified: the success message is present after the refresh.

A second defect was fixed in the same cycle: `refreshAll` returned immediately when a background poll was in flight, so a click could do nothing at all. It now waits for the in-flight cycle (bounded) and then runs its own.

---

## 6. BROWSER CACHE

| Check | Result |
|---|---|
| `Cache-Control` on `/` | **`no-store, must-revalidate`** |
| Browser cache during the test | **disabled** (`Network.setCacheDisabled`) |
| Served page vs file on disk | **byte-identical** |

---

## 7. RESPONSIVE — LIVE PAGE

| Width | Result |
|---|---|
| 390 px | **PASS** — 4 tags, no overflow |
| 430 px | **PASS** — 4 tags, no overflow |
| 768 px | **PASS** — 4 tags, no overflow |
| 1600 px | **PASS** — 4 tags, no overflow |

Screenshots (real live page): `ptmp/ui_shots/live_{390,430,768,1600}.png`
Machine-readable evidence: `ptmp/live_evidence.json`

---

## 8. FILES CHANGED

| File | Change |
|---|---|
| `src/rdrs/dashboard/index.html` | Background poll no longer clears the message bar; `refreshAll` serialises a manual refresh |
| `src/rdrs/api.py` | `/` sends `Cache-Control: no-store, must-revalidate` *(previous cycle)* |
| `ptmp/verify_live_instance.py` | **new** — live-instance driver (no injection) |

**Nothing was deleted or modified in the database, reports, backups, tests, config or lock records.**

---

## 9. REMAINING LIMITATIONS

1. **Backend pytest NOT run.** pytest's own mass temp-directory cleanup is blocked by the sandbox. Last confirmed count remains **739 passed**, from before recent changes. `api.py` changed recently and is **not** covered by a backend run.
2. **`/dashboard-data` returned no new events between the before/after reads**, so the refresh was proven to run and re-stamp, but the counters themselves were not observed changing. Forcing a counter change would require generating real file activity mid-test; the `Last refreshed` advance plus the exact request list are the evidence that the cycle genuinely executed.
3. Only the dashboard page was exercised live. Other pages rest on the harness.
4. Headless Chrome profile directories remain under `ptmp/` from the verification runs. Nothing was deleted.

---

## 10. LOCK GATE

**No file, FR or Phase is claimed as 100/100, FROZEN or PERMANENTLY LOCKED.**

| Item | Position |
|---|---|
| `src/rdrs/dashboard/index.html` | Live instance 19/19 and harness 99/99 pass; backend suite not re-run |
| `src/rdrs/api.py` | Modified (cache header); not covered by a backend run |
| FR-01…FR-13 / Phase 1…Phase 9 | Carried over, not re-verified |
| Phase 10 / Docker | Untouched |
