# Changelog

## 0.2.5

### Added

- Added `flow_analysis.py`: a pure, dependency-free Stage-1 shot-flow classifier (`healthy` / `too_fast` / `too_restrictive` / `puck_prep_issue` / `invalid_measurement`) plus a channeling-suspicion score, per `docs/DESIGN.md`'s diagnostic architecture (sections 8-13). `puck_prep_issue` is judged primarily against fixed mechanical priors so it works from a bag's first shot, and takes priority over the fast/slow duration check (a shot that's both fast and shows a channeling signature is a puck-prep problem to fix first, per section 14). A per-bag baseline, once one exists, can only ever raise that suspicion score, never lower it, so a recurring problem can't normalize itself out of detection. The expected flow rate used for the fast/slow duration check, by contrast, is a genuine Bayesian shrinkage estimate that blends the global prior with a bag's own healthy-shot history and is allowed to fully self-normalize toward that bag's characteristic pace — there's no recurring-problem risk to protect against for "this bag just runs faster/slower than average." `analyze_shot` also flags a shot `invalid_measurement` when its first detected flow is implausible - either never detected despite a real final weight, or detected well before the configured pre-infusion should have ended. The too-fast/too-restrictive duration thresholds are anchored against `docs/DESIGN.md`'s own worked Example A rather than picked arbitrarily. The thresholds remain a single calibration anchor, not derived data (tracked as Phase 3b in `docs/DESIGN.md`). A flow-smoothness/noise check was deliberately not added - every variance-based approach tried also fired on a genuine `puck_prep_issue` shot, so it's left for real recorded scale noise to calibrate against.
- `storage.py` (schema v3) can now persist a shot's flow-analysis result — `classification`, `channeling_suspicion`, and the full feature set as `analysis_json` — and `recent_healthy_features(bag_id)` computes the median late-shot acceleration and flow rate from a bag's recent healthy shots, shaped for `flow_analysis.BaselineFeatures`.
- `runtime.py._async_finalize` now calls `flow_analysis.analyze_shot` for every finalized shot, using the bag's own recent healthy-shot history as the baseline, and persists the result.
- Added `shot_classification` and `shot_channeling_suspicion` sensors (new tiles on the Brew view's Live shot section), so every shot's flow diagnosis is now visible. `puck_prep_issue` displays as "Puck prep issue" via a proper state-translation entry, not the raw stored value.
- `export_shots_text` now includes `classification`, `channeling_suspicion`, and the full `analysis_json` in each shot's metadata block, so exported traces carry their diagnosis for future re-analysis.
- `analyze_shot` now detects the cup or scale being disturbed (lifted, bumped, moved) anywhere in the trace — raw weight can only rise during a real pour, so a meaningful drop below its own running peak is unambiguous interference — and discards everything from that point on before classifying the trustworthy prefix. No timestamp-based cutoff or user awareness of "when it's safe to touch the cup" is needed.
- Every `invalid_measurement` shot now carries an `invalid_reason` (too few samples, near-zero final weight, no detected flow, flow starting before pre-infusion should have ended, or a disturbance leaving too little trustworthy data), and `runtime.py` logs it — so an invalid shot can be diagnosed (e.g. a BLE dropout vs. a disturbed cup) instead of showing up as an unexplained `invalid_measurement`.

### Changed

- Reorganized `runtime.py` into clearly labeled sections and removed several duplicated code paths (background-task cancellation, the stop/abort press-and-raise handling, `entity_value`'s source dispatch, and `async_new_bag`'s per-field validation) with no behavior change.

### Fixed

- The README's "Not implemented yet" list still claimed dynamic SwitchBot long-press programming was missing; it shipped back in 0.2.1. Removed the stale entry.

### Docs

- `docs/DESIGN.md`: marked Phases 1-2 as implemented, recorded Phase 3's actual status, and added Phase 3b (deriving thresholds from real shot data) as a named follow-up. Also documented why a late abort waits for the machine's own timer before finalising the shot log instead of stopping immediately.

## 0.2.4

### Fixed

- Fixed a fresh-install failure on Home Assistant 2026.8: `manifest.json` pinned `bleak-retry-connector==4.6.0`, which conflicts with HA 2026.8's own constraint of `bleak-retry-connector==4.6.3` for that shared dependency. HA's requirements installer failed with `RequirementsNotFound`, and the frontend surfaced it as "Config flow could not be loaded: 500 internal server error." The pin now matches what HA 2026.8 already requires.

### Repository

- Removed the redundant top-level `VERSION` file; nothing read it, and `manifest.json`'s `version` field (already read by `const.py` at runtime) is now the single place that needs bumping per release. `PUBLISHING.md`'s release commands are parametrized with an exported `$VERSION` accordingly.
- Removed the current version number from README's section headers ("Architecture", "What this integration does", "Not implemented yet" (renamed from "Deliberately not in..."), "SwitchBot requirement") so the README doesn't need editing purely to stay in sync with a release.

## 0.2.3

### Fixed

- **Automatic stop-at-target-weight never actually pressed the brew button.** A flag reused for two purposes (scheduling the stop vs. confirming it happened) made the auto-stop task a no-op every time — the shot would keep pouring past its target. Also silently disabled the timeout safety net and manual abort once triggered.
- **A failed stop/abort press could be reported as a successful one.** `abort` swallowed a failed brew-button press and finalized the shot anyway, even though the machine may still have been pouring unattended.
- **The stop/abort press held for the full pre-infusion duration instead of an instant tap**, because nothing reprogrammed the SwitchBot Bot's stored press-hold time back down before pressing to stop. Added a proactive reprogram once extraction begins, with a race-safe fallback (cancel-and-retry, not a shared lock) so the fallback can't itself introduce a concurrent-BLE-connection race or block the urgent press behind non-urgent prep work.
- **Config options flow accepted a stale/removed brew SwitchBot entity** without validation, unlike initial setup; it now validates the same way. Fixed a possible crash (`KeyError`) in initial setup if the BOOKOO scale stops advertising between showing and submitting the form.
- **`scale_battery` sensor stayed "available" showing a frozen reading forever after the scale disconnected** — it now correctly goes unavailable like its sibling sensors.
- **The Brew button never went unavailable with no bag selected.**
- **Bag creation only validated the grind field**, so an out-of-range value for dose, target yield, temperature offset, or pre-infusion could be stored unvalidated and later crash the temperature-offset select entity. All recipe fields are now validated.
- Removed an unreachable dead-code safety check in the config flow (the selector bounds already made it impossible to trigger) and completed missing `options.error` translations.
- The dashboard's WebSocket endpoint no longer blocks the event loop reading/parsing its YAML template on the first request after a restart; the cache is now warmed at startup like `definitions.yaml` already was.
- The shot-data clipboard-copy fallback (when the Clipboard API is unavailable, e.g. in the companion app) now shows the export text in a visible, selectable field instead of an unreliable scripted copy across the shadow-DOM boundary.
- Replaced `enum.StrEnum` (Python 3.11+ only) with a Python 3.10-compatible equivalent.

### Tests

- Added a runtime state-machine test harness (fakes for `hass`/BLE, no real Home Assistant install required) covering the shot-control fixes above, plus a `services.yaml`/`definitions.yaml` consistency check. Suite grew from 21 to 37 tests.

## 0.2.2

- Keep `PUBLISHING.md` in the source/release archive while retaining it in `.gitignore` so local publishing instructions are not committed to GitHub.
- Add a package-managed **Copy all shot data** card to the Brew view.
- Add a WebSocket export endpoint that returns all persisted shot metadata and raw scale samples as paste-friendly text.
- Mark samples recorded at or after the stop command with `post_stop=1` to make late scale movement and settling artefacts easy to inspect.

## 0.2.1

- Implement real SwitchBot Bot long-press programming for per-bag pre-infusion using the published BLE protocol.
- Add a user-confirmed Barista Express programmed maximum shot-duration safety limit plus configurable stop margin.
- Prevent target-stop and abort commands from pressing the brew button after the protected window, avoiding accidental new shots.
- Add explicit `manual_stop_required` state for late aborts.
- Keep stable `barista_assist.brew`, `barista_assist.abort`, `barista_assist.tare`, and `barista_assist.select_slot` actions.
- Keep local `PUBLISHING.md` in the repository workspace, gitignored and excluded from release archives.


## v0.2.0 - 2026-08-19

### Changed

- Refactored user-facing entity metadata, ranges, defaults, mappings, slots and dashboard tokens into `definitions.yaml`.
- Replaced one-class-per-field entity implementations with generic declarative entity adapters.
- Reduced the custom WebSocket API to the single endpoint required by the Community Dashboard strategy.
- Kept the stable `barista_assist.brew`, `barista_assist.abort`, `barista_assist.tare` and `barista_assist.select_slot` Home Assistant actions.
- Made Barista Assist explicitly single-config-entry.
- Moved entity names to custom-integration translations and icons to `icons.json`.
- Simplified the dashboard YAML and removed repeated entity names/icons where entity metadata already supplies them.
- Changed first-bag default target yield from 38 g to **36 g** for an 18 g / 1:2 flat-white starting recipe.
- Moved pre-infusion from an integration-wide setting into each physical bag's recipe.
- Moved selected slot and stop compensation out of SQLite history storage into lightweight Home Assistant state storage.
- Stopped persisting unfinished new-bag form fields.
- Moved SQLite schema DDL into versioned `.sql` migration files.
- Added partial recipe-field updates instead of rewriting an entire recipe for each numeric change.
- Replaced the custom runtime listener set with Home Assistant dispatcher updates.
- Removed the unused runtime snapshot/recent-shot cache from the UI path.
- Version is now read from `manifest.json` by runtime code rather than duplicated in Python constants.

### Migration

- Existing v0.1.x bag and shot history is retained.
- Database schema migrates from v1 to v2 by adding per-bag `preinfusion_s`.
- The old global PI is copied to existing active bags during migration.
- Legacy selected slot and stop compensation are adopted into the new application state on first v0.2 load.

### Tests

- Added definitions/default validation.
- Added dashboard-token consistency checks.
- Added partial recipe update coverage.
- Added explicit v0.1 -> v0.2 SQLite migration coverage.

## v0.1.1 - 2026-08-16

- Moved the visible dashboard to package-owned Lovelace YAML.
- Added Community Dashboard strategy registration.
- Added dashboard-facing recipe and bag entities.

## v0.1.0 - 2026-08-16

- Initial self-contained Barista Assist custom integration.
