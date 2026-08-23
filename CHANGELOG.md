# Changelog

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
