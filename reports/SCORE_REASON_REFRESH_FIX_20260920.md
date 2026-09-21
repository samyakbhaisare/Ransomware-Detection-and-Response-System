# RDRS — SCORE REASON + REFRESH FIX

**Date:** 20 September 2026
**Scope:** Score Reason formatting, Refresh button, safe verification
**Config unchanged this cycle:** `shutdown_timeout: 20.0` · `persistence.enabled: true`

---

## 1. FILES CHANGED

| File | Change |
|---|---|
| `src/rdrs/dashboard/index.html` | Score Reason renderer + card styling; Refresh busy state |
| `ptmp/dashboard_frontend_harness.mjs` | +12 tests (B-13); Element stub made DOM-faithful |
| `ptmp/add_harness_b13.py` | **new** — harness patch script |
| `ptmp/verify_score_reason_browser.py` | **new** — bounded browser driver |
| `ptmp/fix_harness_element_stub.py` | **new** — stub fix script |

**No backend file changed.** Detection logic, scoring weights and signal names are untouched — this is presentation only.

---

## 2. SCORE REASON — ROOT CAUSE AND FIX

**Root cause.** The backend sends one string:

```
Ransomware-like burst detected; signals: rapid_encryption, mass_rename, extension_change, high_entropy
```

and the dashboard rendered it with a single `textContent` assignment into a bare `<div>` with **no CSS class at all**. So the whole reason became one unbroken run of text — cramped, hard to scan, and overflowing on narrow screens.

**Fix (presentation only):**

* `renderScoreReason(reason)` splits the string on `signals:` into a **lead sentence** and a **signal list**.
* Signals render as a wrapped `<ul>` of **pill tags** — `flex-wrap`, `gap: 6px`, so they reflow instead of overflowing.
* Human-readable labels: `rapid_encryption` → **Rapid encryption**, `mass_rename` → **Mass rename**, `extension_change` → **Extension change**, `high_entropy` → **High entropy**. The raw signal name stays available as a `title` tooltip, so nothing is hidden.
* `rapid_encryption` and `mass_rename` get a **red** marker dot; others a yellow one — readable without relying on colour.
* An **unknown** signal is still shown, made readable from its own name, rather than dropped.
* Empty reason falls back to *"No score recorded"*.
* Styling uses the existing dark palette (`--panel-2`, `--text`, `--muted`, `--red`, `--yellow`) with `overflow-wrap: anywhere` and no fixed widths.

---

## 3. REFRESH — WHAT WAS ACTUALLY WRONG

**Investigated the full flow first.** The handler and data path were already sound:

| Check | Finding |
|---|---|
| Click handler executes | ✅ wired to `refreshAll(true)` |
| Correct endpoints, once per refresh | ✅ `/status` + `/dashboard-data` — 2 requests, no duplicates |
| Fresh data from the backend | ✅ both fetched live, no cache |
| All cards update | ✅ `renderDashboard` → score, events, alerts, incidents, top-modified, charts, summary |
| Charts | ✅ raw canvas redraw (`drawChart`), no Chart.js leak, no stale instance |
| Duplicate requests | ✅ `state.refreshing` guard skips overlap |
| Success message truthful | ✅ names the page actually refreshed |
| Failure message truthful | ✅ `catch` sets red status + *"Refresh failed: …"*; no false success |
| Settings form protected | ✅ polling does not repopulate the form |

**The genuine defect was the missing loading state.** `refreshBtn` was referenced in only three places — markup, const, listener — and was **never disabled and never relabelled**. Clicking it gave no visual feedback, so a slow or failed refresh looked like nothing had happened.

**Fix:** `setRefreshBusy(busy)` disables the button, sets `aria-busy`, and switches the label **Refresh → Refreshing…**. Applied only for manual refreshes so the 5-second background poll cannot make the button flicker, and restored in `finally` so it can never stick on *"Refreshing…"*.

---

## 4. VERIFICATION — EXACT COUNTS

| Suite | Command | Result |
|---|---|---|
| **Front-end harness** | `node ptmp/dashboard_frontend_harness.mjs` | **98 passed, 0 failed** |
| **Real browser (Chrome/CDP)** | `python ptmp/verify_score_reason_browser.py` | **9 passed, 0 failed** |
| Backend pytest | — | **NOT RUN** — see §6 |

Harness grew 86 → **98** with a new `B-13` section:

* lead sentence rendered separately · signals become a list, not one run of text
* all four signals readable · raw name kept on the tag · encryption signals marked severe
* a reason with no signals renders just the lead · empty reason falls back correctly
* an unknown signal is still shown, made readable
* Refresh disables + relabels the button, sets `aria-busy`, and restores afterwards

### Real browser results — **completed this cycle**

| Check | Result |
|---|---|
| Lead sentence renders | PASS |
| Four signal tags render | PASS |
| All four signals readable | PASS |
| Card does not overflow horizontally | PASS |
| **390 px** — fits, 4 tags visible | PASS |
| **430 px** — fits, 4 tags visible | PASS |
| **768 px** — fits, 4 tags visible | PASS |
| **1600 px** — fits, 4 tags visible | PASS |
| Screenshots captured for all four widths | PASS |

Screenshots: `ptmp/ui_shots/score_reason_{390,430,768,1600}.png`

> The first browser attempt reported 0 tags at two widths. That was a **race in the test**, not a product defect: the 5-second poll legitimately re-renders the card from live backend data and overwrote the injected value. Render and measure are now atomic, and all widths pass. It incidentally confirms the poll refreshes the score reason.

---

## 5. SAFETY COMPLIANCE

* **No deletion of any kind.** The browser script was changed to use a unique Chrome profile directory per run instead of wiping a shared one — the earlier version's cleanup tripped the delete guard, so the deletion was removed rather than worked around.
* **No bulk deletion executed or approved.**
* **No test weakened, skipped or rewritten to hide a failure.** Where tests changed, they changed because the product contract changed or the stub was unfaithful — each is listed in §1 with its reason.
* **Detection logic, scoring weights and signal names unchanged** — the encoding/safety scan asserts this and passes.
* Docker and Phase 10 untouched.

---

## 6. REMAINING LIMITATIONS

1. **Backend pytest suite NOT run.** It triggers pytest's own mass temp-directory cleanup, which the sandbox blocks. Last confirmed count remains **739 passed**, taken before the recent dashboard changes. **No backend file changed this cycle**, so this is low risk — but it is not a measurement.
2. **No full-page browser pass** at all four widths this cycle — only the Score Reason card was measured in the browser. The broader UI fixes from the previous cycle rest on the harness and live HTTP.
3. The live dashboard's own score reason is currently empty (`""`) because no burst has run since the last restart, so the card shows *"No score recorded"* in normal operation. The tagged rendering was verified by injecting a real reason through the real `renderScore` function.

---

## 7. LOCK GATE — POSITION

**No file, FR or Phase is claimed as 100/100, FROZEN or PERMANENTLY LOCKED on this cycle's evidence.**

| Item | Position |
|---|---|
| `src/rdrs/dashboard/index.html` | Modified. Harness 98/98 and browser 9/9 pass; backend suite not re-run |
| Backend source | **Unchanged** this cycle |
| FR-01…FR-13 / Phase 1…Phase 9 | Carried over, not re-verified this cycle |
| Phase 10 / Docker | Not started, not modified |

---

## 8. SUBMISSION POSITION

Both reported problems are **fixed and verified**:

* **Score Reason** renders as a readable lead sentence with wrapped, labelled signal tags — confirmed in a real browser at 390 / 430 / 768 / 1600 px with no overflow.
* **Refresh** fetches fresh data exactly once per cycle, updates every card and chart, reports success or failure truthfully, protects unsaved Settings edits, and now shows a genuine loading state.
