# RDRS release verification — 2026-10-12

## Scope and provenance

This release packages the current FR-01–FR-13 implementation and approved
seven-page SOC dashboard. It preserves the root configuration, original five
submission screenshots, sample reports/logs, local historical audits and Git
history. It does not package live databases or raw quarantine/runtime evidence.
The six deletion-only commits through `977956a` are retained in history.

## Completed deployment checks (2026-10-12)

These were real prior checks of the unchanged deployed implementation, not tests
rerun against a live database during packaging:

- Chrome 155 original local frontend versus hosted read-only frontend: all seven
  pages, 1,000-row event/process pages, Older/Newer paging and refresh passed.
- All four incident timeline timestamp/action sequences, scores, signals and
  affected-file counts matched the original private view.
- Existing JSON/CSV report responses for four incidents returned 200. Actual
  public report buttons downloaded redacted files without report-generation POSTs.
- All 1,577 events, sampled current/September process pages and eight reports
  passed redaction checks; client headers could not disable redaction.
- Administrative operations and restricted routes returned 403. Port 8000 was
  externally inaccessible; HTTPS and HTTP 308 redirect passed.
- SQLite integrity `ok`, no foreign-key violations, unchanged historical
  row/evidence/report fingerprints, seven healthy subsystems and continuing
  process captures (2,475,672 to 2,475,676 in one observation window).

## Release packaging checks

- Scoped backend release regression: **246 passed**, 8 dependency deprecation
  warnings, 94.60 seconds. Command:
  `python -m pytest tests/unit/test_public_redaction.py tests/unit/test_api_endpoints_task7.py tests/unit/test_runtime.py tests/unit/test_monitor_database_integration.py tests/unit/test_process_monitor.py tests/unit/test_docker_configuration.py tests/unit/test_config_watch_paths.py tests/unit/test_storage_task7.py tests/unit/test_reports_phase8.py -q`.
- Original frontend harness: **124 passed, 0 failed**.
- Isolated wheel build succeeded with the declared build dependencies. The wheel
  contains `rdrs/public_redaction.py` and `rdrs/dashboard/index.html`; ZIP CRC passed.
  An initial no-build-isolation attempt could not import setuptools in the local
  environment; normal isolated build resolved this without changing runtime dependencies.
- The 104-entry submission ZIP passed CRC, required-entry/security checks and
  byte-for-byte comparison with the final source manifest before atomic replacement.
  The previous ZIP was retained separately; original evidence hashes matched.
- Docker Compose local and VPS override configuration: validation succeeded
  with `docker compose config --quiet` and the two-file override command.
- Source AST syntax, controlled manifest, documentation references, preserved
  evidence hashes and archive verification are checked by the packaging process.
  No live database or VPS changes are part of these checks.

## Limitations and historical distinctions

- Full-suite 835-test/94%-coverage results in the README are dated October 8;
  they are not a new full-suite or coverage measurement of the release snapshot.
- Original screenshots depict October 8/9 safe sandbox runs, not current counters.
- Local Docker database corruption observed October 9 was not repaired. The VPS
  data store is separate; this release never reads/writes the local live database.
- Windows Docker bind-mount file notifications remain unverified for the target
  host; monitoring covers configured folders/container processes only.
- Public allowlist redaction withholds some metadata, free-text descriptions and
  recommendations; pseudonyms are per-server-process and change after restart.
- No login/account system or proven causal process/file attribution is claimed.
- A 20 KB database remains in historical commit `370045d`; its private contents
  were not inspected. No history rewrite or force-push is part of this release.
- Dependencies are not fully pinned; future rebuilds can resolve newer versions.

## Evidence locations

[Original write-up](../submission/WRITEUP.md),
[evidence index](../submission/evidence/README.md),
[Overview screenshot](../submission/screenshots/01-dashboard.png),
[incident screenshot](../submission/screenshots/02-incident-details.png),
[quarantine verification](../submission/screenshots/03-response-quarantine.png),
[report screenshot](../submission/screenshots/04-generated-report.png),
[static test screenshot](../submission/screenshots/05-test-evidence.png).
