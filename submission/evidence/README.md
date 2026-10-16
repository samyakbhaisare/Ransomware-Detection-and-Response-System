# Evidence directory

## Current README captures — 2026-10-16 UTC

All five current README images were newly captured in real Chrome during this
update; no earlier screenshot was reused as a current view:

1. [SOC Dashboard](../screenshots/refresh-01-soc-dashboard.png) — production read-only status, charts and recorded October 10 score.
2. [Incident Investigation](../screenshots/refresh-02-incident-investigation.png) — genuine stored incident #1 and redacted report timeline.
3. [Quarantine Verification](../screenshots/refresh-03-quarantine-verification.png) — fresh execution of the existing read-only verifier, 10/10 saved copies matched to originals.
4. [Reports](../screenshots/refresh-04-reports.png) — production existing report controls; no report generation.
5. [Test Verification](../screenshots/refresh-05-test-verification.png) — fresh actual isolated pytest output, 51 Docker-configuration/redaction checks passed.

Views 3 and 5 are browser evidence views of real command output, not new
application interfaces or fabricated runtime data. Quarantine files are from
the historical safe run; only their verification and screenshot are fresh.
Production monitoring, private backup and original evidence were not modified.

## Preserved earlier evidence

Reports and log excerpts here were generated from the genuine RDRS safe-sandbox
run described in [the write-up](../WRITEUP.md). Earlier captures include the
[fresh production SOC Overview](../screenshots/current-soc-dashboard.png) and
[fresh recorded incident investigation](../screenshots/current-incident-investigation.png),
captured on 2026-10-15 UTC from https://rdrs.samyaktech.in/ in real Chrome.
They show current monitoring status and historical October 10 incident data;
no new attack, response action or evidence generation occurred.
The [original SOC Overview](../screenshots/01-dashboard.png) and
[original incident capture](../screenshots/02-incident-details.png) remain unchanged.
JSON/CSV and JSONL excerpts preserve recorded security facts while redacting
machine-specific root/home paths.

The [current Reports interface](../screenshots/current-reports.png) was captured
in real Chrome on 2026-10-16 UTC. It lists four genuine stored incidents and
existing JSON/CSV download controls; no production report was generated.

Earlier quarantine/test screenshots remain authentic historical evidence.
They are preserved separately from the newly captured README views:

- [Quarantine-copy integrity verification](../screenshots/03-response-quarantine.png).
- [Historical generated-report evidence](../screenshots/04-generated-report.png).
- [Historical static-test evidence](../screenshots/05-test-evidence.png).

The byte-identical duplicate `screenshots/rdrs_incident_1_dashboard.png` was
removed from release tracking; its canonical original is `02-incident-details.png`.
The duplicate file remains preserved locally. Original images were not modified.
Public quarantine-copy outcomes are not exposed by the incident API, so the
existing verified quarantine evidence above is retained. Reports/log samples
remain from the separately documented historical safe run. Private recovery
backup material is not part of this submission evidence.
