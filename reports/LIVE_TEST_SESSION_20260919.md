# RDRS — LIVE TEST SESSION REPORT

**Date:** 19 September 2026, 18:37–18:52 IST
**Mode:** run-only — **no source file was modified** (67 source/config/test files hashed before and after)

---

## 1. What was asked

Run the app locally and produce real events, alerts and an incident. No code changes.

## 2. What happened — SUCCESS

A ransomware-like burst was written into the monitored folder
(`data/sandbox/live_demo2/`): 15 high-entropy files, 75 modifications, 15 renames
to `.locked`.

The running RDRS detected it and produced the full chain itself:

| Counter | Before | After |
|---|---|---|
| Events | 274 | **402** |
| Alerts | 2 | **4** |
| Incidents | 1 | **2** |
| Scores | 2 | **4** |

**New alert:** `#3 WARNING — RDRS threat score: 55/100` →
`#4 CRITICAL — RDRS threat score: 95/100`

**New incident:** `#2 CRITICAL · open · score 95.0 · 31 affected files ·
suspect WorkBuddyAI.exe`
Reason: *"Ransomware-like burst detected; signals: rapid_encryption, mass_rename,
extension_change, high_entropy"*

Screenshot: `ptmp/ui_shots/live_incident.png` — Threat Score 95.0 CRITICAL,
Events 402, Alerts 4, Incidents 2, live event rows from `live_demo2`.

---

## 3. 🔴 CRITICAL DEFECT DISCOVERED — the runtime cannot be restarted after Stop

This was found because the first burst produced **nothing**, and investigating why
uncovered a defect that makes the app permanently unusable after one click.

### Evidence

```
13:07:51  runtime.start.success              ✅
13:13:18  POST /monitor/stop  -> 200         ← Stop pressed
13:14:46  POST /monitor/start -> 409 Conflict
          runtime.start.failed: "Database must be initialized before incident response"
13:14:50  POST /monitor/start -> 409 Conflict
13:14:57  POST /monitor/start -> 409 Conflict
13:15:00  POST /monitor/start -> 409 Conflict
          GET /dashboard-data -> 500 Internal Server Error
```

Re-confirmed after the fact: three consecutive `POST /monitor/start` calls all
returned **409**.

### Root cause

`close_if_stopped()` in `src/rdrs/api.py:101-110`:

```python
def close_if_stopped(runtime, database):
    if runtime.state not in {RuntimeStatus.RUNNING, RuntimeStatus.DEGRADED}:
        database.close()
        runtime._database = None          # <-- destroys the database handle
```

It is called from the `finally` block of **seven** read endpoints
(`api.py` lines 864, 888, 910, 932, 948, 1022, 1119) — including `/status`,
`/events` and `/dashboard-data`, which the dashboard polls every 5 seconds.

`_initialize_pipeline()` in `src/rdrs/runtime.py:463-467`:

```python
if self._database is None:
    raise RuntimeError("Database must be initialized before incident response")
```

`/monitor/start` turns that `RuntimeError` into **409 Conflict**
(`api.py:816-830`).

### The chain

1. Operator presses **Stop Monitoring** → runtime `STOPPED`
2. The dashboard's 5-second poll hits a read endpoint while stopped
3. That endpoint's `finally` calls `close_if_stopped` → **`runtime._database = None`**
4. Operator presses **Start Monitoring** → `start()` → `_database is None` →
   `RuntimeError` → **409**
5. Any read that needs the database returns **500**

**Impact:** after a single Stop, the runtime can never be restarted through the
API and read endpoints can fail with 500. **Only restarting the process
recovers it.**

**Severity: Critical.** Affects **FR-01** (runtime lifecycle), **FR-10** (API),
**FR-11** (dashboard). Touches the permanently locked `api.py` and `runtime.py`.

**Why tests missed it:** the front-end harness drives the control buttons with
mocked state, and the E2E stops the runtime only at teardown — nothing exercises
stop → start on a live runtime.

**I did not fix it.** You asked for run-only.

---

## 4. ⚠️ `config.yaml` changed during the session — a real user edit, handled correctly

`config.yaml` moved from `ca4afe3f…` (1851 bytes) to `7006bae4…` (1852 bytes).
I stopped to investigate rather than assume.

**The diff is exactly one line:**

```diff
 persistence:
-  enabled: true
+  enabled: false
```

**What this shows — the fix cycle from yesterday worked:**

| Check | Result |
|---|---|
| `watch_paths` | **`['data']` — preserved** ✅ (was destroyed before the fix) |
| Comments | **preserved** ✅ |
| 12 × `POST /settings` from a browser tab | **only 1 write** — the no-op guard working ✅ |
| Backup before the write | `config.yaml.bak-20260919T131100Z` created ✅ |

So: someone set **Persistence = Disabled** in the Settings page and pressed Save
repeatedly. Only the intended field changed, nothing was destroyed, and a backup
exists. **This is the fixed behaviour working on a real user action.**

**Note:** `persistence.enabled: false` means the database is no longer persisted.
If that was not intentional, set it back to **Enabled** in Settings — or tell me
and I'll restore it (that is a one-line change).

---

## 5. Why the first burst appeared to fail

It did not fail — the runtime had been **stopped** while the burst ran, so nothing
was monitored. Re-running the identical burst on a fresh runtime produced the full
event/alert/incident chain immediately.

An early signal I misread: `rdrs.file.event` log lines were **0** even when events
*were* being detected, so that grep is not a valid liveness check. Detection is
confirmed through the `/events` API instead.

---

## 6. Files created by the test (no code touched)

| Path | Purpose |
|---|---|
| `data/sandbox/live_demo/` | first burst (15 `.locked` files) — not detected |
| `data/sandbox/live_demo2/` | second burst — detected, produced the incident |
| `data/sandbox/detect_probe.txt`, `probe_sandbox.txt` | detection probes |
| `data/probe_root.txt` | detection probe |
| `ptmp/trigger_live_burst.py`, `ptmp/runonly_server.log` | test scaffolding |

**0 source, test, config or lock files modified by me.** `config.yaml` changed only
through the dashboard's own Save, which was a user action.

---

## 7. Current state

The server is **running** on http://127.0.0.1:8000 with runtime `RUNNING`, all 7
subsystems healthy, showing **Incident #2 (CRITICAL, 95.0)**.

## 8. Recommended next step

**Fix the restart defect (Section 3) before any submission** — an operator who
presses Stop once cannot start monitoring again without restarting the process.
The fix is small (re-initialise the database on start instead of refusing, and
stop `close_if_stopped` from nulling the handle), plus a regression test for
stop → start.

Say the word and I'll do it as a controlled defect-fix cycle on the locked files.
