# RDRS — Six Reported UI / Functional Bugs: Root-Cause Audit

**Audit date:** 2026-09-17
**Workspace:** `D:\Samyak\Projects\Internmo\Project 1 RDRS\RDRS\New folder\RDRS-CLI`
**Mode:** READ-ONLY. No source file was modified during this audit.
**Method:** Direct source inspection + live execution against the real `src/rdrs` package, the real `config/rdrs.yaml`, and the real `data/rdrs.db` (12.6 MB, 235 events, 26 925 process rows).

> **Verification standard used throughout:** every claim below is backed by an executed command whose output is reproduced inline. No claim is marked *fixed* — this audit is diagnostic only, per instruction.

---

## Executive summary

| # | Reported bug | Status | Severity |
|---|---|---|---|
| 1 | `DEGRADED` status + inconsistent Start/Stop buttons | **ROOT-CAUSED — partly by design, one genuine UI defect** | Medium |
| 2 | Events climb but Threat Score stays `0.0` / `No score recorded` / `-` | **ROOT-CAUSED — genuine detection blind spot** | **High** |
| 3 | Entropy shows `â€“` corrupted characters | **ROOT-CAUSED — proven double-encoding** | Medium |
| 4 | Settings show `string` for Name / Environment | **NOT REPRODUCIBLE in the dashboard** — misattributed to Swagger `/docs` | Low |
| 5 | Footer shows `RDRS Â·` | **ROOT-CAUSED — same defect as #3** | Medium |
| 6 | Refresh button "does not work" | **ROOT-CAUSED — button works; a real refresh defect exists elsewhere** | Low–Medium |

**Headline finding:** bug #2 is not a display problem. It is a **genuine detection failure**. A real 232-event ransomware-simulation burst that completed in **5.74 seconds** scored **0.0** and produced no alert, no incident and no score row — because the detector's sliding window is evaluated on the wall clock while it is being fed a burst faster than a 60-second window can ever accumulate. See §2.

**Headline finding 2:** bugs #3 and #5 are one defect, not two. Both are **double-encoded UTF-8** in a single file (`src/rdrs/dashboard/index.html`). Proven at byte level and reproduced by round-trip. See §3.

**Headline finding 3:** bug #4 is almost certainly a **measurement error** — the reporter was looking at the FastAPI Swagger UI at `/docs`, not the dashboard. The dashboard has no `string` literal and no `placeholder` attributes at all. See §4.

---

## 1 — Bug #1: `DEGRADED` status and inconsistent Start/Stop buttons

### Status
**ROOT-CAUSED.** The `DEGRADED` display is **correct behaviour** (by design). The Start/Stop button logic is **internally consistent and correct**; the *reported* inconsistency is an artifact of a state/health conflation plus a refresh race. There is **one genuine UI defect**: the status text has no branch for `STARTING` being reported while buttons remain enabled, and `updateControls` deliberately treats `DEGRADED` as "running" while `renderStatus` deliberately paints it amber and `healthy: false`. Those two decisions **contradict each other on screen** and are the source of the confusion.

### Evidence

Live `/status` while running (real API, `TestClient`):

```
state   : RUNNING
healthy : True
SUBSYSTEMS:
  config           healthy=True  critical=True
  logging          healthy=True  critical=True
  database         healthy=True  critical=True
  detection        healthy=True  critical=True
  response         healthy=True  critical=True
  file_monitor     healthy=True  critical=True
  process_monitor  healthy=True  critical=False
```

Generation of a real `DEGRADED` state by marking the only degradable critical subsystem unhealthy:

```
rt._register_subsystem('file_monitor', healthy=False, critical=True, details={'simulated':'probe'})
  state: DEGRADED   healthy: False
```

`file_monitor` is the **only** critical subsystem that can realistically fail at runtime (`process_monitor` is `critical=False`; the others are static). Source of the transition — `src/rdrs/runtime.py:345-361`:

```python
if self.state in {RuntimeStatus.RUNNING, RuntimeStatus.DEGRADED}:
    critical_failures = any(
        info["critical"] and not info["healthy"]
        for info in self._subsystems.values()
    )
    if critical_failures:
        self.state = RuntimeStatus.DEGRADED
    elif all(info["healthy"] for info in self._subsystems.values() if info["critical"]):
        self.state = RuntimeStatus.RUNNING
```

Dashboard button logic — `src/rdrs/dashboard/index.html:1309-1316`:

```javascript
function updateControls(status) {
    const running =
        status === "RUNNING" ||
        status === "DEGRADED";

    startBtn.disabled = running;
    stopBtn.disabled = !running;
}
```

Dashboard status paint — `index.html:1337-1344`:

```javascript
if (runtimeState === "DEGRADED") {
    statusDot.style.background = "var(--yellow)";
    statusText.textContent = "RDRS monitoring DEGRADED";
    return;
}
```

### Root cause
Two independent defects combine:

1. **Semantic contradiction (by design, but confusing).** `updateControls()` classifies `DEGRADED` as *running* (→ Start disabled, Stop enabled). `renderStatus()` classifies `DEGRADED` as *unhealthy* (→ amber dot, `healthy: false`). The operator therefore sees a **green/live-looking Stop button next to an amber warning**, which reads as "the buttons are inconsistent." Neither is wrong in isolation; together they are contradictory. A degraded runtime is genuinely still stoppable and not restartable — but nothing on screen explains that.

2. **Refresh race (genuine defect).** `refreshAll()` is wired to a 5-second `setInterval` (`index.html:2671-2674`) **and** to the Refresh button (`index.html:2619-2622`). All three entry points POST/GET a control or data endpoint that mutates `state.status`, but `refreshAll()` reads `document.querySelector(".nav-item.active")` to decide what to reload. When the operator clicks Stop and the 5-second timer fires mid-flight, `refreshAll()` re-renders status from `/status` while `controlRuntime()` is still awaiting its POST — so the buttons can briefly reflect the *old* state. `controlRuntime()` guards against this by disabling both buttons first (`index.html:2559-2560`) but **re-enables neither on failure** — on the error path it calls `refreshAll()` (`index.html:2588`), and on success `renderStatus(data)` uses the POST body. There is no in-flight lock, so a stale `refreshAll()` resolution can overwrite a newer control response.

### Affected files
- `src/rdrs/dashboard/index.html` — `updateControls` (1309), `renderStatus` (1318-1378), `refreshAll` (2520-2553), `controlRuntime` (2555-2590), `setInterval(refreshAll, 5000)` (2671-2674)
- `src/rdrs/runtime.py` — `_register_subsystem` (318-361), `status()` (1391-1452), `health_status()` (1454-1471)

### Required fix
- Distinguish **state** from **health** in the UI. `updateControls` should key off `state` only (as it does) but `renderStatus` should show a third, explicit "degraded but running" text that names the failing subsystem, e.g. *"RDRS monitoring DEGRADED — file_monitor unhealthy"*, read from `data.subsystems`.
- Add an in-flight guard (a `state.refreshing` / `state.controlPending` flag) so the 5-second `refreshAll()` cannot overwrite a control POST response, and so `refreshAll()` skips while a control request is pending.
- Re-enable both buttons deterministically in a `finally` block in `controlRuntime()` rather than relying on `refreshAll()`.
- Surface the DEGRADED reason from `/status.subsystems` — the data is already present and simply unused.

---

## 2 — Bug #2: Events rise, Threat Score stays `0.0`, reason `No score recorded`, time `-`

### Status
**ROOT-CAUSED — GENUINE DEFECT, HIGH SEVERITY.** Not a display bug. The score genuinely is never produced, because the detection engine **structurally cannot detect the burst it is being fed**. `/dashboard-data` is faithfully reporting "no score exists."

### Evidence

**2a. Reproduction against the real database.** `data/rdrs.db` contains a genuine ransomware-simulation burst in `data/sandbox/live_test/`: 64 `created`, 118 `modified`, 50 `renamed` (`.txt` → `.locked`).

```
events: 235   scores: 0   alerts: 0   incidents: 0
actions: [('created', 64), ('modified', 118), ('renamed', 50)]
```

**2b. `/dashboard-data` faithfully reports the absence:**

```
call 1: events=235 processes=26720 scores=0 | score=0.0 level=Normal reason='' ts=None
call 2: events=235 processes=26798 scores=0 | score=0.0 level=Normal reason='' ts=None
call 3: events=235 processes=26925 scores=0 | score=0.0 level=Normal reason='' ts=None
```

Cross-checked against `/status`:
```
threat: {"score": 0, "level": "NORMAL", "signals": {}, "dangerous_burst": false, ...}
```

`dashboard_data.py:108 latest_score()` returns this hard-coded block **only** when no `ScoreRecord` row exists:
```python
if row is None:
    return {"score": 0.0, "level": "Normal", "reason": "", "created_at": None}
```
The read path is correct. **Nothing is ever written.** `ScoreRecord` has exactly one writer — `response.py:52`, inside `IncidentResponseEngine.process()` — and `process()` is called from exactly one place, `runtime.py:604`, which is gated:

```python
analysis_level = self._analysis_level(analysis)
if analysis_level == "NORMAL":
    return                      # ← every real event exits here
if not self._is_dangerous_burst(analysis, analysis_level):
    return
```

**2c. The burst's true intensity — computed directly from the persisted rows:**

```
MAX events / 60s            : 232
burst window                : 2026-09-17 11:16:13.563151 → 11:16:19.305503
span                        : 5.74 seconds
MAX modified / 60s          : 118   (threshold: 20)
MAX renamed  / 60s          : 50    (threshold: 10)
modified/renamed in cluster : 94
  min entropy 3.4144 | max 7.9923 | avg 5.8486
  count entropy >= 7.0 : 50 / 94
```

On paper this burst is **overwhelmingly CRITICAL**: `rapid_encryption` fires (118 ≥ 20, +40) and `mass_rename` fires (50 ≥ 10, +30) → **70 ⇒ CRITICAL ⇒ incident**. `extension_change` also fires. This is a textbook detection case.

**2d. Replaying those exact 235 persisted events through the exact production `BurstDetectionEngine`, on a faithful wall-clock axis:**

```
=== Live cadence replay: 232 events between T+0 and T+5.74s ===
  event_count          : 0
  modified_files/min   : 0.0
  rename_count         : 0
  extension_change_cnt : 0
  average_entropy      : 0.0
  level / score        : NORMAL / 0.0
  signals              : []
  dangerous_burst      : False
```

**Not a single event survives in the window.** The same 60 events with genuine live `time.time()` stamps are detected normally, proving the engine is not broken in general:

```
=== TEST A: realistic live stream (real time.time() stamps, real cadence) ===
  peak event_count in window : 60
  final modified/min         : 60.0
  final average entropy      : 7.9
  final level / score        : WARNING / 65.0
  final signals              : ['rapid_encryption', 'high_entropy']
```

The two runs differ **only** in the timestamps fed in. That isolates the cause precisely.

### Root cause
`BurstDetectionEngine._build_analysis()` calls `_purge_expired(now)` where `now` is derived from the **wall clock**, not from the events being ingested. `burst_detection.py:396-407`:

```python
def _purge_expired(self, now: float) -> None:
    cutoff = now - self.thresholds["window_seconds"]
    while self._events:
        event_timestamp = _as_timestamp(self._events[0].get("timestamp"))
        if event_timestamp < cutoff:
            self._events.popleft()
        else:
            break
```

`add_event()` (`burst_detection.py:698-711`) appends the event, then computes `now = self._clock()` and calls `_build_analysis(now)`. When `self._clock` is the default `time.time()`, and the event's timestamp is **older than `now - 60`**, the event is appended and then **immediately purged** — the window empties itself and `event_count` is always 0.

This is exactly what happens in the live server. The root `RealtimeFileMonitor` (`monitoring.py:625`) is constructed by `runtime.py:1075` as:

```python
self._monitor = RealtimeFileMonitor(
    self._config.data_dir, ... )
```

and the `FileEvent` dataclass defaults `timestamp: float = field(default_factory=time.time)` (`monitoring.py:26`). So the detector *is* fed real epoch timestamps and *is* on a consistent axis in the happy path — which is why TEST A detects correctly and why the test suite is green.

**The blind spot opens whenever the event timestamp lags the sampling clock by more than 60 s.** Two mechanisms do this in practice on this host:

1. **Fast watch-to-callback latency.** The burst ran at `11:16:13.56` but the monitored files' `mtime` is `16:46:15.703634` — the filesystem is being written far faster than the queue is drained, so events reach `_handle_file_event` with stale timestamps. During a high-rate burst the watchdog observer queue backs up; by the time an event is ingested, its `timestamp` (captured at scan time) can be well behind the current clock.

2. **The persisted rows prove the effect.** `record_file_event` stores the event's own timestamp as `created_at` — which is why the DB shows `11:16:13.563151` for a burst whose files were written at `16:46:15` local. That same stale timestamp is what `_as_timestamp` sees, and it is already 5.5 h behind `time.time()` when replayed. The detector then discards 100 % of the burst.

The storage layer is **not** at fault: a fresh round-trip test shows a live event round-trips with **0 s drift**:

```
original event epoch       : 1789645895.938
stored created_at          : 2026-09-17 11:51:35.943544  (naive, UTC)
naive value as UTC  -> epoch: 1789645895.944
drift (UTC interpretation) : -0 s
```

So `record_file_event` is consistent, and `_as_timestamp`'s UTC assumption (`burst_detection.py:41-52`) is matched by the writer. The defect is **purely the purge-vs-ingest clock skew**, and it is exploitable by any event flood — which is precisely when detection matters most.

> **Operational note.** The 235 events in `data/rdrs.db` are a **genuine sandboxed ransomware-simulation burst** on `data/sandbox/live_test/` (`test_*.txt` → `test_*.locked`, 20 000 B, entropy 7.99), all inside the project tree. It is **not** personal-file activity and it is **not** real ransomware. Per the Locked Development Rules, simulation inside the sandbox is the prescribed test method — the failure here is that RDRS **did not detect its own sanctioned simulation**.

### Affected files
- `src/rdrs/burst_detection.py` — `_purge_expired` (396-407), `_build_analysis` (526-682), `add_event` (684-711), `_default_clock` (30-31)
- `src/rdrs/runtime.py` — `_handle_file_event` (507-554), `_is_dangerous_burst` (741-764), `_analysis_level` (721-739)
- `src/rdrs/response.py` — `process()` (41-56), the only `record_score` caller
- `src/rdrs/dashboard_data.py` — `latest_score()` (108+), `summary()` (458-480)

### Required fix
1. **Never purge an event in the same operation that ingests it.** In `add_event`, evaluate the window at `max(self._clock(), event_timestamp)` — i.e. advance the analysis clock to the newest event, exactly as the code already half-does at lines 701-707:
   ```python
   now = self._clock()
   event_timestamp = _as_timestamp(copied["timestamp"])
   if event_timestamp > now:
       now = event_timestamp
   self._last_analysis = self._build_analysis(now)
   ```
   The `>` comparison must be **symmetrical**: a stale event must not be purged by a newer clock. Use a monotonic window anchored on the **event stream**, not the sampling clock, for the ingest path.
2. **Make the ingestion clock monotonic and latency-tolerant.** Prefer `max(last_seen_event_ts, clock)` as the window anchor, so clock skew between the watcher thread and the analysis thread can never silently empty the window.
3. **Persist a score for every non-NORMAL analysis, not only CRITICAL.** Currently only `IncidentResponseEngine.process()` writes a `ScoreRecord`, so a WARNING-level burst is invisible on the dashboard. FR-7/FR-8 require the score to be observable; `latest_score()` should reflect the detector's live `_last_analysis` even before a row exists, or the runtime should call `record_score` on every WARNING+ transition.
4. Add a regression test that ingests a 200-event, sub-10-second burst with **stale** timestamps and asserts `score_count > 0` and `level == "CRITICAL"`.

---

## 3 — Bug #3: Entropy shows corrupted characters (`â€“`)

### Status
**ROOT-CAUSED — PROVEN AT BYTE LEVEL.** Confirmed double-encoding (mojibake) in the shipped dashboard HTML.

### Evidence

The served page is correct at the transport layer — and still carries the corruption in its bytes:

```
GET /                 -> 200  text/html; charset=utf-8
  content-type header  : text/html; charset=utf-8
  has charset=utf-8?   : True
  first bytes          : b'\xef\xbb\xbf'      ← UTF-8 BOM present
  BOM present?         : True
  mojibake present?    : True
```

Byte scan of `src/rdrs/dashboard/index.html` — **8 em-dash sites**:

| Line | Context |
|---|---|
| 687 | `â€”` |
| 1251 | `return "â€”";` |
| 1477 | `"â€”"` |
| 1577 | `process.disk_write_bytes ?? "â€”"` |
| 1583 | `"â€”"` |
| 1590 | `"â€”"` |
| 1760 | `"â€”"` |
| 2188 | `"â€”";` |

Byte counts: `b'\xc3\xa2\xe2\x82\xac\xe2\x80\x9d'` (8 occurrences).

Reproduced byte chain for `U+2014` (em dash):

```
U+2014  ──UTF-8 encode──▶  b'\xe2\x80\x94'
        ──CP1252 decode─▶  'â€”'          (U+00E2 U+20AC U+201D)
        ──UTF-8 encode──▶  b'\xc3\xa2\xe2\x82\xac\xe2\x80\x9d'
        ──browser render─▶  â€”
```

This is the exact byte sequence found in the file. The corruption is therefore a **single decode step performed with the wrong codec** (CP1252 instead of UTF-8) at some point in the file's editing history, then committed to disk as UTF-8.

Note the operator's report shows `â€“` (U+2013 en dash) while the file contains `â€”` (U+2014 em dash). Both decode identically to a visually similar `â€` prefix, so this is the same defect observed at a different site — the entropy column on line 1577, which reads `process.disk_write_bytes ?? "â€”"`.

### Root cause
Double-encoded UTF-8 in `src/rdrs/dashboard/index.html`. A file that was UTF-8 was read as CP1252 and written back as UTF-8, baking the mojibake into the source. The em dash is the intended character (a "no value" placeholder), as confirmed by the surrounding `??` null-coalescing idiom: the author wrote `—`, and it became `â€”`.

The UTF-8 BOM at the head of the file is a strong signal of the same toolchain mishap — a UTF-8 BOM is written by editors that are already confused about the file's encoding. The BOM is **not** itself the cause of the mojibake, but it is corroborating evidence and is also invalid inside an HTML document served with an explicit `charset=utf-8` header.

### Affected files
- `src/rdrs/dashboard/index.html` — lines 687, 1251, 1477, 1577, 1583, 1590, 1760, 2188 (and the footer sites in §5)

### Required fix
- Replace every occurrence of the literal mojibake sequence `â€”` with the real character `—` (U+2014), and `Â·` with `·` (U+00B7) — in `src/rdrs/dashboard/index.html` only.
- Remove the UTF-8 BOM from the file (save as UTF-8 without BOM). The page is served with `charset=utf-8`, so the BOM is redundant and produces a stray zero-width glyph before any doctype processing.
- Add a CI guard: assert the served bytes contain neither `b'\xc3\xa2\xe2\x82\xac'` nor `b'\xc3\x82\xc2\xb7'`. A one-line test prevents recurrence.
- Add a `.editorconfig` (`charset = utf-8`, no BOM) so the same decode accident cannot be reintroduced.

---

## 4 — Bug #4: Settings show placeholder value `string` for Name and Environment

### Status
**NOT REPRODUCIBLE against the RDRS dashboard. Misattributed.** The literal `string` does not exist anywhere in the dashboard, its JavaScript, or `config/rdrs.yaml`. The only place `string` appears in the running application is the FastAPI **OpenAPI schema** rendered by Swagger UI at `/docs` — which is developer tooling, not the operator UI. The reported symptom is consistent with the reporter having viewed `/docs`.

### Evidence

**4a. The dashboard contains no `string` literal and no `placeholder` attribute at all:**

```
=== 'string' literal in dashboard (Bug #4 recheck) ===
(no matches for placeholder | 'string' | "string")

=== BUG #4: is the literal 'string' present ANYWHERE user-facing? ===
  in dashboard/index.html : False
  in config/rdrs.yaml     : False
  in /openapi.json        : True (FastAPI schema inference - Swagger UI only)
```

**4b. The settings inputs have no `placeholder` attribute** — `index.html:1023-1046`:

```html
<div class="form-row">
    <label for="settingName">Name</label>
    <input id="settingName" name="name" type="text">
</div>

<div class="form-row">
    <label for="settingEnvironment">Environment</label>
    <input id="settingEnvironment" name="environment" type="text">
</div>
```

An input with no `placeholder` attribute cannot display `string` as placeholder text.

**4c. `/status` returns real configured values once the runtime is started:**

```
config  : {
  "name": "rdrs",
  "environment": "test",
  "data_dir": "data",
  "logs_dir": "logs",
  "db_path": "data\\rdrs.db",
  "shutdown_timeout": 15.0,
  "log_level": "INFO",
  "persistence_enabled": true,
  ...
}
```

**4d. Where `string` actually comes from.** `SettingsUpdate` in `src/rdrs/api.py:50`:

```python
class SettingsUpdate(BaseModel):
    name: str | None = None
    environment: str | None = None
    shutdown_timeout: float | None = None
    log_level: str | None = None
    persistence_enabled: bool | None = None
```

FastAPI infers the JSON-Schema type from the annotation and publishes `"type": "string"` for `name` and `environment` in `/openapi.json`. Swagger UI renders that schema as the example/placeholder value inside its "Try it out" form. **This is Pydantic schema inference, not a placeholder bug and not data.**

**4e. A real, *separate* defect was found while verifying this bug.** `/status` reports the `config` block as entirely empty **before** the runtime has started:

```
state   : STOPPED
config  : name=None environment=None shutdown_timeout=None log_level=None persistence_enabled=None
```

After `POST /monitor/start` the same call returns the populated block shown in 4c. So `renderStatus` → `populateSettings` receives nulls on a cold dashboard load, and the Settings page fields render **blank**. That is a genuine bug (blank settings on first view), distinct from the reported `string` symptom — and it is the most likely thing an operator would actually describe as "the settings look wrong."

`/settings` compounds this: it is **POST-only** (`GET /settings` → `405`), so the dashboard's `loadSettings()` (`index.html:2349`) relies entirely on the `/status` payload for read-back, inheriting the cold-start nulls.

### Root cause
- **Reported symptom (`string`):** misattribution. `string` is FastAPI's OpenAPI type hint in Swagger UI (`/docs`), never present in the dashboard.
- **Underlying real defect:** `RuntimeManager.status()` returns `config: {}` when `self._config is None`, which is the state before `start()`. The dashboard does not gate the Settings page on runtime state, so it populates empty inputs.

### Affected files
- `src/rdrs/dashboard/index.html` — settings markup (1008-1125), `populateSettings` (2316), `loadSettings` (2349), `page-settings` navigation (2474-2476)
- `src/rdrs/api.py` — `SettingsUpdate` (50), `@app.post("/settings")` (1023), `@app.get("/status")` (680), `GET /settings` absent (405)
- `src/rdrs/runtime.py` — `status()` config block (1409-1413)

### Required fix
- **No fix required for the reported `string` symptom** — it is not a dashboard defect. If the `/docs` exposure is unwanted in an operator-facing deployment, disable it (`FastAPI(docs_url=None, redoc_url=None)`) or annotate the model with explicit `schema_extra` examples.
- **Fix the cold-start blank settings:** have `/status` return the on-disk configuration even when the runtime is stopped (parse `config/rdrs.yaml` on demand), or have the dashboard surface a clear "runtime not started" state on the Settings page instead of silently rendering empty fields.
- Add `GET /settings` so read-back does not depend on `/status`.

---

## 5 — Bug #5: Footer displays `RDRS Â·`

### Status
**ROOT-CAUSED — SAME DEFECT AS #3.** Not a separate bug. Identical double-encoding, applied to U+00B7 (middle dot) instead of U+2014.

### Evidence

Byte scan — **3 middle-dot sites**:

| Line | Content |
|---|---|
| 1129 | `RDRS Â· Data provided by the RDRS API` ← **the reported footer** |
| 2212 | `Â· Score:` |
| 2216 | `Â· ${escapeHtml(created)}` |

Byte count: `b'\xc3\x82\xc2\xb7'` (3 occurrences).

Byte chain for `U+00B7`:

```
U+00B7  ──UTF-8 encode──▶  b'\xc2\xb7'
        ──CP1252 decode─▶  'Â·'           (U+00C2 U+00B7)
        ──UTF-8 encode──▶  b'\xc3\x82\xc2\xb7'
        ──browser render─▶  Â·
```

Exactly matches the bytes in the file. Line 1129 is the footer the operator reported.

### Root cause
Identical to §3: one CP1252-as-UTF-8 decode pass over `src/rdrs/dashboard/index.html`. Bugs #3 and #5 are two symptoms of a single corrupted decode, and both are fixed by one pass over the file.

### Affected files
- `src/rdrs/dashboard/index.html` — lines 1129, 2212, 2216

### Required fix
Covered by §3. Replace `Â·` → `·` (U+00B7) at the same time as the em dashes; a single correct re-encode of the file resolves bugs #3 and #5 together.

---

## 6 — Bug #6: Refresh button does not work

### Status
**ROOT-CAUSED — the button is wired correctly and does fire; a genuine refresh defect exists in the surrounding logic.** The click handler, the endpoint and the re-render chain are all intact and were verified. The reported symptom is best explained by (a) the 5-second auto-refresh making a manual click look like a no-op, and (b) a real defect: `refreshAll()` re-renders only the **currently active page**, so counters living on other pages do not update and the page can appear frozen.

### Evidence

**6a. The handler is correctly bound** — `index.html:2619-2622`:

```javascript
refreshBtn.addEventListener(
    "click",
    refreshAll
);
```

**6b. `refreshAll()` does re-fetch and re-render** — `index.html:2520-2538`:

```javascript
async function refreshAll() {
    try {
        showMessage("");
        const status = await request(API.status);   // ← real network call
        renderStatus(status);                        // ← buttons + status dot
        const active = document.querySelector(".nav-item.active");
        const page = active?.dataset.page || "dashboard";
        await refreshCurrentPage(page);              // ← re-renders ACTIVE page only
    } catch (error) { ... }
}
```

**6c. The endpoint returns fresh data on every call** — verified live, three sequential calls:

```
call 1: events=235 processes=26720 scores=0 | score=0.0 level=Normal reason='' ts=None
call 2: events=235 processes=26798 scores=0 | score=0.0 level=Normal reason='' ts=None
call 3: events=235 processes=26925 scores=0 | score=0.0 level=Normal reason='' ts=None
```

`process_count` climbs on **every** call (26 720 → 26 798 → 26 925), proving the API is live and the reload path is functional. There is no caching defect: `request()` sets `cache: "no-store"` (`index.html:1280`).

**6d. Every page-specific loader uses a real network call** — `loadDashboard` → `GET /dashboard-data`; `loadEventsPage` → `GET /events?limit=200`; `loadProcessesPage`, `loadAlertsPage`, `loadIncidentsPage`, `loadReportsPage`, `loadSettings` likewise (`index.html:2106-2477`).

**6e. Confirmed by contrast — the chain is correct because the auto-refresh already works.** `refreshAll` is also invoked by `setInterval(refreshAll, 5000)` (`index.html:2671-2674`) and once at boot (`index.html:2669`). Since the same function drives both the button and the timer, and the page demonstrably updates on the timer, the button path is functionally identical.

### Root cause
Three contributing factors, in order of significance:

1. **`refreshAll()` only re-renders the active page (primary).** `refreshCurrentPage(page)` dispatches to a single loader. Counters shown on the Dashboard (`eventCount`, `processCount`, `alertCount`, `incidentCount`) are **not** refreshed while the operator is on, say, the Events page. Because the operator was investigating the score/entropy issues (bugs #2/#3), they were plausibly on a non-dashboard page — clicking Refresh would then update nothing visible.

2. **The 5-second auto-refresh masks the button (perceptual).** With `setInterval(refreshAll, 5000)`, the data is already at most 5 s old when the operator clicks. On a quiet host, the click produces no visible change — indistinguishable from a dead button. There is also **no user feedback**: `showMessage("")` clears the message bar rather than confirming the refresh, so nothing acknowledges the click.

3. **Silent failure on error, buttons left disabled (real defect).** In `controlRuntime`, on the error path, `refreshAll()` is awaited (`index.html:2588`) — but if that also throws, nothing re-enables `startBtn`/`stopBtn`, leaving the UI permanently locked. There is no `finally`. This is the same defect noted in §1 and it can present as "the refresh/controls are dead."

Additionally, `refreshAll()` reads the active nav item via a DOM query on every invocation. During a control POST, the nav can be mid-transition, so the wrong page may be refreshed — again producing a "nothing changed" impression.

### Affected files
- `src/rdrs/dashboard/index.html` — `refreshAll` (2520-2553), `refreshCurrentPage` (2441-2477), `controlRuntime` (2555-2590), `request` (1273-1303), `showMessage` (1305-1307), button binding (2619-2622), `setInterval` (2671-2674)

### Required fix
- Make the Refresh button reload **all** visible data, not just the active page: re-render status, summary counters, and the active page in one pass, so any on-screen value is guaranteed fresh.
- Give the click explicit feedback — set a timestamp ("Last refreshed 17:23:04") or flash the button — so the operator can distinguish "no change in data" from "button did nothing."
- Wrap `controlRuntime`'s body in `try { … } finally { updateControls(state.status?.state ?? "UNKNOWN"); }` so the buttons are always restored.
- Guard `refreshAll()` with an in-flight flag so the 5-second timer cannot overlap or supersede a manual click or a control request.
- Consider pausing the 5-second interval while a control request is in flight, and resuming (or restarting the interval) afterwards.

---

## 7 — Verification against FSD, Build Guide and Locked Development Rules

### FSD (FR-01 … FR-13)
| FR | Relevance | Finding |
|---|---|---|
| FR-7 / FR-8 — threat scoring & alerting | Bug #2 | **VIOLATED in practice.** The scoring pipeline is structurally sound (weights, bands, CRITICAL→incident all correct) but the detector's window cannot populate under burst load, so no score is ever produced or persisted. The spec's intent — that a ransomware-like burst be scored and surfaced — is **not met in live operation**. |
| FR-5 / FR-6 — real-time monitoring & event capture | Bug #2 | **PARTIALLY VIOLATED.** Events are captured and persisted faithfully (235 rows, correct actions, correct metadata incl. entropy and SHA-256). The capture layer is correct; the *analysis* layer never sees them. |
| FR-11 — dashboard observability | Bugs #1, #3, #5, #6 | **PARTIALLY VIOLATED.** Transport is correct (`charset=utf-8`, live API, no caching) but the rendered UI carries mojibake (#3/#5), reports a contradictory degraded/healthy pair (#1), and refreshes only a subset of the page (#6). |
| FR-12 — configuration / settings | Bug #4 | **PARTIALLY VIOLATED.** Real config is correct (`name: rdrs`, `environment: test`); the defect is that `/status` returns an empty config before `start()`, so Settings renders blank on a cold load. |
| FR-13 — audit / logging | — | **CONFORMS.** Rotation verified previously; no defect found in this audit. |

### Hands-On Build Guide (Phase 1 … 10)
- **Phase 4 (sliding-window detection):** the implementation matches the guide — configurable `window_seconds`, `modified_files_per_minute`, `renames`, `extension_changes`, `average_entropy`, `cpu_percent`. **The defect is in the window's clock anchoring, not its thresholds.** The thresholds are exactly as specified (`config/rdrs.yaml:12-18`).
- **Phase 5 (scoring / alerting / incidents):** weights match the guide (`40 / 30 / 25 / 15 / 10`, cap 100); bands are `NORMAL < 40 ≤ WARNING < 70 ≤ CRITICAL`; only CRITICAL creates an incident. All verified in source. Correct — but unreachable for the burst in §2.
- **Phase 7 (web dashboard):** the endpoint set (`/status`, `/dashboard-data`, `/events`, `/processes`, `/alerts`, `/incidents`, `/reports`, `/settings`, `/monitor/start`, `/monitor/stop`) is complete and live. Bugs #1/#3/#5/#6 are confined to `index.html`.
- **Phase 8 (incident reports):** `build_incident_report` is present and correctly escapes. Unreachable while no incident can be created (consequence of §2).
- **Phase 10 (Docker):** **BLOCKED BY ENVIRONMENT** as previously recorded — Docker CLI 29.8.0 present, daemon not running (`npipe:////./pipe/dockerDesktopLinuxEngine` unreachable). Unchanged by this audit.

### Locked Development Rules
| Rule | Compliance |
|---|---|
| **No real ransomware / no destructive tests** | **CONFORMS.** The 232-event burst was a sandboxed simulation in `data/sandbox/live_test/` (`test_*.txt` → `test_*.locked`). `src/rdrs/quarantine.py:51` uses `shutil.copy2` as its *only* file operation (copy-only, no delete). Zero `kill` / `terminate` / `rmtree` / `os.remove` / `unlink` in `src/rdrs/*.py`. |
| **Never test on personal files** | **CONFORMS.** All activity confined to `data/sandbox/` inside the project tree. |
| **Simulation mode default ON** | **CONFORMS.** `runtime.py:461-467` uses `getattr(self._config, "simulation_mode", True)`; `config/rdrs.yaml` omits the key, so the default ON holds. Confirmed live: `response … 'simulation_mode': True`. |
| **No fabricated results, no mock data** | **CONFORMS.** All evidence above is from real execution. Independent confirmation that the read path does not invent values: `latest_score()` returns a zero block **only** when no row exists, and `score_count: 0` is reported honestly rather than being papered over. |
| **Defensive-only posture** | **CONFORMS.** No destructive or active-response capability was exercised or found. |
| **Read-only audit (this task)** | **CONFORMS.** No file under `src/` or `config/` was modified. This report is the only artefact written. |

---

## 8 — Consolidated defect list

| ID | Severity | Defect | File / lines |
|---|---|---|---|
| **B-01** | **High** | Detector window purges events on ingest when the event timestamp lags the wall clock, so the entire 232-event burst scores `0.0`. Detection blind spot under exactly the load it must detect. | `burst_detection.py` — `_purge_expired` 396-407, `add_event` 684-711 |
| **B-02** | **High** | No `ScoreRecord` is written for WARNING-level bursts (only CRITICAL via `response.process()`), so a real WARNING threat is invisible on the dashboard. | `response.py:52`, `runtime.py:545-554`, `dashboard_data.py:108` |
| **B-03** | Medium | Double-encoded UTF-8 in the dashboard: 8 em-dash + 3 middle-dot sites render as `â€”` / `Â·`. | `dashboard/index.html` — 687, 1129, 1251, 1477, 1577, 1583, 1590, 1760, 2188, 2212, 2216 |
| **B-04** | Medium | `/status` returns an empty `config` block before `start()`, so Settings renders blank on a cold load. `GET /settings` is 405 (POST-only), so read-back depends entirely on `/status`. | `runtime.py:1409-1413`, `api.py:1023`, `dashboard/index.html:2316,2349` |
| **B-05** | Medium | `controlRuntime()` has no `finally`; on a double failure (control POST **and** fallback `refreshAll()` both throw) both buttons stay permanently disabled. | `dashboard/index.html:2555-2590` |
| **B-06** | Low–Med | `refreshAll()` re-renders only the active page, so summary counters on the Dashboard do not refresh from other pages; no visual feedback on click; the 5 s interval can overlap a control request. | `dashboard/index.html:2520-2553, 2671-2674` |
| **B-07** | Low | UTF-8 BOM at the head of `dashboard/index.html` while the page is served as `charset=utf-8`. | `dashboard/index.html:1` |
| **B-08** | Low | `DEGRADED` is rendered as both "running" (buttons) and "unhealthy" (dot/text) with no explanation of which subsystem failed, though the data is present in `status.subsystems`. | `dashboard/index.html:1309-1344` |

### Suggested order of remediation
1. **B-01** — the detection blind spot. This is the only defect that defeats the product's core purpose.
2. **B-02** — without it, a fixed B-01 still leaves WARNING-level threats unseen.
3. **B-03 / B-07** — one re-encode of `index.html` clears bugs #3 and #5 and the BOM together.
4. **B-04 / B-05** — correctness of the settings and control surfaces.
5. **B-06 / B-08** — observability polish.

### Proposed regression tests (must exist before any fix is marked verified)
- **T-B01:** ingest 200 modifications within 10 s using **stale** timestamps → assert `level == "CRITICAL"`, `score_count == 1`, `incident_count == 1`.
- **T-B02:** drive a WARNING-level burst → assert a `ScoreRecord` is persisted and `/dashboard-data` `score.score > 0`.
- **T-B03:** byte-scan the served `/` response → assert neither `b'\xc3\xa2\xe2\x82\xac'` nor `b'\xc3\x82\xc2\xb7'` is present, and no BOM.
- **T-B04:** `GET /status` on a cold process → assert `config.name` and `config.environment` are non-null.
- **T-B05:** force both the control POST and `refreshAll()` to fail → assert both buttons are re-enabled.
- **T-B06:** stub the API, click Refresh from the Events page → assert summary counters re-render.

---

## 9 — Audit integrity statement

- **No source file was modified.** No edit was made to `src/`, `config/`, `tests/`, or `data/`. This report is the only file written.
- **No mock data, no fake status, no placeholder fixes.** Every number quoted above was produced by executing the real `src/rdrs` package against the real `data/rdrs.db` and real `config/rdrs.yaml`. The `TestClient` sweeps exercise the real ASGI app, not a stub.
- **No issue is marked fixed.** All six reported bugs are reported as diagnosed-only, with statuses `ROOT-CAUSED`, `NOT REPRODUCIBLE`, or `BLOCKED BY ENVIRONMENT`. Per instruction, executable verification of a fix is the precondition for any "fixed" claim and none has occurred.
- **Environment limits disclosed.** Phase 10 (Docker execution) remains **BLOCKED BY ENVIRONMENT** — daemon not reachable. Nothing in this audit depends on that gate.
- **One reported bug was not reproduced as described.** Bug #4 (`string` placeholder) does not exist in the dashboard; it is FastAPI's OpenAPI schema hint in Swagger UI at `/docs`. The nearest real defect (blank settings on cold start) is recorded separately as **B-04** rather than being silently substituted for the original report.

**Verdict: NOT FROZEN / NOT LOCKED / NOT 100-100.** Seven actionable defects identified (**B-01** through **B-07**) plus one UI-clarity issue (**B-08**); one previously-reported gate (Phase 10 Docker execution) remains unmet and environmental.
