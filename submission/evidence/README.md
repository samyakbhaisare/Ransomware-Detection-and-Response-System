# Evidence directory

## Current README application GUI captures — 2026-10-16 UTC

These four images were captured directly from https://rdrs.samyaktech.in/ in
real Chrome. No screenshot was recaptured from an image file or verification page:

1. [SOC Dashboard](../screenshots/gui-01-live-soc-dashboard.png).
2. [Incident Investigation](../screenshots/gui-02-live-incident-investigation.png).
3. [Response / Evidence](../screenshots/gui-03-live-response-evidence.png) — genuine response status, not quarantine-copy verification.
4. [Reports](../screenshots/gui-04-live-reports.png).

The application has no dedicated Testing/System Status GUI. Genuine test results
remain in the README's technical verification section and existing records.
Production state, private backup and historical evidence were not changed.

## Preserved earlier evidence

The earlier `refresh-*` production captures and command-output verification
images remain preserved. The quarantine and test output images are supporting
verification evidence, not application GUI screenshots, and are not displayed
in the current README showcase.

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
