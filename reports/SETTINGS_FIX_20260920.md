# RDRS — SETTINGS PERSISTENCE FIX + FINAL VERIFICATION

**Date:** 20 September 2026 · **Scope:** the Settings persistence defect and non-destructive verification
**Config at verification time:** `shutdown_timeout: 20.0` · `sha256 886eebd4663beec6…`

---

## 1. FILES CHANGED

| File | Change |
|---|---|
| `src/rdrs/dashboard/index.html` | The fix (settings form state protection) |
| `ptmp/dashboard_frontend_harness.mjs` | +9 settings-persistence tests, `FormData`/`URLSearchParams` stubs, `fire`/`typeInto` helpers |
| `ptmp/verify_settings_browser.py` | **new** — CDP browser driver |
| `ptmp/add_harness_b11.py` | **new** — harness patch script |
| `.venv` | installed `websockets` (CDP client for browser verification) |

**No backend source file was changed for this defect.** `config.yaml` was written only by the settings saves that the test scenario itself required.

---

## 2. ROOT CAUSE

The backend was **never** at fault. Verified directly:

```
POST /settings {"shutdown_timeout": 20}  ->  200
config.yaml on disk                      ->  shutdown_timeout: 20.0
                                            watch_paths: ['data']  (preserved)
                                            comments: intact
```

The defect was entirely in the dashboard:

```js
// refreshAll(), running every 5 seconds
} else if (page === "settings") {
    await loadSettings(status, false);      // <- repopulated the whole form
}
```

`populateSettings` assigned **all five fields unconditionally**, so:

1. The operator types `20` into Shutdown Timeout.
2. Within 5 seconds the poll fires, fetches `/status` (still `15`) and overwrites the field.
3. The edit is gone before it can be saved.

That is exactly the reported symptom, and why `RDRS / test / 15` kept reappearing. A form is not live data, and background polling must never own a form the operator is editing.

---

## 3. FIX IMPLEMENTED

All in `src/rdrs/dashboard/index.html`:

| Change | Purpose |
|---|---|
| Background polling no longer repopulates the form; it calls `markSettingsPolled()` | Removes the whole failure mode. `markSettingsPolled` changes **no** field value — only the status line. |
| `dirtySettings` set + `wireSettingsFieldTracking()` (input/change listeners), wired **before** the first load | An edited field is remembered as unsaved |
| `populateSettings(config, available, force)` skips dirty fields | Polling, navigation and refresh cannot clobber an unsaved edit |
| `loadSettings(status, announce, force)` | The explicit "Load Current Settings" button passes `force=true` (deliberate discard); navigation passes `force=false` (preserves edits) |
| `saveSettings` uses the response as authoritative, clears the dirty flags, repopulates from it, reports success | A stale response can no longer revert what was just saved |
| Status line reports unsaved changes | The operator is told their edit is being protected |

---

## 4. BROWSER VERIFICATION — 10 passed, 0 failed

Real Chrome, driven over the DevTools Protocol (`ptmp/verify_settings_browser.py`).

| Check | Result |
|---|---|
| Settings opens showing the backend value (15) | **PASS** |
| Operator types 20 | **PASS** |
| **Edit survives 12 s of polling (2+ cycles)** ← the reported defect | **PASS** |
| Page reports the edit as unsaved | **PASS** |
| Save reports success | **PASS** |
| Field holds the saved value | **PASS** |
| Survives further polling | **PASS** |
| Survives a full page reload | **PASS** |
| Survives navigation away and back | **PASS** |
| Desktop + mobile screenshots captured | **PASS** |

Screenshots: `ptmp/ui_shots/settings_unsaved_desktop.png` · `settings_desktop.png` · `settings_mobile.png`

Post-run disk state: `shutdown_timeout: 20.0`, `watch_paths: ['data']`, comments intact, `.bak-*` backups written.

---

## 5. TESTS EXECUTED — EXACT COUNTS

| Suite | Command | Result |
|---|---|---|
| **Front-end harness** | `node ptmp/dashboard_frontend_harness.mjs` | **72 passed, 0 failed** |
| **Encoding + safety scan** | `python ptmp/encoding_safety_scan.py` | **ALL 22 CHECKS PASSED** |
| **Backend pytest suite** | `pytest tests -q -W error::pytest.PytestUnhandledThreadExceptionWarning` | **NOT RUN — sandbox denied** (see §7) |

The harness grew from 63 to **72** with the nine new settings tests, including:

* `a background poll does not revert the edit`
* `a stale load does not clobber an unsaved edit`
* `the unsaved-edit flag is cleared after a successful save`
* `polling after a save leaves the saved value in place`

### Test-count reconciliation

| Suite | Count |
|---|---|
| Backend pytest | **739 passed** — last confirmed *before* this dashboard change; **not re-run this cycle** |
| Front-end harness | **72 passed, 0 failed** — confirmed *after* the change |
| Encoding + safety | **22 passed, 0 failed** — confirmed *after* the change |
| Browser (CDP) | **10 passed, 0 failed** — confirmed *after* the change |

The dashboard change is frontend-only, so the backend suite is not expected to be affected — but that is an expectation, **not a measurement**. It is listed as unverified.

---

## 6. SETTINGS IN BOTH STATES

| State | Result |
|---|---|
| **STOPPED** | Editable; the 15 → 20 change saved and persisted across polling, reload and navigation |
| **RUNNING** | `POST /settings {"shutdown_timeout": 25}` → **200**, value written to disk. **No unnecessary blocking.** |

The settings endpoint applies no RUNNING-state restriction, and no field is presented as locked, so no lock messaging was required.

---

## 7. REMAINING LIMITATIONS

1. **Backend pytest suite not re-run — sandbox denied.** pytest's own temp-directory garbage collection tripped the bulk-delete guard:
   ```
   SANDBOX EXECUTION REJECTED BY USER
   Blocked paths: C:\Users\SAMYAK\AppData\Local\Temp\pytest-of-SAMYAK\garbage-<uuid>
   ```
   This is pytest cleaning up **its own** temp dirs, not RDRS data. It needs either approval for that temp path or a different `--basetemp`.
2. **E2E script (30 checks) not run** — it attempts sandbox file deletions, which were denied. Not a product defect.
3. **Browser console errors not captured.** CDP is now available, but console capture was not wired.
4. **`shutdown_timeout` is 20.0**, changed from the original 15 by the verification itself. A pre-change backup exists at `reports/backups/config/config.yaml.pre-settings-fix-20260920` (value 15). Nothing was restored.
5. `websockets` was added to the project venv for the CDP work.

---

## 8. LOCK GATE — HONEST POSITION

**No file, FR or Phase is claimed as 100/100, FROZEN or PERMANENTLY LOCKED on the basis of this cycle.**

| Item | Position |
|---|---|
| `src/rdrs/dashboard/index.html` | Modified. Front-end harness 72/72 and browser 10/10 pass, **but the backend suite has not been re-run since the change**, so it is **not** re-locked on fresh evidence |
| Backend files | **Unchanged** by this cycle |
| FR-01…FR-13 / Phase 1…Phase 9 | Previous 100/100 status **not re-verified this cycle** — treated as carried over, not freshly confirmed |
| Phase 10 / Docker | Not started, not modified |

Fresh lock evidence for the dashboard file requires the backend suite to be re-run successfully first.
