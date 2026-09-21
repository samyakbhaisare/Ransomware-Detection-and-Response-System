# RDRS — LIVE UI PROOF: SCORE REASON + REFRESH

**Date:** 20 September 2026
**Purpose:** prove the Score Reason formatting and the Refresh button in the **live UI**, not in stubs.

---

## 1. WHY THE EARLIER "FIXED" CLAIM WAS NOT PROOF

The previous browser run called `renderScore(...)` **directly**, bypassing the live render path. That proved the formatting function worked but **not** that the running dashboard used it. The user's screenshot of the raw string was the correct challenge.

This cycle re-verified from the bottom up:

| Question | Method | Answer |
|---|---|---|
| Does the server serve the fixed file? | sha256 of `GET /` vs sha256 of the file on disk | **Identical** (`a966bf117874e691…`) |
| Is `renderScoreReason` in the served HTML? | grep the served bytes | **Yes** |
| Does the live path call it? | grep the served `renderScore` body | **Yes** — `renderScoreReason(scoreData.reason)` |
| Was the browser seeing a cached page? | inspect response headers | **Yes, a real gap** — see §2 |

**Root cause of the stale UI:** `FileResponse` was returned with **no `Cache-Control` header**. With only `ETag`/`Last-Modified`, browsers apply heuristic caching and can keep serving the previous page from the disk cache, so a fix appears not to have taken effect even after a normal refresh.

---

## 2. FIXES

### 2.1 Stale dashboard served from browser cache
`src/rdrs/api.py` — the `/` route now returns:

```python
headers={
    "Cache-Control": "no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}
```

The dashboard is a single ~100 KB self-contained page served from localhost, so always re-fetching costs nothing and guarantees the operator never sees a stale UI.

### 2.2 Score Reason formatting *(implemented in the previous cycle, proven live here)*
`renderScoreReason()` splits the backend's single string into a **lead sentence** plus a wrapped `<ul>` of pill tags, with human-readable labels (`rapid_encryption` → "Rapid encryption"), the raw name kept in a `title` tooltip, a red marker on encryption signals, and a safe fallback for unknown or empty reasons.

### 2.3 Refresh silently doing nothing — a real defect found by this cycle
The live test showed that clicking Refresh while the 5-second background poll was in flight made `refreshAll(true)` **return immediately**: no busy state, no result message. That is exactly what "Refresh is not working" looks like.

`refreshAll` now **waits for the in-flight cycle and then runs its own**, so a manual click always produces a fresh fetch and an honest result. The background poll still skips (no queue build-up), the wait is bounded so it can never spin, and a timeout reports an error rather than pretending.

---

## 3. FILES CHANGED

| File | Change |
|---|---|
| `src/rdrs/api.py` | `/` now sends `Cache-Control: no-store, must-revalidate` |
| `src/rdrs/dashboard/index.html` | `refreshAll` serialises a manual refresh instead of dropping it |
| `ptmp/verify_live_ui.py` | **new** — live proof driver (cache disabled, real render path) |
| `ptmp/dashboard_frontend_harness.mjs` | overlap test updated to the corrected contract + a no-duplicate-requests assertion |

---

## 4. LIVE BROWSER RESULTS — 17 passed, 0 failed

Real Chrome, **cache disabled** (`Network.setCacheDisabled`), driven over CDP. The Score Reason is exercised through the **real render path** — `renderDashboard(payload)` with a payload shaped exactly like `/dashboard-data` — not by calling `renderScore` directly.

### Score Reason

| Check | Result |
|---|---|
| The LIVE page contains `renderScoreReason` | **PASS** |
| The LIVE page contains the `signal-tag` CSS | **PASS** |
| `renderScoreReason` is callable in the live page | **PASS** |
| The lead sentence is separated from the signals | **PASS** |
| Four signal tags render via the real render path | **PASS** |
| All four signals are human-readable | **PASS** |
| **The raw unformatted string is NOT what is displayed** | **PASS** |
| The card does not overflow horizontally | **PASS** |

### Refresh button (real click, observed in flight)

| Check | Result |
|---|---|
| Disabled while the request is in flight | **PASS** |
| Label changes to `Refreshing…` | **PASS** |
| `aria-busy` is set | **PASS** |
| Restored after the refresh settles | **PASS** |
| Success message comes from the actual API result | **PASS** |

### Responsive widths — lead + 4 tags, no overflow

| Width | Result |
|---|---|
| 390 px | **PASS** |
| 430 px | **PASS** |
| 768 px | **PASS** |
| 1600 px | **PASS** |

Screenshots: `ptmp/ui_shots/proof_score_reason_{390,430,768,1600}.png`

### Harness

| Suite | Result |
|---|---|
| Front-end harness | **99 passed, 0 failed** |

---

## 5. WHAT THE SCREENSHOT SHOWS

At 390 px the Score Reason card renders:

```
Score Reason
Ransomware-like burst detected
[• Rapid encryption] [• Mass rename]
[• Extension change] [• High entropy]
```

— not the raw `Ransomware-like burst detected; signals: rapid_encryption, mass_rename, extension_change, high_entropy`.

---

## 6. REMAINING GAPS — HONEST

1. **Backend pytest NOT run.** It triggers pytest's own mass temp-directory cleanup, which the sandbox blocks. Last confirmed **739 passed**, from before the recent changes. **`api.py` was modified this cycle** (the cache header), so that change in particular has not been covered by the backend suite.
2. **No full-page browser pass** — only the Score Reason card and the Refresh button were measured live. The dark-theme scrollbar/spinner/overflow fixes from the earlier cycle rest on the harness and on CSS assertions, not on a visual pass.
3. **The live dashboard's own score reason is currently empty**, because no burst has run since the last restart. The tagged rendering is therefore proven against a payload shaped exactly like the real API response, not against a real recorded burst.
4. Leftover headless Chrome profile directories under `ptmp/` were created by the verification runs. Nothing was deleted.

---

## 7. LOCK GATE

**No file, FR or Phase is claimed as 100/100, FROZEN or PERMANENTLY LOCKED on this cycle's evidence.**

| Item | Position |
|---|---|
| `src/rdrs/dashboard/index.html` | Live browser 17/17 and harness 99/99 pass; backend suite not re-run |
| `src/rdrs/api.py` | Modified (cache header); **not covered by a backend run** |
| FR-01…FR-13 / Phase 1…Phase 9 | Carried over, not re-verified |
| Phase 10 / Docker | Untouched |

---

## 8. SAFETY

* **Nothing deleted.** No bulk delete run or approved; no Chrome-profile deletion, no pytest temp cleanup, no report deletion.
* No test weakened or rewritten to hide a failure. The one harness assertion that changed encoded the old *"manual refresh is silently dropped"* behaviour, which is the defect being fixed; it now asserts the corrected contract **plus** a no-duplicate-requests check that did not exist before.
* Detection logic, scoring weights and signal names untouched.
