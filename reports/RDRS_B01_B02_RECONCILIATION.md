# Reconciliation Addendum — B-01 / B-02

**Date:** 2026-09-17
**Supersedes:** the B-01/B-02 verification claims in `RDRS_B01_B07_FIX_REPORT.md`
**Verdict:** **NOT FROZEN / NOT LOCKED / NOT 100-100**

---

## 1. What I got wrong

The challenge was correct. Two distinct problems:

**(a) I presented a pre-fix failure artifact as if it were verification.**
`reports/evidence/B01_B02_BASELINE_BEFORE.txt` contains the **pre-fix** outputs
(`window 0 / score 0.0 / NORMAL` and `NORMAL / 30.0 / scores 3 / alerts 3 /
incidents 0`). The file is named "BEFORE" and the report labelled it as the
baseline — but I passed it to `present_files` in the same batch as the passing
evidence, with no prominent warning. That was misleading and I should not have
done it.

**(b) The E2E harness could not detect the B-02 defect at all.**
This is the substantive finding. Running the E2E against the pre-fix code:

```
PASS  burst was detected (incident created)  ({"score_count": 5, "alert_count": 5,
                                               "incident_count": 1})
PASS  score persisted                        (score_count=5)
PASS  alert persisted                        (alert_count=5)
PASS  exactly one incident for the one burst (incident_count=1)
```

The pre-fix code produced **5 duplicate scores and 5 duplicate alerts for one
burst**, and the E2E passed anyway, because the assertions were `>= 1` for the
counters. So the E2E was **not valid evidence for B-02**. My earlier report
presented it as such.

**(c) B-01's persisted-replay path was not reproducible.** The live
`data/rdrs.db` was destroyed mid-cycle by the (now fixed) test fixture, so the
one passing B-01 replay could not be repeated. A verification whose input no
longer exists is not a verification.

---

## 2. Exact reproduction of both failing paths

Both paths were reproduced **verbatim** against a reconstructed pre-fix tree, and
then run against the canonical fixed tree with the *same script, same database,
same commands*.

**Pre-fix tree:** `ptmp/prefix_tree/` — a full copy of `src/` with the three
modified files restored from `reports/backups/bugfix_20260917_185831/`.
Confirmed pre-fix: `_window_anchor` ×0, `detector.reset()` ×1,
`_responded_levels` ×0, `_active_signals` ×6 (so it is the canonical pre-fix
tree, not the stale second checkout).

```bash
# Required path 1 + 2 against the PRE-FIX tree
PYTHONPATH='<abs>\ptmp\prefix_tree\src' python ptmp/verify_b01_b02.py
# Required path 1 + 2 against the CANONICAL tree
python ptmp/verify_b01_b02.py
```

### Required path 1 — B-01 persisted replay

| | Pre-fix | Post-fix |
|---|---|---|
| persisted events loaded | 235 | 235 |
| **final** window / score / level | **0 / 0.0 / NORMAL** | 235 / 70.0 / CRITICAL |
| **peak** window / score / level | **0 / 0.0 / NORMAL** | 121 / **70.0 / CRITICAL** |
| peak signals | `[]` | `rapid_encryption, mass_rename, extension_change` |
| CRITICAL evaluations | **0** | **115** |
| **VERDICT** | **FAIL** | **PASS** |

Pre-fix output reproduced exactly as quoted in the challenge:

```
  window event_count   : 0
  score                : 0.0
  level                : NORMAL
```

### Required path 2 — B-02 continuous runtime ingest

| | Pre-fix | Post-fix |
|---|---|---|
| final window event_count | 15 | 75 |
| final modified/min | 0.0 | 60.0 |
| final rename_count | 15 | 15 |
| **final level** | **NORMAL** | **CRITICAL** |
| **final score** | **30.0** | **95.0** |
| final signals | `mass_rename, extension_change` | `rapid_encryption, mass_rename, extension_change, high_entropy` |
| **persisted scores** | **3** | **2** |
| **persisted alerts** | **3** | **2** |
| **persisted incidents** | **0** | **1** |
| **VERDICT** | **FAIL (4 of 5 checks)** | **PASS** |

Pre-fix output reproduced exactly as quoted in the challenge:

```
  final level              : NORMAL
  final score              : 30.0
  persisted scores         : 3
  persisted alerts         : 3
  persisted incidents      : 0
```

**A/B totals:** pre-fix **1/6**, post-fix **6/6**.
Evidence: `reports/evidence/RECONCILE_B01_B02_PREFIX_REPRO.txt`,
`reports/evidence/RECONCILE_B01_B02_POSTFIX.txt`.

---

## 3. Why the E2E passed while the other two failed

Your question #4, answered with evidence rather than assertion.

**It was not a code-path difference. It was a test-strength difference.**

I ran the E2E against the pre-fix tree. It scored **20/30** — it *did* fail, but
on the wrong checks:

| Check | Pre-fix result |
|---|---|
| `no duplicate scores for one burst` | **FAIL** — `score_count=7` |
| `no duplicate alerts for one burst` | **FAIL** — `alert_count=7` |
| `exactly one incident for the one burst` | **FAIL** — `incident_count=2` |
| encoding (B-03/B-04) | FAIL ×4 |
| settings (B-05) | FAIL ×2 |
| **B-02 duplicate checks as originally written** | **PASS** — because they were `>= 1` |

The original E2E asserted `score_count >= 1`, `alert_count >= 1` and
`incident_count == 1`. Pre-fix, the live monitor feeds events fast enough that
the wiped window rebuilds past CRITICAL before the next reset, so an incident
*is* eventually created — but alongside 5–7 duplicate scores and alerts, and on
one run **2 duplicate incidents**. The `>= 1` assertions could not see any of
that.

So the three paths differed only in **what they measured**, not in what the code
did:

| Path | Why it discriminates |
|---|---|
| B-01 persisted replay | Measures the **window contents** directly. Pre-fix the window is emptied, so it cannot be fooled. |
| B-02 runtime ingest | Measures the **final severity and exact counters** for one synthetic burst. Pre-fix the window is wiped mid-burst, so the burst never reaches CRITICAL. |
| E2E (before) | Asserted only **existence** (`>= 1`). An incident existed, so it passed. |
| E2E (after) | Now also asserts **absence of duplicates** (`<= 2` per band) and one incident. |

### Fix applied to the E2E assertions

```python
report("no duplicate scores for one burst (B-02)", score_count <= 2, ...)
report("no duplicate alerts for one burst (B-02)", alert_count <= 2, ...)
report("one score and one alert per severity band", score_count == alert_count, ...)
```

Deduplication is per severity band, so at most 2 of each is correct for a burst
escalating WARNING → CRITICAL. This is a **strengthening**, not a relaxation.

### One further correction found while doing this

The balance assertion initially reported `scores=1, alerts=2` post-fix. That was
**my test reading a torn snapshot**, not a product defect: `summary()` issues
five independent `COUNT` queries while the monitor is still writing, so a
snapshot can interleave a score and its incident. Verified against the database
— the rows are correct and balanced:

```
alerts:  id=1 WARNING  score=40   signals={rapid_encryption: True, ...}
         id=2 CRITICAL score=70   signals={rapid_encryption, mass_rename: True, ...}
scores:  id=1 40.0 WARNING | id=2 70.0 CRITICAL
incidents: id=1 threat_score=70.0 threat_level=CRITICAL
```

The E2E now re-reads the summary after a 2 s settle before asserting counter
relationships.

### E2E A/B after strengthening

| | Pre-fix | Post-fix |
|---|---|---|
| `exactly one incident` | **FAIL** (`incident_count=2`) | PASS (1) |
| `no duplicate scores` | **FAIL** (`score_count=7`) | PASS (2) |
| `no duplicate alerts` | **FAIL** (`alert_count=7`) | PASS (2) |
| **total** | **20/30** | **30/30** |

Evidence: `reports/evidence/RECONCILE_E2E_PREFIX.txt`,
`reports/evidence/BUGFIX_E2E_VERIFICATION.txt`.

---

## 4. Regression tests added for both failures (before fixing)

Two tests were added that reproduce the two real paths as first-class tests.
They were written and confirmed to fail against the pre-fix code.

| Test | Reproduces |
|---|---|
| `test_b01_persisted_replay_of_a_stored_burst_is_detected` | The persisted-replay path: persists a burst through the real `DatabaseManager.record_file_event`, backdates `created_at` by 6 h (modelling a stored burst replayed later — `record_file_event` stores the *write* time, not the event's own timestamp), reads it back and replays it. Asserts the replay lag exceeds `window_seconds` and that the peak reaches CRITICAL. |
| `test_b02_long_escalating_burst_creates_exactly_one_incident` | The continuous-ingest path: 80 high-entropy writes → 20 renames → 40 more writes, driven through the real runtime. Asserts `incidents == 1`, `scores <= 2`, `alerts <= 2`. |

### Discriminating power — measured, not assumed

```bash
# against the pre-fix tree
PYTHONPATH='<abs>\ptmp\prefix_tree\src' python -m pytest \
    tests/unit/test_bugfix_b01_b07.py -q -o pythonpath=
```

```
13 failed, 19 passed
```

The 13 pre-fix failures include every B-01 and B-02 regression test:

```
FAILED test_b01_historical_burst_is_detected
FAILED test_b01_lagging_event_is_not_purged_on_ingest
FAILED test_b01_stale_out_of_order_event_does_not_pollute_window
FAILED test_b01_persisted_replay_of_a_stored_burst_is_detected
FAILED test_b02_one_burst_creates_exactly_one_incident
FAILED test_b02_sustained_burst_does_not_duplicate_alerts
FAILED test_b02_new_burst_after_recovery_creates_a_new_incident
FAILED test_b02_incident_records_the_real_score
FAILED test_b02_long_escalating_burst_creates_exactly_one_incident
FAILED test_b03_served_dashboard_bytes_are_clean
FAILED test_b05_status_reports_config_before_start
FAILED test_b05_status_reports_unavailable_when_config_missing
FAILED test_b07_status_exposes_unhealthy_subsystems
```

All 32 pass against the canonical tree. **The tests are not tautological.**

---

## 5. Root cause (unchanged, now fully evidenced)

**B-01.** `BurstDetectionEngine` measured its sliding window against the **wall
clock**. `add_event()` appended an event and then called
`_build_analysis(self._clock())`; `_purge_expired()` deleted anything older than
`now - window_seconds`. Any event whose timestamp lagged the sampling clock by
more than 60 s was appended and purged in the same operation — so a replay of
stored events lost **100 %** of its events. Because `record_file_event` stores
the *write* time as `created_at`, a stored burst replayed later is *always*
stale, which is why this path failed reliably.

**B-02.** Two coupled defects. (a) `_handle_file_event` called `detector.reset()`
after every response, **clearing the window**, so a burst climbing towards
CRITICAL was wiped back to zero and never created an incident. (b) A burst stays
dangerous for every event inside its window, so `response.process()` ran **once
per event**, producing duplicate scores, alerts and incidents for one burst.

Both are fixed in the canonical tree only (`src/rdrs/burst_detection.py`,
`src/rdrs/runtime.py`). **No threshold, weight, window length or band boundary
was changed**, and no locked file was touched.

---

## 6. Re-run results — all required layers

| Layer | Command | Result |
|---|---|---|
| B-01 persisted replay | `python ptmp/verify_b01_b02.py` | **6/6 PASS** (peak 70.0 CRITICAL) |
| B-02 runtime ingest | `python ptmp/verify_b01_b02.py` | **6/6 PASS** (CRITICAL 95.0, 1 incident) |
| Full suite | `python -m pytest tests -q` | **448 passed, 0 failed** |
| Frontend harness | `node ptmp/dashboard_frontend_harness.mjs` | **32 passed, 0 failed** |
| E2E | `python ptmp/e2e_bugfix_verification.py` | **ALL 30 CHECKS PASSED** |
| Encoding + safety | `python ptmp/encoding_safety_scan.py` | **ALL 22 CHECKS PASSED** |

**The previously-passing E2E results remain valid after the fix** — they were
re-executed from a clean database and now pass all 30 checks *including* the
three strengthened duplicate assertions that the pre-fix code fails.

### Full suite detail

```
448 passed, 8 warnings in 24.60s
```

(415 at the start of the cycle → 448 now: +33 tests.)

### E2E detail

```
burst was detected       : {"event_count": 279, "score_count": 2,
                            "alert_count": 2, "incident_count": 1}
exactly one incident     : incident_count=1
no duplicate scores      : score_count=2 (expected <= 2)
no duplicate alerts      : alert_count=2 (expected <= 2)
score tile               : 70.0 CRITICAL, reason populated, timestamp populated
ALL 30 E2E CHECKS PASSED
```

---

## 7. Evidence and backups preserved

| Artifact | Contents |
|---|---|
| `reports/backups/bugfix_20260917_185831/` | pre-change copies of all 6 modified files + the `.pth` |
| `reports/backups/evidence_db/rdrs.db.persisted_replay_fixture` | the persisted-replay fixture DB (235 events / 2 scores / 2 alerts / 1 incident), sha256 `39326243847ceb23…` |
| `reports/evidence/RECONCILE_B01_B02_PREFIX_REPRO.txt` | **the failing outputs, reproduced** |
| `reports/evidence/RECONCILE_B01_B02_POSTFIX.txt` | the same two paths passing |
| `reports/evidence/RECONCILE_E2E_PREFIX.txt` | E2E against the pre-fix tree — 20/30 |
| `reports/evidence/BUGFIX_E2E_VERIFICATION.txt` | E2E against the fixed tree — 30/30 |
| `reports/evidence/BUGFIX_FINAL_SUITE.txt` | 448 passed |
| `reports/evidence/BUGFIX_FRONTEND_HARNESS.txt` | 32 passed |
| `reports/evidence/BUGFIX_ENCODING_SAFETY_SCAN.txt` | 22 checks passed |
| `reports/evidence/B01_B02_BASELINE_BEFORE.txt` | **pre-fix baseline — NOT verification output** |
| `ptmp/prefix_tree/` | the reconstructed pre-fix tree used for the A/B |

`B01_B02_BASELINE_BEFORE.txt` now carries a header stating that it is the
pre-fix baseline and must not be read as a verification result.

---

## 8. Corrected status

| ID | Previous claim | Corrected status |
|---|---|---|
| B-01 | VERIFIED | **VERIFIED** — persisted replay now 6/6 against a preserved fixture; 4 regression tests, all of which fail pre-fix |
| B-02 | VERIFIED | **VERIFIED** — runtime ingest now 6/6; 5 regression tests, all of which fail pre-fix; E2E strengthened to detect duplication and now discriminates |
| B-03/B-04 | VERIFIED | VERIFIED (unchanged) |
| B-05 | VERIFIED | VERIFIED (unchanged) |
| B-06 | VERIFIED | VERIFIED (unchanged) |
| B-07 | VERIFIED | VERIFIED (unchanged) |
| E2E as evidence for B-02 | implied valid | **was NOT valid** — assertions too weak; now strengthened and re-validated |

**Changed files in this reconciliation:** `ptmp/e2e_bugfix_verification.py`
(strengthened assertions), `tests/unit/test_bugfix_b01_b07.py` (2 new
regression tests), `ptmp/verify_b01_b02.py` (new A/B harness),
`ptmp/prefix_tree/` (reconstruction, not part of the deliverable).
**No production source file was changed in this reconciliation** — the fix
itself was already correct; what was wrong was my verification and my reporting.

---

## 9. Remaining issues

1. **Docker / Phase 10 — BLOCKED BY ENVIRONMENT.** CLI 29.8.0 present, daemon
   not running, `docker compose` unavailable. No claim here depends on it.
2. **The original live `data/rdrs.db` is unrecoverable** (destroyed by the
   now-fixed fixture). The persisted-replay path is now covered by a preserved
   fixture (`reports/backups/evidence_db/`) and by a self-contained regression
   test, so this no longer blocks verification.
3. **`summary()` in `/dashboard-data` is not a consistent snapshot** under
   concurrent writes — five independent `COUNT` queries can interleave. Harmless
   for the dashboard, but it makes any counter-relationship assertion racy.
   Worth wrapping in a single transaction if counter invariants are ever
   asserted elsewhere.
4. **Stale second checkout** at `…\RDRS\RDRS\RDRS-CLI` still exists (left
   deliberately; it contains the `AttributeError` defect).
5. **setuptools missing from the venv**, so `pip install -e .` fails.

---

## 10. Status statement

B-01 and B-02 may now be marked **VERIFIED**, because both real verification
paths pass and both were demonstrated to fail without the fix:

- B-01 persisted replay: **1/6 pre-fix → 6/6 post-fix**
- B-02 continuous runtime ingest: **fails pre-fix → passes post-fix**
- Regression tests: **13 fail pre-fix → 32 pass post-fix**
- E2E: **20/30 pre-fix → 30/30 post-fix**

**The project is NOT FROZEN, NOT LOCKED, and NOT 100/100.** Phase 10 cannot be
executed in this environment, so the lock gate is not met.
