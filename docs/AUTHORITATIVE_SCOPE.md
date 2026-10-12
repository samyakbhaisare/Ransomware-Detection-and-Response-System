# Authoritative RDRS submission scope

This release follows the supplied **RDRS Functional Specification Document (FSD),
version 1.0**, **Complete Hands-On Build Guide**, and **Locked Development &
Verification Rules**. The rejected 125-feature specifications are preserved
locally as historical archives and are not release requirements.

## FSD requirements

| Requirement | Scope |
| --- | --- |
| FR-01 | Load configuration from `config.yaml` |
| FR-02 | Monitor real-time create/modify/delete/rename file events |
| FR-03 | Calculate Shannon entropy of modified files |
| FR-04 | Collect process metadata with psutil |
| FR-05 | Persist events, processes, alerts and incidents in SQLite |
| FR-06 | Analyze events in a sliding time window |
| FR-07 | Compute a weighted threat score from 0 to 100 |
| FR-08 | Generate alerts and incident records |
| FR-09 | Copy quarantine evidence in simulation mode |
| FR-10 | Provide REST API endpoints |
| FR-11 | Display a live dashboard |
| FR-12 | Generate JSON/CSV reports |
| FR-13 | Support automated tests and Docker deployment |

The non-functional requirements are real-time monitoring, modular Python,
simulation by default, comprehensive logging and maintainable documentation.
Testing is restricted to a dedicated sandbox: no real malware or destructive
actions against personal or shared-machine files.

## Build Guide phases and deliverables

Phases 1–10 cover setup/entropy, file monitoring, database/process monitoring,
detection, scoring/response, API, dashboard, reporting, tests and Docker.
Required delivery includes code/setup instructions, an architecture explanation,
real safe-simulation screenshots, at least one generated report and detection
log sample, and a short design/testing/false-positive/improvement write-up.
JSON and CSV are mandatory; PDF/HTML reports and machine learning are not.
The guide targets Python 3.12 or newer and approximately 90% test coverage.

## Locked verification rules

- Only the FSD and Build Guide define feature scope.
- Preserve working logic; no fake data, fake security, fabricated results or dead controls.
- Claims of completeness require real tests/integration/output verification.
- Copy-only evidence and original-file preservation are mandatory.
- Changes to verified files require an evidenced defect, not optional redesign.
- Public reviewer access is a redacted read-only projection; administrative
  actions remain private and the original seven-page SOC design is retained.

See [release verification](RELEASE_VERIFICATION.md) for actual results and
[submission write-up](../submission/WRITEUP.md) for the original safe demo.
