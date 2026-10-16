# Evidence directory

Reports and log excerpts here were generated from the genuine RDRS safe-sandbox
run described in [the write-up](../WRITEUP.md). The portfolio README presents
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

The portfolio's quarantine and test views use authentic historical evidence
because those operational details are not safely available as public application
views. All supporting evidence remains preserved:

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
