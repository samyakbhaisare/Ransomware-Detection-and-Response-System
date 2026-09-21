# RDRS — FINAL A–Z COMPLIANCE AUDIT

**Date:** 20 September 2026 · **Type:** read-only, strict, evidence-based
**Authoritative documents read:** `RDRS_Functional_Specification_Document_FSD.docx` · `RDRS_HandsOn_Build_Guide (1).docx` · `RDRS_LOCKED_DEVELOPMENT_RULES.md`

The rejected 125-feature Theoretical Maximum spec was **not** used (it is present under `docs/` and was excluded).

---

## 1. EXECUTIVE SUMMARY

FR-01…FR-12 and Phase 1…8 are implemented with real logic and verified. Two defects and two blockers stop a 100/100 claim:

| # | Finding | Severity |
|---|---|---|
| **A** | **`/dashboard-data` returns HTTP 500 while monitoring is STOPPED** — reproduced live. Root cause: a read endpoint's `finally` closes the **shared** SQLite engine while another request is still reading it. | **HIGH** |
| **B** | **Phase 10 / FR-13 Docker deployment unverified** — daemon not running; Locked Rules §8 require real container build/run verification. | **HIGH (blocker)** |
| **C** | **Backend pytest suite NOT VERIFIED** — safety guard blocks pytest's temp cleanup. | **MEDIUM** |
| **D** | **Guide §16 submission artefacts missing** — architecture description/diagram, false-positive discussion, future-improvements reflection. | **MEDIUM (rubric)** |

Persistence behaviour is **correct by design** and was measured directly: Enabled = durable file-backed SQLite; Disabled = in-memory, non-durable. **No data loss was found.**

---

## 2. DOCUMENTS REVIEWED

| Document | Location | Used for |
|---|---|---|
| FSD (FR-1…FR-13 + NFRs + constraints) | `D:\Samyak\Projects\Internmo\Project 1 RDRS\` | Requirement text (verbatim) |
| Hands-On Build Guide (Phases 1–10, §15 sandbox test, §16 What to Submit, §17 Rubric, §18 Rules) | same | Phase "Done when" criteria, submission + rubric |
| Locked Development Rules (§1–§16) | same | Lock/100 rules, Phase 10 requirement, safety rules |

> The Grading Rubric is a **table** in the Build Guide (7 rows × 3 cols) and is not present in a plain paragraph dump.

---

## 3. FR-01 – FR-13 COMPLIANCE

| Req | FSD wording | Implementation | Evidence | Status |
|---|---|---|---|---|
| FR-1 | Load configuration from config.yaml | `config.py` load/validate/save | Live config intact; loader respects `persistence.enabled` (verified: True→True, False→False) | **100/100 — VERIFIED** |
| FR-2 | Monitor create/modify/delete/rename in real time | watchdog observers | Live: 402 events; `renamed` rows present | **100/100 — VERIFIED** |
| FR-3 | Shannon entropy of modified files | `entropy.py` | Measured 4.397 (text) / 7.979 (random) | **100/100 — VERIFIED** |
| FR-4 | Process metadata via psutil | `telemetry.py` | 914,193 process rows; suspect named in incident | **100/100 — VERIFIED** |
| FR-5 | Store events/processes/alerts/incidents in SQLite | `storage.py` | 402 / 914,193 / 4 / 2 rows in `data/rdrs.db` (354.7 MB) | **100/100 — VERIFIED** |
| FR-6 | Sliding time window | `burst_detection.py` | Burst → CRITICAL; thresholds unchanged | **100/100 — VERIFIED** |
| FR-7 | Weighted threat score 0–100 | `scoring.py` | Live 95.0 CRITICAL; range enforced | **100/100 — VERIFIED** |
| FR-8 | Alerts and incident records | `response.py` | Incident #2 CRITICAL, 40 affected files | **100/100 — VERIFIED** |
| FR-9 | Copy evidence to quarantine (simulation) | `response.py` | `copy2` present; **no** `.unlink(`, **no** `shutil.move` | **100/100 — VERIFIED** |
| FR-10 | Provide REST API endpoints | `api.py` | 9/10 endpoints 200 — **`/dashboard-data` intermittently 500 while STOPPED (Finding A)** | **PARTIALLY VERIFIED** |
| FR-11 | Display live dashboard | `dashboard/index.html` | Real API↔UI correspondence verified; **fails while STOPPED (Finding A)** | **PARTIALLY VERIFIED** |
| FR-12 | Generate JSON/CSV reports | `api.py` | Both 200; real files on disk | **100/100 — VERIFIED** |
| FR-13 | Support automated testing **and Docker deployment** | tests + Docker files | Testing verified; **Docker never built/run** | **PARTIALLY VERIFIED** |

**NFRs:** real-time ✓ · modular ✓ · simulation default ✓ · logging ✓ · maintainable (partial — docs gaps).

---

## 4. PHASE 1 – 10 COMPLIANCE

| Phase | Guide "Done when" | Evidence | Status |
|---|---|---|---|
| 1 | text ~0–5, random ~8.0 | 4.397 / 7.979 | **100/100 — VERIFIED** |
| 2 | file changes update live count | 402 events live | **100/100 — VERIFIED** |
| 3 | DB rows for events + process snapshots | 402 + 914,193 | **100/100 — VERIFIED** |
| 4 | burst above thresholds, normal below | CRITICAL reached | **100/100 — VERIFIED** |
| 5 | Critical score, incident, alert-log, copied evidence, originals untouched | all present; copy-only confirmed | **100/100 — VERIFIED** |
| 6 | `/status` and `/alerts` return JSON | 200; but see Finding A | **PARTIALLY VERIFIED** |
| 7 | dashboard updates on its own | 5 s polling; see Finding A | **PARTIALLY VERIFIED** |
| 8 | JSON + CSV report files | 200 + files on disk | **100/100 — VERIFIED** |
| 9 | tests pass, coverage high | Harness 99/99, live 19/19, encoding 22/22; **backend pytest NOT VERIFIED**, coverage % not re-measured | **PARTIALLY VERIFIED** |
| 10 | `docker compose up` starts with no manual setup | files present; **daemon not running — never executed** | **BLOCKED** |

---

## 5. FILE-BY-FILE LOCK VERIFICATION

| File | Modified after its lock? | Fresh verification | Lock status |
|---|---|---|---|
| `api.py` | **YES** (settings 409, cache header, `close_if_stopped`) | Partial — live API, no backend suite | **NOT RE-LOCKED** |
| `dashboard/index.html` | **YES** (score reason, refresh, lock UI, theme) | Harness 99/99 + live 19/19 | **NOT RE-LOCKED** |
| `runtime.py` | **YES** (persistence/restart fixes) | In-process probes | **NOT RE-LOCKED** |
| `storage.py` | **YES** (in-memory mode, WAL) | Probes; no backend suite | **NOT RE-LOCKED** |
| `monitoring.py` | **YES** (persist retry) | No backend suite | **NOT RE-LOCKED** |
| `cli.py` | **YES** (config paths, idempotent stop) | CLI tests only | **NOT RE-LOCKED** |
| `config.py`, `entropy.py`, `scoring.py`, `burst_detection.py`, `response.py`, `telemetry.py`, `events.py`, `health.py`, `policy.py`, `workers.py`, `audit.py`, `logging.py` | No evidence of recent change | Carried over | Previously locked — **not re-verified this cycle** |

**No file is marked 100/100 — VERIFIED — PERMANENTLY LOCKED by this audit.**

---

## 6. SETTINGS AND PERSISTENCE FINDINGS

Measured with isolated temp configs (project files untouched) plus live SQLite counts.

| Question | Answer | Evidence |
|---|---|---|
| 1. Is Disabled mode intentionally non-durable? | **YES** | `persistence.enabled=False` → `manager.durable=False`, `db_path=None`, **no file created** |
| 2. Does Enabled mode load existing SQLite history? | **YES** | `durable=True`, `db_path=<…>/data/rdrs.db`, file exists |
| 3. Do old DB records remain intact? | **YES** | Live DB: 402 events / 4 alerts / 2 incidents / 4 scores / 914,193 processes — unchanged across mode switches |
| 4. Do settings changes reset the DB? | **NO reset** | Switching mode *replaces the manager*; the durable file and its rows are untouched. Enabled→Disabled makes history **invisible, not deleted** |
| 5. Does monitoring restart reset only in-memory counters? | **YES** | In-process stop/start: scores 1→1 in **both** modes |
| 6. Does a full app restart preserve history? | **Enabled: YES** · **Disabled: NO (by design)** | Earlier live restart: events 402→402, alerts 4→4, incidents 2→2 |
| 7. Events/alerts/incidents/scores/processes/reports intact? | **YES** (Enabled) | Counts above; reports present in `data/reports/` |
| 8. Does the UI explain the mode? | **NO — gap** | The Persistence control is a bare Enabled/Disabled dropdown; **no text anywhere explains that Disabled is in-memory and will not survive a restart** (`in-memory`/`durable`/`restart` all appear **0** times) |

**Verdict:** the observed "old history reappears when Persistence is enabled" and "fresh history when Disabled" are **correct, intentional behaviour**, not data loss. **No actual data loss, overwriting or DB reset was found.**

**One genuine defect:** the "monitoring sometimes shows zero values after changing settings" symptom is explained by mode switching (in-memory starts empty) combined with Finding A — not by deletion.

**Severity:** Low (UX/documentation). **Effect on 100/100:** blocks FR-11's "UI accurately explains state" and weakens FR-1 documentation.

---

## 7. STOPPED → REFRESH HTTP 500 FINDINGS

### Reproduced live

```
stop             -> 200
/dashboard-data  -> 200
/dashboard-data  -> 500      <-- reproduced
/dashboard-data  -> 200
/status x3       -> 200 200 200
/events /processes /alerts /incidents /reports -> all 200
start            -> 200
/dashboard-data  -> 200
```

### Exact root cause

```
RuntimeError: Database not initialized
  api.py:1004  dashboard_data
  dashboard_data.py:521  complete
  dashboard_data.py:246  recent_alerts
  storage.py:1227  list_alerts
  storage.py:558   _require_engine   -> raise RuntimeError("Database not initialized")
```

The endpoint:

```python
@app.get("/dashboard-data")
def dashboard_data():
    runtime = get_runtime()
    database = get_database()                 # ensures engine is initialised
    try:
        return DashboardData(database).complete()   # MANY queries
    finally:
        close_if_stopped(runtime, database)   # CLOSES THE SHARED ENGINE
```

`close_if_stopped()` closes the engine when state is not RUNNING/DEGRADED. So while **STOPPED**, request A's `finally` disposes the shared engine **while request B is still inside `complete()`** → B's next query hits `engine is None` → **500**.

**Why intermittent and why STOPPED-only:** the close only happens when stopped; it needs two overlapping requests (the 5 s poll overlapping a manual refresh, or two rapid polls). While RUNNING the `finally` is a no-op, so no race.

The same pattern exists in the other read endpoints that call `close_if_stopped`.

**Also in the log:** `AttributeError: 'NoneType' object has no attribute 'connect'` — the same defect surfacing through a different path.

**Affected:** FR-10, FR-11 · Phase 6, Phase 7 · files `api.py`, `storage.py`.
**Severity: HIGH.** **Effect on 100/100: blocks FR-10 and FR-11.**
**Required action (not performed — audit only):** stop closing the shared engine from read endpoints, or make `_require_engine` re-initialise defensively, or serialise access with a lock.

---

## 8. BACKEND AND FRONTEND FINDINGS

| Area | Finding | Status |
|---|---|---|
| Dashboard data source | Values come from real API responses; verified timestamp correspondence (API `13:19:30Z` → UI "6:49:30 pm" IST) | **VERIFIED** |
| Score Reason | Real reason rendered as lead + tags; raw string not displayed | **VERIFIED** |
| Refresh | One click → exactly `/status` + `/dashboard-data`; busy state, `aria-busy`, restored | **VERIFIED** |
| Responsive | 390/430/768/1600 px pass | **VERIFIED** |
| Dark theme, scrollbar, number input | CSS rules present; harness-asserted, **no fresh visual pass** | **PARTIALLY VERIFIED** |
| Loading / error states | Refresh + report generation both report real failures | **VERIFIED** |
| Dead buttons | None found | **VERIFIED** |
| Polling | 5 s; does not clobber unsaved settings | **VERIFIED** |
| **STOPPED-state reads** | **Intermittent 500 (Finding A)** | **DEFECT** |

---

## 9. SECURITY AND SAFETY FINDINGS

| Check | Result |
|---|---|
| Copy-only quarantine | ✅ `copy2`; no `.unlink`, no `shutil.move` |
| Original files preserved | ✅ Confirmed by code inspection and by incident evidence (originals present) |
| No real ransomware | ✅ Only `os.urandom` writes + renames in a sandbox |
| No real process termination | ✅ Simulation mode; no kill path |
| Sandbox-only testing | ✅ Tests use `tmp_path`; bursts under `data/sandbox/` |
| Credentials in repo | ✅ **None**; `.env.example` only; no `.env`/keys |
| Deduplication | ✅ Identical-content evidence disambiguated by hash suffix |
| **Local-origin guard on mutating APIs** | ✅ Present |
| False-positive handling | ⚠️ Detection has thresholds, but **no false-positive discussion in docs** |

---

## 10. TEST AND COVERAGE EVIDENCE

| Suite | Date | Result |
|---|---|---|
| Front-end harness | 20 Sep 2026 ~20:17 | **99 passed, 0 failed** |
| Live instance browser | 20 Sep 2026 19:43 | **19 passed, 0 failed** |
| Encoding + safety scan | 20 Sep 2026 | **22 passed, 0 failed** |
| **Backend pytest** | 20 Sep 2026 20:05 | **NOT VERIFIED — blocked** |

* `pytest.skip` **0** · `pytest.xfail` **0** · `@pytest.mark.skip` **0** · `TODO` **0** · `FIXME` **0** — no weakening, no hidden skips.
* 39 unit test files.
* **Coverage percentage NOT VERIFIED** — not re-measured.
* Last confirmed backend count **739 passed**, from **before** the recent `api.py` changes — carried over, not current.
* Guard interference: pytest's own temp-dir garbage collection is blocked by the safety guard; **not bypassed, not retried, no deletion approved**.

---

## 11. DOCKER FINDINGS

| Item | Finding |
|---|---|
| Docker CLI | Present — v29.8.0 |
| **Docker daemon** | **NOT RUNNING** |
| `Dockerfile` | Well-formed: `python:3.13-slim`, installs `requirements.txt`, copies `src`/`config.yaml`/`README.md`, `EXPOSE 8000`, healthcheck, `CMD uvicorn rdrs.api:app --host 0.0.0.0 --port 8000` |
| `docker-compose.yml` | Well-formed: build context, port 8000, volumes for `data`/`logs`/`config.yaml`, healthcheck |
| Persistent storage | Volumes mount runtime state to the host — correct |
| Filesystem monitoring | ⚠️ **Unverified in-container** — watchdog on a bind-mounted volume can behave differently on Docker Desktop for Windows (host→container event propagation). This is a genuine untested risk for FR-2 inside Docker |
| Health check | Present and sensible |
| Python version | ⚠️ Image pins **3.13**, development venv runs **3.14.3** — untested combination |

**Docker build/run: NOT VERIFIED — BLOCKED (external: daemon unavailable).** No image was built and no container was started. Nothing was modified or built.

---

## 12. HOSTING READINESS

| Aspect | Assessment |
|---|---|
| Binds `0.0.0.0` in Dockerfile | ✅ container-reachable |
| README suggests `--host 0.0.0.0` | ✅ but `127.0.0.1` is safer for a local defensive tool |
| Secrets | ✅ none in repo; env-var friendly |
| Persistent volume for SQLite | ✅ defined |
| Health endpoint for orchestrators | ✅ `/health` + Docker HEALTHCHECK |
| **Filesystem monitoring on a cloud host** | ❌ **Not viable as-is** — the tool watches local folders; a hosted container would watch its own volume, not the user's machine |
| Single-process assumption | ⚠️ SQLite + in-process runtime state; not multi-replica safe |
| **GitHub / ZIP readiness** | ⚠️ Working tree **uncommitted**; no git history for this work |

---

## 13. DOCUMENTATION GAPS

| Guide §16 requirement | Present? |
|---|---|
| Git repository with code + clear README + setup | ⚠️ README good; **uncommitted working tree** |
| **Short description or diagram of module structure** | ❌ **MISSING** |
| Screenshots / recording of the detection | ⚠️ 50 PNGs exist under `ptmp/ui_shots/`, **not curated or referenced** |
| Sample report + log files from a detection run | ✅ real files present |
| **One-page write-up: design choices, testing, one false-positive case, improvements** | ❌ **MISSING** |

Also missing/weak: architecture section, configuration reference, limitations section, false-positive discussion, future improvements, deployment instructions.

---

## 14. EXACT BLOCKERS

1. **Docker daemon unavailable** → Phase 10 and FR-13 Docker half **BLOCKED** (genuine external dependency).
2. **Safety guard blocks pytest temp cleanup** → backend suite **NOT VERIFIED**.
3. **Finding A (STOPPED 500)** → FR-10 / FR-11 blocked pending a fix.
4. **Three Guide §16 artefacts absent** → rubric lines *Architecture & docs* and *Demo & write-up* at risk.

---

## 15. REQUIRED FIXES IN PRIORITY ORDER

| # | Fix | Affects | Why first |
|---|---|---|---|
| 1 | **Remove the shared-engine close from read endpoints** (or make `_require_engine` re-initialise / add a lock) | FR-10, FR-11, Phase 6–7 | Only live functional defect; causes 500s and stale dashboards |
| 2 | **Start Docker and run `docker compose up`**, then repeat the §15 sandbox test inside the container | FR-13, Phase 10 | Unlocks the two biggest blockers at once |
| 3 | **Run the backend pytest suite** | FR-13, Phase 9 | Needed before any lock claim; also covers fix #1 |
| 4 | **Add the three §16 artefacts**: module diagram, false-positive discussion, design/testing write-up | Rubric: Architecture & docs (15), Demo & write-up (10) | Cheapest rubric points available |
| 5 | Curate screenshots into the submission and reference them from the README | §16 item 3 | Evidence for "detection works" (30 pts) |
| 6 | Explain Persistence Disabled = in-memory in the UI and README | FR-1, FR-11 | Removes the "history disappeared" confusion |
| 7 | Commit the work to git | §16 item 1 | A repo with no history is a weak submission |
| 8 | Reconcile `python:3.13-slim` vs the 3.14.3 venv | Phase 10 | Avoids an untested runtime |
| 9 | Consider a smaller demo database (354.7 MB) | Demo polish | Cosmetic |

---

## 16. SUBMISSION READINESS ASSESSMENT

**Rubric projection (Guide §17, 100 points):**

| What is assessed | Pts | Assessment |
|---|---|---|
| Detection works | 30 | **Strong** — real burst → CRITICAL, sensible bands |
| Code quality | 15 | **Strong** — modular, typed, docstrings |
| Testing | 15 | **Good but capped** — backend suite unverified, coverage unmeasured |
| Architecture & docs | 15 | **At risk** — no architecture section/diagram |
| Feature breadth | 15 | **Strong** — DB/API/dashboard/reports; deployment unverified |
| Demo & write-up | 10 | **At risk** — no write-up |

**Overall verdict: NOT 100/100. NOT LOCKED.**

| Category | Items |
|---|---|
| Previously locked | FR01–FR13 and Phase 1–9 lock records in `reports/` — **carried over, not re-verified** |
| Re-verified this cycle | FR-1…FR-9, FR-12, Phase 1–5, Phase 8, persistence semantics, quarantine copy-only, entropy |
| Modified after lock | `api.py`, `dashboard/index.html`, `runtime.py`, `storage.py`, `monitoring.py`, `cli.py` — **lock invalid until the backend suite passes** |
| Currently verified | Harness 99/99 · live 19/19 · encoding 22/22 · persistence probe · entropy · quarantine |
| Unverified | Backend pytest · coverage % · Docker build/run · dark-theme visual pass |
| Blocked | Phase 10 / FR-13 Docker half (external) |

Per Locked Rules §3/§14, 100/100 requires all mandatory requirements implemented **and genuinely verified** with no known mandatory gaps. Findings A, B and C are mandatory gaps.

**Recommended order to reach a defensible 100/100:** fix #1 → run #3 (backend suite) → start Docker and run #2 → add #4/#5 documentation → commit (#7).

---

## AUDIT CLOSED

Read-only. **Nothing was modified, deleted, or built.** No guard was bypassed. No evidence was fabricated. No further discovery cycle started.
