# RDRS Safe Detection Demonstration

**Ransomware Detection and Response System (RDRS)**
**Demonstration date:** 2026-10-08 (UTC)
**Incident:** #1 — **CRITICAL, 85/100**
**Scope:** Safe simulation under `data/sandbox/submission_demo_20261008T150259Z_c795fc9c/`; no personal or production files.

## 1. Purpose and observed result

RDRS monitors file-system activity, scores correlated changes, records alerts/incidents, and preserves evidence copies. This demonstration exercised the running application end-to-end with synthetic text files. The monitor recorded 23 affected paths and the response layer created 10 verified evidence copies. The score fired: rapid encryption, mass rename, cpu spike. Incident timestamp: `2026-10-08T15:03:10.024522+00:00`.

## 2. Design choices

The application is split into configuration, monitoring, burst detection, scoring, response, persistence, reporting, and dashboard modules. Watch paths come from `config.yaml`; the live Docker service watches its mounted `data/` area. SQLite persistence retains events and incidents. Detection thresholds and score weights remain as configured; they were not weakened for the demo. The dashboard calls the same API used by the report generator. Simulation mode was confirmed enabled, and response uses copy-only quarantine: each preserved copy was SHA-256 matched to an original synthetic `.locked` sandbox file.

## 3. Verification and evidence

The safe scenario created 50 harmless text files in a unique sandbox directory, overwrote them with random bytes, and renamed them to `.locked` names. RDRS produced a real `CRITICAL` incident at 85/100. The genuine dashboard incident-card capture is `screenshots/02-incident-details.png`; the incident-scoped JSON/CSV report and matching event/alert/audit log excerpts are in `evidence/`. Paths and user-home identifiers were redacted in the report/log copies; score, signals, incident ID, timestamps, and other security facts are retained.

Verification commands/results: `python -m pytest tests/ -q` — **835 passed**; `node tests/dashboard_frontend_harness.mjs` — **124 passed**; `python tests/encoding_safety_scan.py` — **22 passed**; Docker configuration checks — **49 passed**. The Docker runtime, persistence/restart, and post-fix performance checks are documented in the repository README.

## 4. False-positive scenario

A bulk archive or backup job can rewrite and rename many files quickly. If the output is compressed/encrypted, its high entropy can combine with rename and rapid-change signals and produce a warning or critical score. A single archive file alone is insufficient; the correlated multi-file burst is the risk.

## 5. Limitations

Detection is heuristic and depends on the configured paths and filesystem notifications. The suspect process is a contemporaneous observation, not proven causal attribution. Simulation protects originals in this workflow, but RDRS does not authenticate API users; deployment remains loopback-focused. During this run, the Windows Docker bind mount remained healthy but did not deliver the new sandbox file events. The captured live demo therefore used an isolated local RDRS instance watching only the unique sandbox folder, while the Docker service and its process monitor remained running and untouched. Windows Docker bind-mount notification behaviour still needs verification on the target host/runtime. Process history grows over time and needs retention policy.

## 6. Future improvements

1. Add configurable path/process allow-lists for trusted backup, archive, and build workloads.
2. Add process-history retention and alert lifecycle controls.
3. Add API authentication before any network-facing deployment.
4. Expand cross-platform filesystem-notification tests and report operator workflow feedback.

## 7. Isolated dashboard verification (2026-10-09)

To check the dashboard without opening the malformed Docker database, a separate
temporary config and SQLite database were created under ignored `ptmp/` using
`RuntimeManager(config_path=...)`. The isolated runtime watched only its own
synthetic sandbox in simulation mode. `/health`, `/dashboard-data`, `/events`,
`/processes`, `/alerts`, and `/incidents` each returned HTTP 200. The dashboard
rendered its matching API summary: 202 events, 454 cumulative process snapshots,
2 alerts, and 1 Critical incident (85/100, 39 affected paths). The Overview
capture is `screenshots/01-dashboard.png`; it is not evidence of the current
Docker runtime's health. The live database and Docker container were not
repaired or restarted.

## 8. Current release distinction (2026-10-12)

Sections 1–7 describe preserved historical sandbox evidence, not a new run.
The hosted service now provides redacted read-only record views with the same
seven-page SOC layout, timelines, pagination and existing report downloads.
Administrative operations remain restricted to private loopback/SSH access.
See [current release verification](../docs/RELEASE_VERIFICATION.md). No original
screenshots, reports or log excerpts were regenerated for this release.

**Public reviewer/demo URL:** <https://rdrs.samyaktech.in/>.
This is the existing redacted read-only service; the 2026-10-15 domain audit
verified HTTPS, permitted reads, existing report downloads and restrictions.
Original screenshots and the historical safe-demo results above are unchanged.
