# FR-10 + PHASE 6 — PERMANENT LOCK RECORD

**Items:** FR-10 (REST API) · Build Guide Phase 6 (REST API)
**Date:** 2026-09-18
**Suite at lock time:** 681 passed, 0 failed · front end 32/32 · E2E 30/30 · encoding+safety 22/22
**Config integrity:** hash unchanged by a full suite run

---

## 1. Scope

FR-10 covers the HTTP surface. Phase 6 covers the seven named FastAPI
endpoints, JSON responses, and the automatic documentation page.

## 2. Exact requirements (verbatim)

- **FSD FR-10:** "Provide REST API endpoints."
- **Phase 6 Goal:** "Expose the system's state over HTTP so other tools (and your dashboard) can read it."
- **Task 1:** "Use FastAPI to add endpoints: **GET /health, /status, /alerts, /events, /reports; POST /scan and /settings**."
- **Task 2:** "Return JSON from every endpoint."
- **Task 3:** "Use FastAPI's automatic documentation page to test your endpoints."
- **Command:** `uvicorn app.api.main:app --reload` → `http://127.0.0.1:8000/docs`
- **Done when:** "You can call /status and /alerts in the browser and get JSON responses back."

## 3. Every inspected file

`src/rdrs/api.py` · `src/rdrs/dashboard/index.html` (API map) ·
`src/rdrs/dashboard_data.py` · `src/rdrs/runtime.py` · `src/rdrs/storage.py` ·
`tests/unit/test_api_phase6.py` (new) · `tests/unit/test_api.py` (rewritten earlier)

## 4. Requirement → code mapping

| Requirement | Route | Verified |
|---|---|---|
| GET /health | `@app.get("/health")` | 200 + JSON |
| GET /status | `@app.get("/status")` | 200 + JSON |
| GET /alerts | `@app.get("/alerts")` | 200 + JSON |
| GET /events | `@app.get("/events")` | 200 + JSON |
| GET /reports | `@app.get("/reports")` | 200 + JSON |
| POST /scan | `@app.post("/scan")` | 200 + JSON, real filesystem scan |
| POST /settings | `@app.post("/settings")` | 200 + JSON, persists |
| JSON from every endpoint | `application/json` content type | asserted per endpoint |
| automatic docs | FastAPI `/docs` + `/openapi.json` | both 200; all seven documented |
| Done-when | `/status` + `/alerts` | 200 + JSON |

**Extra routes beyond the required seven** (all real, all used):
`/processes`, `/incidents`, `/dashboard-data`, `/reports/generate`,
`/monitor/start`, `/monitor/stop`.

**No dead UI control:** every path in the dashboard's `API` map was checked
against `api.py` — all 13 exist. A test asserts this, so a UI control can never
point at a missing route again.

## 5. Locked Rules

§3 · §4 (endpoints return stored data, never fabricated) · §5 real verification ·
§9 safety (no destructive route) · §11 (every UI control hits a real API) ·
§13 real tests.

## 6. Defects found

**None in production code** for FR-10/Phase 6. The endpoint set, JSON contract,
docs page and validation were all already correct.

One earlier defect in this area is worth restating because it was found here:
**`tests/unit/test_api.py` rewrote the shipped `config.yaml`** (a module-level
`TestClient(app)` with no runtime config → `POST /settings` persisted to the
real file). Fixed and permanently guarded by `tests/conftest.py`.

**Checked and correctly absent:** there is no `/scores` route. It is **not** one
of Phase 6's seven endpoints, and nothing calls it — the dashboard reads scores
through `/dashboard-data`. Not a defect.

## 7. Tests and real runtime verification

| Layer | Result |
|---|---|
| `test_api_phase6.py` (new, 20 tests) | **20 passed** |
| Full suite | **681 passed, 0 failed** |
| Front end | **32 passed** |
| End-to-end | **ALL 30 CHECKS PASSED** |
| Encoding + safety | **ALL 22 CHECKS PASSED** |
| Config integrity | unchanged |

The new suite covers all seven endpoints, the JSON content type, the Done-when
pair, `/docs` and the OpenAPI schema, limit validation (422), settings
validation (400), and the dashboard-to-route existence check.

## 8. No fixable defect remains

Every Phase 6 task and the Done-when condition verified against real behaviour.

## 9. Score

**FR-10: 100/100 · Phase 6: 100/100**

## 10. Status

**FROZEN: YES · LOCKED: YES · 100/100: YES · REWRITE REQUIRED: NO · FUTURE REWRITE REQUIRED: NO**

**Complete-file locked:** none newly claimed. `api.py` and
`dashboard_data.py` are also read by FR-11 (dashboard) and FR-12 (reports); they
become eligible once those gates pass. `index.html` likewise.
