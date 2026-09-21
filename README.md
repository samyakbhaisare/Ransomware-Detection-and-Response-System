# RDRS — Ransomware Detection & Response System

A defensive ransomware detection and response toolkit implementing the full
FSD requirement set (FR-01 … FR-13) and the Hands-On Build Guide phases
(Phase 1 … Phase 10).

> **Defensive tooling only.** RDRS detects ransomware-like behaviour and copies
> evidence for analysis. It never encrypts, never destroys data, and never
> kills real processes. Quarantine is copy-only and simulation mode is on by
> default.

## Implemented scope

| Area | Requirement | Status |
| --- | --- | --- |
| Config loading & validation | FR-01 | Implemented |
| Real-time file monitoring (create/modify/rename/delete) | FR-02 | Implemented |
| Shannon entropy analysis | FR-03 | Implemented |
| Process telemetry (`psutil`) | FR-04 | Implemented |
| SQLite persistence | FR-05 | Implemented |
| Sliding-window burst detection | FR-06 | Implemented |
| Weighted 0–100 threat scoring | FR-07 | Implemented |
| Alerts & incident records | FR-08 | Implemented |
| Evidence quarantine (copy-only) | FR-09 | Implemented |
| REST API | FR-10 | Implemented |
| Live dashboard | FR-11 | Implemented |
| JSON / CSV reports | FR-12 | Implemented |
| Automated tests + Docker | FR-13 | Implemented |

Threat bands: **Normal** 0–40, **Warning** 40–70, **Critical** 70–100.

Scoring weights (Build Guide Phase 5): rapid encryption 40, mass rename 30,
high entropy 25, CPU spike 15, unknown program 10.

## Requirements

- Python 3.13+
- Dependencies: `pip install -r requirements.txt`

## Quick start

```bash
pip install -r requirements.txt

# Start the API + dashboard on http://127.0.0.1:8000
uvicorn rdrs.api:app --host 0.0.0.0 --port 8000
```

Open `http://127.0.0.1:8000/` for the dashboard.

## CLI

```bash
rdrs status
rdrs start
rdrs stop
```

## REST API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/` | Live dashboard (HTML) |
| GET | `/health` | Liveness + subsystem health |
| GET | `/status` | Runtime status snapshot |
| POST | `/monitor/start` | Start filesystem monitoring |
| POST | `/monitor/stop` | Stop filesystem monitoring |
| GET | `/events` | Persisted file events |
| GET | `/processes` | Process telemetry snapshots |
| GET | `/alerts` | Alerts raised |
| GET | `/incidents` | Incidents raised |
| GET | `/dashboard-data` | Aggregated dashboard payload |
| GET | `/scan` | Trigger a scan |
| POST | `/scan` | Trigger a scan (with parameters) |
| GET | `/reports` | List generated reports |
| POST | `/reports/generate` | Generate a JSON/CSV incident report |
| GET | `/reports/{incident_id}/{format}` | Download a report (`json` or `csv`) |
| POST | `/settings` | Update runtime settings (persisted to the config file) |

## Operational logs

Five size-rotated logs are written under the configured `logs_dir`
(Build Guide Phase 1):

| Log | Contents |
| --- | --- |
| `system.log` | Runtime lifecycle transitions |
| `events.log` | Every observed file event |
| `alerts.log` | Threat detections and alerts |
| `errors.log` | Pipeline and runtime failures |
| `audit.log` | Critical-incident audit trail |

## Docker

```bash
docker compose up --build
```

The image exposes port `8000`, declares a `/health` healthcheck, and mounts
`./data`, `./logs` and `./config` from the host so no runtime state is baked
into the image.

## Testing

```bash
python -m pytest tests/ -q
```

The suite covers unit tests and integration tests (config, entropy, detection,
scoring, persistence, response, API, dashboard data, reporting, logging and
runtime lifecycle).

### Front-end verification

The dashboard's own JavaScript is executed, not just inspected. `pytest`
invokes a Node harness that loads the real `<script>` block out of
`src/rdrs/dashboard/index.html` into a `vm` context with a DOM stub and drives
the real functions (refresh, control buttons, settings rendering):

```bash
node ptmp/dashboard_frontend_harness.mjs
```

**Prerequisite:** Node.js (the harness is run from `pytest`, so a missing
Node fails the suite rather than silently skipping front-end verification).

### Environment note — editable install path

`import rdrs` resolves through
`.venv/Lib/site-packages/__editable__.rdrs-0.1.0.pth`. That file must point at
**this** checkout's `src` directory. If it points at another copy of the
project, `pytest` still tests this tree (via `pythonpath = ["src"]`) while
`uvicorn rdrs.api:app` and ad-hoc scripts silently run the other copy — so a
fix can appear to have no effect. Verify with:

```bash
python -c "import rdrs; print(rdrs.__file__)"
```

> **Windows note:** the WorkBuddy sandbox ships a `safe-delete` shim that
> intercepts file deletion. It can cause spurious errors when tests clean up
> `data/rdrs.db`. Running the suite with a clean interpreter avoids this; the
> same tests pass without the shim.

## Safety

- Detection and response only — no offensive capability.
- Quarantine copies evidence; originals are never modified or removed.
- Simulation mode is enabled by default and must be explicitly disabled.
- Never point RDRS at personal or production data during testing; use the
  dedicated sandbox directory from the Build Guide (section 15).


## Architecture

RDRS is a single Python package with one responsibility per module. The runtime
owns the lifecycle; the API and dashboard are thin layers over it.

```
                    ┌──────────────────────────────┐
                    │   cli.py / api.py (FastAPI)  │   entry points
                    └───────────────┬──────────────┘
                                    │
                    ┌───────────────▼──────────────┐
                    │        runtime.py            │   lifecycle, wiring,
                    │  RuntimeManager / Status     │   persistence mode
                    └──┬────────┬────────┬─────────┘
                       │        │        │
        ┌──────────────▼─┐ ┌────▼─────┐ ┌▼──────────────┐
        │ monitoring.py  │ │telemetry │ │  storage.py   │
        │ watchdog file  │ │  .py     │ │ SQLAlchemy +  │
        │ events (FR-2)  │ │ psutil   │ │ SQLite (FR-5) │
        └───────┬────────┘ │ (FR-4)   │ └───────┬───────┘
                │          └────┬─────┘         │
                │               │               │
        ┌───────▼───────────────▼───────────────▼───────┐
        │            burst_detection.py (FR-6)          │
        │   sliding time window -> active signals       │
        └───────────────────┬───────────────────────────┘
                            │
                 ┌──────────▼──────────┐
                 │    scoring.py       │   weighted 0-100 (FR-7)
                 └──────────┬──────────┘
                            │
                 ┌──────────▼──────────┐
                 │    response.py      │   alerts + incidents (FR-8)
                 │    quarantine.py    │   copy-only evidence (FR-9)
                 └──────────┬──────────┘
                            │
                 ┌──────────▼──────────┐
                 │ dashboard_data.py   │   aggregates one payload
                 │ dashboard/index.html│   live UI (FR-11)
                 └─────────────────────┘
```

Supporting modules: `config.py` / `config_engine.py` (FR-1 loading, validation
and comment-preserving save), `entropy.py` (FR-3 Shannon entropy over a 64 KB
sample), `logging.py` / `audit.py` (rotating logs), `events.py` (in-process
event bus), `health.py` (subsystem health), `policy.py` and `workers.py`
(thread-pool helpers), `fileaccess.py` (shared-mode reads so a read never blocks
a rename or delete).

**Data flow:** file events and process snapshots are persisted, then evaluated in
a sliding window. When enough signals fire, a weighted score is computed; a
CRITICAL score creates an alert and an incident, and evidence is **copied** to
quarantine. The dashboard reads one aggregated payload rather than querying the
database itself.

## Detection signals and false positives

The detector fires on five signals, each weighted (see `config.yaml`):

| Signal | Fires when | Weight |
| --- | --- | --- |
| `rapid_encryption` | many distinct files modified inside the window | 40 |
| `mass_rename` | many renames inside the window | 30 |
| `high_entropy` | average entropy of modified files is high | 25 |
| `cpu_spike` | the suspect process is CPU-busy | 15 |
| `unknown_program` | the suspect process is not a known system binary | 10 |

Bands: **NORMAL** `< 40`, **WARNING** `40–69`, **CRITICAL** `>= 70`. Only
CRITICAL creates an incident.

**False positives are the main practical risk for a detector like this**, and the
design accepts that trade-off deliberately. The signals that most often produce
them:

* **`high_entropy`** — compressed archives, encrypted backups, disk images, and
  large media files are *legitimately* high-entropy. A single `.zip` or `.mp4`
  being written can look like encryption. This is mitigated by requiring a
  *burst* (many files in the window) rather than one file, and by the
  `average_entropy` threshold acting alongside the file-count signals rather
  than alone.
* **`mass_rename`** — bulk organisation scripts, archive extraction, and build
  tools (which create and rename hundreds of files quickly) all rename en masse.
  A `git checkout`, `npm install`, or unzip of a large archive can resemble this.
* **`rapid_encryption`** — any tool that rewrites many files in place, such as a
  backup restore, a bulk metadata edit, or a formatter run across a repository.
* **`cpu_spike` / `unknown_program`** — a legitimate compile or video encode
  raises CPU, and any non-system executable (including a user's own script)
  counts as unknown. These carry the lowest weights for that reason.

**Mitigations in the current implementation:** thresholds are tuned so that
normal activity stays below the WARNING band while a genuine burst reaches
CRITICAL; scoring is weighted rather than binary; and `unknown_program` /
`cpu_spike` alone can never reach CRITICAL. **Not yet implemented:** an
allow-list of known-good directories, processes or extensions, and a per-path
exclusion for backup/archive locations. These are the first things to add in a
real deployment — see *Future improvements*.

## Persistence modes

`persistence.enabled` in `config.yaml`, also editable on the Settings page:

| Mode | Behaviour |
| --- | --- |
| **Enabled** | A file-backed SQLite database at `persistence.db_path`. Events, processes, alerts, incidents and scores survive a **process restart**. |
| **Disabled** | An **in-memory** database (`db_path` is `None`, no file is created). Everything works while the process lives, including stop/start of monitoring, but history is **lost when the process exits**. |

Switching the setting replaces the database manager for the running process. It
**does not delete anything**: the file-backed database and every row in it are
left untouched, so re-enabling persistence brings the previous history back.
Turning persistence *off* makes that history **invisible, not deleted** — which
is the intended behaviour, but is worth knowing before it looks like data loss.

> Both modes keep data across a **stop/start of monitoring** within the same
> process. The difference only appears when the process itself restarts.

## Known limitations

* **Docker deployment is not verified.** `Dockerfile` and `docker-compose.yml`
  are present and well-formed, but the image has never been built or run in this
  environment, so Phase 10 and the deployment half of FR-13 remain unverified.
* **Filesystem monitoring inside a container is untested.** watchdog relies on
  native filesystem notifications; behaviour on a bind-mounted volume (Docker
  Desktop on Windows in particular) may differ from the host.
* **The Dockerfile pins `python:3.13-slim`** while development runs on 3.14.3 —
  an untested combination.
* **No authentication.** The API trusts the local machine and guards mutating
  endpoints with a local-origin check. It is not intended to be exposed to a
  network.
* **Single-process only.** SQLite plus in-process runtime state means the app is
  not safe to run as multiple replicas.
* **No allow-list / exclusion tuning** for false positives (see above).
* **Cloud hosting is not a good fit as-is** — the tool watches the local
  filesystem, so a hosted container would only watch its own volume.
* A long-running instance accumulates process snapshots quickly; the bundled
  database can grow large during extended testing.

## Verification status

Verified with fresh evidence unless marked otherwise.

| Area | Status |
| --- | --- |
| Backend test suite (`pytest tests`) | **739 passed, 0 failed, 0 skipped** |
| Front-end harness (`ptmp/dashboard_frontend_harness.mjs`) | **99 passed, 0 failed** |
| Encoding + safety scan (`ptmp/encoding_safety_scan.py`) | **22 passed, 0 failed** |
| Live browser verification (real API, no injected data) | **19 passed, 0 failed** |
| Entropy (FR-3) | normal text **4.397**, random bytes **7.979** |
| Quarantine (FR-9) | copy-only confirmed — `copy2`; no `unlink`, no `shutil.move` |
| Persistence across a process restart | events/alerts/incidents/scores all preserved |
| Monitoring lifecycle (start/stop/start, polling while stopped) | no 500s; 24 concurrent reads all 200 |
| Report generation (JSON + CSV) | repeated generation and downloads verified |
| **Docker build/run (Phase 10, FR-13)** | **NOT VERIFIED — Docker daemon unavailable** |

## Design, testing and future improvements

**Design choices.** The system is deliberately modular with one responsibility
per file, so each stage (monitor → detect → score → respond → report) can be
tested in isolation. Persistence is SQLite via SQLAlchemy because it needs no
server and the dataset is single-machine. The runtime owns the database
lifecycle: read endpoints never close it, because doing so raced concurrent
reads and produced 500s while monitoring was stopped. The dashboard renders one
aggregated payload per poll instead of issuing many small requests. All
timestamps ordered by the list endpoints are indexed, which took the dashboard
payload from ~8.5 s to ~0.08 s on a database holding ~980k process rows.

**How it was tested.** Three layers: a 739-test backend suite covering config,
storage, detection, scoring, the API and the runtime lifecycle; a front-end
harness that executes the real dashboard JavaScript in a JS VM with a DOM stub;
and a live pass that drives a real browser against the running server using the
real API responses — no injected payloads. Detection was exercised with the
Build Guide's section 15 sandbox script: throwaway files overwritten with
`os.urandom` and renamed to `.locked`, which reaches CRITICAL and creates an
incident with copied evidence while the originals stay untouched.

**One false positive considered.** A user archiving a project directory would
create many new files and rename many others in a short window, and a `.zip` or
`.7z` output is high-entropy — three signals at once. The current thresholds
would likely raise a WARNING and could reach CRITICAL for a large archive. The
burst requirement (many *distinct* files) is what keeps a single large file from
triggering, but a bulk archive job is a genuine false-positive scenario, and the
right fix is a path exclusion for backup/archive locations.

**What I would improve next, in order:**
1. A **path and process allow-list** so backups, archives, build directories and
   known tools can be excluded — the single highest-value change.
2. **Verify Docker** and confirm watchdog works on a bind-mounted volume.
3. **Authenticate the API** and make the local-origin check configurable, so it
   could run on a host rather than only on a workstation.
4. **Retention for process snapshots** — the table grows without bound; a
   time-based prune would keep the database small.
5. **Ship the architecture diagram and screenshots** with the repository, and
   record a short demonstration of the sandbox attack.
