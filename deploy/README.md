# VPS deployment

Verified public read-only dashboard: **https://192.46.208.222/**

HTTP redirects to HTTPS. A trusted Let's Encrypt short-lived IP certificate
is installed; `rdrs-tls-renew.timer` checks renewal twice daily and reloads
Nginx. Certificate/account keys are stored only on the VPS under `deploy/tls/`.

Deployed on 2026-10-10 to Ubuntu 24.04 using Docker 29.1.3 and
Compose 2.40.3. The VPS runs independently of the development laptop.

## Start command (on the VPS)

```bash
docker compose --project-directory /opt/rdrs \
  -f /opt/rdrs/docker-compose.yml \
  -f /opt/rdrs/deploy/compose.vps.yml up --build -d
```

The application is bound to `127.0.0.1:8000`. Nginx exposes port 80 and
allows GET/HEAD only on explicit dashboard/read/report-download routes.
All public POST requests, scan/settings routes, and API documentation routes
are denied. Backend administrative operations require localhost or an SSH tunnel.
No login page or account system has been added. Existing administrative buttons
remain in the GUI but their public requests are denied by the gateway.

Fresh `data/` and `logs/` bind mounts were created on the VPS, owned by UID/GID
1001. No local SQLite database, screenshots, submission archive, or secret was
transferred. The package contained 33 explicitly selected runtime/config files.

## Executed verification

| Check | Observed result |
| --- | --- |
| Docker build and Compose startup | Passed; backend healthy, gateway running |
| Public GUI, health, dashboard-data, events, processes, alerts, incidents | HTTP 200 |
| Public JSON/CSV report downloads for incident 1 | HTTP 200; 106,273 / 79,505 bytes |
| Public GET scan/settings/docs | HTTP 403 |
| Public POST monitor/stop, settings, scan, reports/generate | HTTP 403 |
| External backend port 8000 | Unreachable |
| Genuine monitored sandbox burst | Critical 70/100; incident 1; 50 synthetic originals preserved |
| Controlled backend stop/start | Healthy afterward; 185 events, 2 scores, 2 alerts, 1 incident preserved; report still downloadable |
| Initial resource snapshot | Backend 60.57 MiB / 768 MiB, CPU 0.10%; gateway 2.648 MiB / 128 MiB, CPU 0.00% |
| Real public Chrome rendering | 185 events, 2 alerts, 1 incident displayed |

The public deployment screenshot is retained at `evidence/public-dashboard.png`
as deployment verification evidence, separate from original development images.

The original deployment exposed synthetic demonstration history. Historical
records were later imported; the 2026-10-12 release exposes redacted record-level
reviewer views, not raw private evidence. Docker monitors only configured mounted
paths and its own process namespace, not the whole VPS or a visitor's computer.
Process history/quarantine retention and unattended long-term availability remain
operational limitations. Controlled restart was tested; host reboot and abrupt
power-loss recovery were not tested. Dependencies use the existing unpinned
manifest; future rebuilds can resolve different versions.

The malformed local Docker database was not repaired or transferred.

## Clipboard verification (2026-10-11)

Chrome confirmed HTTP was not a secure context and `navigator.clipboard` was
unavailable. HTTPS provides a secure context and the Clipboard API. The shared
handler now reports explicit HTTPS/browser/permission errors rather than a
generic failure. It uses no legacy clipboard bypass or fake success.

Every loaded Copy button was activated and its clipboard contents pasted using
Ctrl+V into a temporary browser test field, then compared exactly with the
button's full path. Clipboard permission was granted through Chrome's normal
automation permission control; no certificate or secure-context bypass was used.

| Section | Verified buttons | Result |
| --- | ---: | --- |
| Overview events | 8 | PASS |
| Events | 185 | PASS |
| Processes (loaded page) | 1,000 | PASS |
| Incident process observation | 1 | PASS |
| Incident timeline | 145 | PASS |
| Incident affected files | 60 | PASS |

The combined incident-detail pass also verified all 206 buttons. Counts are
the real loaded collections during verification, not a claim that all historical
process pages were traversed. Results: [copy-results.json](evidence/copy-results.json).
All tested public mutations and restricted GET routes still returned HTTP 403;
public reads returned HTTP 200. The dashboard harness passed 124 checks.

## Current reviewer access (2026-10-12)

All seven SOC pages retain their approved layout. Explicit GET reads for status,
dashboard data, events, processes, alerts, incidents and reports are proxied with
a forced `X-RDRS-Public: 1` header. Backend allowlist redaction keeps IDs,
timestamps, scores, signals, numeric telemetry and incident relationships,
pseudonymizes paths/names, and removes commands/private evidence fields.
Pseudonyms last for one server process. Private details require the loopback
backend and authenticated SSH tunnel; no login page was added.

Chrome checks passed all pages, Older/Newer pagination, four incident timelines
and actual JSON/CSV downloads. Public buttons download existing reports, never
POST generation. All 1,577 event rows, sampled current/September process pages
and eight report responses passed redaction checks. Administrative operations
remained 403; external backend port 8000 remained inaccessible. SQLite integrity
was `ok`, pre-existing row/report/evidence fingerprints matched and all seven
health flags were true. The initial counts/screenshots above remain historical.
