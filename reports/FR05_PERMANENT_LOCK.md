# FR-05 — PERMANENT LOCK RECORD

**Item:** FR-05 — Store events, processes, alerts and incidents in SQLite
**Date:** 2026-09-18
**Suite at lock time:** 610 passed, 0 failed · front end 32/32 · E2E 30/30 · encoding+safety 22/22

---

## 1. Item name and scope

FR-05 covers durable storage: schema creation, the write and read paths for all
five record types, legacy-database migration, and the key contract the API and
dashboard consume.

**Exact requirement text**

- **FSD FR-5:** "Store events, processes, alerts and incidents in SQLite."
- **FSD §6 Outputs:** "Threat scores, alerts, logs, incident records, REST API
  responses, dashboard updates, reports."
- **Build Guide Phase 3:** "Use SQLAlchemy with SQLite to create tables for
  events, processes, scores, alerts, and incidents." · "Save file events and
  process snapshots into the database as they happen." · Done when: *"your
  database contains rows for file events and process snapshots."*

---

## 2. Every inspected file

| # | File | Responsibility | Inspected |
|---|---|---|---|
| 1 | `src/rdrs/storage.py` | 9 SQLAlchemy models, `DatabaseManager` (init, migrate, write, read, count, close) | yes — all 45 methods |
| 2 | `src/rdrs/dashboard_data.py` | read projections over the stored rows | yes |
| 3 | `src/rdrs/api.py` | endpoints serving stored rows | yes |
| 4 | `src/rdrs/dashboard/index.html` | renders stored rows | yes |
| 5 | `src/rdrs/runtime.py` | persistence call sites | yes |

---

## 3. FSD requirement → code mapping

| Requirement | Code | Verified |
|---|---|---|
| events in SQLite | `EventRecord` / `record_file_event` / `list_events` | round-trip with `metadata`, `previous_path`, `new_path` |
| processes in SQLite | `ProcessRecord` / `record_processes` / `list_processes` | 392-row live capture |
| alerts in SQLite | `AlertRecord` / `record_alert` / `list_alerts` | round-trip with `evidence` |
| incidents in SQLite | `IncidentRecord` / `record_incident` / `get_incident` | full metadata incl. `affected_files`, `suspect_process` |
| scores in SQLite (Phase 3) | `ScoreRecord` / `record_score` / `list_scores` | round-trip; 0–100 enforced |
| §6 outputs are real | readers feed API, dashboard and reports | verified |

**The five required tables exist:** `events`, `processes`, `scores`, `alerts`,
`incidents` — plus four supporting tables (`runtime_state`,
`subsystem_health`, `user_sessions`, `network_connections`). All are SQLAlchemy
declarative models, not raw SQL.

**Persistence verified:** rows survive `close()` → reopen. **Linkage verified:**
`incident.score_id` and `incident.alert_id` resolve to the real score and alert
rows.

---

## 4. Build Guide Phase 3 → code mapping

| Instruction | Code | Verified |
|---|---|---|
| "Use SQLAlchemy with SQLite" | `Base(DeclarativeBase)` + `create_all` + `Session` | 9 declarative models |
| "create tables for events, processes, scores, alerts, and incidents" | all five present | confirmed via `sqlite_master` |
| "Save file events and process snapshots … as they happen" | `record_file_event`, `record_processes` (one transaction per capture) | rows appear during a live run |
| Done when: DB contains rows for events and process snapshots | live session | 392 process rows + event rows |

---

## 5. Locked Rule verification

| Rule | Verified |
|---|---|
| §3 100/100 only | this record |
| §4 no fake logic / fabricated data | JSON decoders never invent values; `record_score` rejects out-of-range scores |
| §5 real verification only | every claim from live runs |
| §12 reports from real stored data | readers feed reports from stored rows |
| §13 tests test the real implementation | 18 tests against real SQLite, 7 proven to fail pre-fix |

---

## 6. Defects found and exact fixes

| # | Defect | File | Fix |
|---|---|---|---|
| 1 | **Incomplete schema migration.** `_migrate_schema` added only the columns in a hand-maintained `_ADDED_COLUMNS` map, which had drifted from the model — `incidents.status`, `evidence_json` and `created_at` were never added. A legacy database failed every query with `sqlite3.OperationalError: no such column: incidents.status`. | `storage.py` | the migration now derives the column set from `Base.metadata` and builds the DDL per column (`_migration_ddl`, adding a type-appropriate DEFAULT for NOT NULL columns). Cannot drift again. |
| 2 | **Dashboard timestamps never rendered.** The dashboard called `formatTimestamp(event.timestamp)` / `(alert.timestamp)` / `(incident.timestamp)` at **6 sites**, but every reader returns **`created_at`** — so the Events, Alerts and Incidents tables showed no timestamp. (FR-11 explicitly requires "Correct timestamps".) | `dashboard/index.html` | all six now use `created_at`. |

**Migration evidence:** a legacy `incidents(id, severity, description)` table
went from **3 → 12 columns** (all 9 missing added), the legacy row survived,
migrated rows became readable, and a second `initialize()` was idempotent. A
near-empty `events(id)` table was migrated to all 8 columns. Every model table
was verified to end up complete.

---

## 7. Tests and real runtime verification

| Layer | Command | Result |
|---|---|---|
| FR-05 suite | `pytest tests/unit/test_storage_migration.py` | **18 passed** |
| Full suite | `pytest tests -q` | **610 passed, 0 failed** |
| Front end | `node ptmp/dashboard_frontend_harness.mjs` | **32 passed** |
| End-to-end | `python ptmp/e2e_bugfix_verification.py` | **ALL 30 CHECKS PASSED** |
| Encoding + safety | `python ptmp/encoding_safety_scan.py` | **ALL 22 CHECKS PASSED** |
| Live probe | five tables round-trip real rows; linkage intact; survives reopen | verified |
| Live probe | legacy DB migration 3 → 12 columns, idempotent | verified |
| Live probe | dashboard/API wiring: every field the dashboard reads is served | verified |

**Discrimination:** against the pre-fix migration the FR-05 suite produced
**7 failures**; reverting the dashboard to `.timestamp` makes the timestamp test
fail, and re-applying the fix makes it pass.

---

## 8. Evidence that no fixable defect remains

- All five required tables exist as SQLAlchemy models and round-trip real data.
- Legacy databases migrate to the complete model schema, idempotently, without
  losing rows.
- Every field the dashboard reads is served by the API (checked against live
  endpoint payloads with seeded data).
- Nothing in FR-05 is BLOCKED; nothing is left as a known issue.

**Documented, deliberate non-defects (verified, not overlooked):**

- `_decode_json_list("{bad")` returns `['{bad']`. This is intentional: a legacy
  row may hold a bare path string where a JSON array belongs, and discarding it
  would lose the only record of which file was affected.
- `list_*` clamps `limit` to ≥ 1 (`max(1, int(limit))`). Deliberate: it prevents
  an accidental unbounded query.
- `AlertRecord` exposes `severity` while `ScoreRecord` exposes `level`. Different
  tables, different semantics; the dashboard reads each correctly.

---

## 9. Exact score

**FR-05: 100/100**

---

## 10. Permanent status

**FROZEN: YES**
**LOCKED: YES**
**100/100: YES**
**REWRITE REQUIRED: NO**
**FUTURE REWRITE REQUIRED: NO**

**Locked files:** `src/rdrs/storage.py` (complete file) · the FR-05 read
projections in `src/rdrs/dashboard_data.py` · the FR-05 endpoints in
`src/rdrs/api.py` · `src/rdrs/dashboard/index.html` (timestamp wiring).

> **Note on the §3 complete-file rule.** `dashboard_data.py`, `api.py` and
> `index.html` also serve FR-10/FR-11/FR-12 and will receive further
> *additions* for those FRs. Per §3 those files are therefore **not yet
> eligible for complete-file locking**; they will be locked complete once the
> last FR that touches them is finished. `storage.py` has no pending FR work and
> is locked complete here.
