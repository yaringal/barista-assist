# Changelog

## 0.2.12

### Fixed

- **Regression in 0.2.11: the brew Bot could become completely unresponsive for a long time, with Brew stuck and Stop/Abort not doing anything.** 0.2.10 wrapped `SwitchBotBotConfigurator.async_set_long_press_duration` entirely in `bleak_retry_connector`'s `retry_bluetooth_connection_error`, intending to retry only a rare, narrow failure (the Bot dropping the link right after a successful connect, before the first GATT operation lands). But `establish_connection()` already retries the connect step internally - its own attempt count can reach the high single digits on constrained hardware - so wrapping the *entire* method multiplied that already-slow retry loop several times over on every genuine failure (e.g. the adapter simply being out of connection slots, which retrying more doesn't fix), live-observed in 0.2.11 as the brew button becoming unresponsive for a long stretch. Restructured so only the narrow case it was meant for (connect succeeds, then the GATT sequence fails) triggers one reconnect-and-retry; a hard failure of the connect step itself now fails immediately.

### Added

- **The scale's own onboard timer now stops when a shot finalizes.** Barista Assist starts it (`async_tare_and_start_timer`) at the beginning of every shot, but never told it to stop, so the scale's own display kept counting up indefinitely after a shot ended instead of freezing at the real shot duration. `BookooUltraClient` gained `async_stop_timer()` (BOOKOO command `0x05`), called from `_async_finalize` - so it only fires once a shot has actually and confidently ended, not on an ambiguous `stop_error`.
- **Added debug/info-level logging across the shot lifecycle and Bot BLE operations**, to make live issues like the one above diagnosable from the log alone: every shot-phase transition, brew/abort/stop entry (with elapsed time and reason), scale connect/disconnect events, Bot connect/program/press attempts (including `_bot_lock` waits and reconnect-retries), and shot finalization. Enable debug logging for `custom_components.barista_assist` to see it (see README's new "Debug logging" section).

### Docs

- The README's `configuration.yaml` example still showed the old `mdi:coffee-maker` sidebar icon after the earlier coffee-maker → coffee-to-go icon sweep - that example is copied into the user's own config, not anything Barista Assist regenerates, so it wasn't touched by that sweep. Updated the example; anyone who already copied it needs to update their own `configuration.yaml` to match (and restart, since YAML-mode dashboard config is only read at startup).

### Testing

- Added a regression test proving a hard connect failure is not retried again (only one `establish_connection()` call), plus kept the existing test proving a post-connect disconnect still gets exactly one retry.
- Added a regression test proving `_async_finalize` stops the scale's own timer.
- Full suite: 88 tests, all passing.

## 0.2.11

### Fixed

- **Brew and stop/abort could race each other's BLE session to the same brew Bot.** `async_brew` serializes under `_shot_lock` and `async_stop_at_target`/`async_abort` serialize under the separate `_actuation_lock` (intentionally - so a fast abort never has to wait behind brew's own slow scale-connect/tare preamble), but both eventually call the same `_async_prepare_brew_bot`/`_async_press_brew_bot`, and nothing previously stopped those from running concurrently against the same physical device from the two different locks. A live shot showed the likely consequence: the stop command fired at the correct target weight, but the pour continued for several more seconds and ~19g past target - consistent with the instant-tap reprogram silently losing a race and the press falling back to holding for the full configured pre-infusion duration. Both methods now also serialize through a dedicated `_bot_lock`. The actual press (`_async_press_brew_bot`, used by brew/stop/abort alike) only waits up to 2s for it before proceeding anyway, since it must never be blocked indefinitely behind a slow/stuck prepare call - it's the one time-critical action that has to happen regardless.

### Added

- **Brew and Abort** now also show as unavailable without a connected scale, alongside the existing bag/active-shot checks - starting a shot (or, per explicit request, stopping one) without the scale that drives the whole workflow doesn't make sense. Note this means Abort can gray out if the scale drops mid-shot; the physical machine and the Bot's own switch entity remain usable regardless.
- **A shot is now auto-aborted (best-effort) if the scale disconnects while it's active**, and its state is cleared once that abort succeeds. Previously a scale dropout left `active_shot` set forever with no way to start a new shot - even after reconnecting the scale - short of restarting Home Assistant, since Brew now also requires an active shot to *not* be set. Like a manual abort, this still correctly leaves the shot in `stop_error` rather than clearing it if the stop press itself also fails, rather than pretending a possibly-still-pouring machine is safely stopped.

### Docs

- **Clarified the shot-duration safety documentation**, confirmed against Breville's own instruction books (BES875, BES878) and live testing: the 1-CUP/2-CUP button has two distinct modes - a single tap starts "Pre-Programmed Shot Volume" mode, auto-stopping at a user-set dose, while press-and-hold (what Barista Assist always uses, to drive pre-infusion) starts "Manual Pre-Infusion & Extraction" mode instead, which the manual doesn't describe as having an equivalent auto-stop. The machine does still appear to have its own cutoff in this mode too - a held water-only test shot stopped itself after ~30s - but third-party reports suggest this is based on total pumped *volume* via an internal flow meter, not elapsed time. Whether that specific 30s is a fixed generic safety timer or tied to whatever volume happens to be programmed for that button (the BES875 manual lists 30ml/60ml as the 1-CUP/2-CUP single-tap defaults, suspiciously close to the water test) is unconfirmed - but either way, how long it takes depends heavily on flow resistance, so a real coffee puck should take meaningfully longer than a water test to hit the same cutoff. Since this isn't documented with full public confidence, `machine_max_shot_seconds` should be set from the user's own measured worst case with real coffee, not a water test. Updated README, `docs/DESIGN.md`, and the in-app options-flow description accordingly.

### Testing

- Added a regression test proving `_async_press_brew_bot` waits for an in-flight `_async_prepare_brew_bot` call rather than racing it.
- Added regression tests for Brew/Abort's new scale-connected requirement.
- Added regression tests for the scale-disconnect auto-abort.
- Full suite: 86 tests, all passing.

## 0.2.10

### Fixed

- **Brewing could still eventually fail to press the SwitchBot Bot** with `BleakDBusError: [org.bluez.Error.NotConnected]`, raised from `start_notify` immediately after `establish_connection` had already reported success - a BLE peripheral is free to drop the link right after connecting, before the first GATT operation lands. `SwitchBotBotConfigurator.async_set_long_press_duration` is now wrapped in `bleak_retry_connector`'s own `retry_bluetooth_connection_error`, which retries the whole connect-use-disconnect sequence (a fresh connection, not just the failed step) - the documented way this library expects a mid-operation disconnect to be handled.
- **A real, completed shot with a normal yield (confirmed live: ~53g from a water test) could still be classified `invalid_measurement`/`near_zero_final_weight`.** `flow_analysis.py`'s disturbance detection (see 0.2.5's changelog entry) treated any >0.5g drop below the running weight peak as the cup/scale being disturbed, with no lower bound - but scale settling noise during pre-infusion, before any real coffee mass has accumulated, can easily span several tenths of a gram on its own. A sub-gram noise dip minutes before the real pour began was truncating away the entire real pour that followed. Disturbance detection now only arms once the running peak clears 2 g, leaving its sensitivity to genuine disturbances during/after a real pour unchanged.

### Added

- **Brew** and **Abort** now correctly show as unavailable/disabled when they don't apply: Brew while a shot is already active, Abort when there's no active shot. Both previously stayed enabled at all times regardless of shot state; pressing them as a no-op wasn't unsafe, but looked like the button was broken. Backed by two new `EntityDefinition` flags, `requires_active_shot`/`requires_no_active_shot`, following the same pattern as the existing `requires_bag`/`requires_scale`.

### Testing

- Added a regression test reproducing the SwitchBot mid-connection disconnect and confirming the whole operation retries via a fresh connection.
- Added regression tests for Brew/Abort availability across shot state transitions.
- Added regression tests reproducing the pre-infusion-noise false positive and confirming a genuine mid/post-pour disturbance is still caught.
- Full suite: 81 tests, all passing.

## 0.2.9

### Fixed

- **`protocol.py` misread the BOOKOO weight/flow sign byte, so nearly every non-zero reading came out negated.** The scale's own protocol doc doesn't name the sign byte's values, and the previous decode treated any non-zero byte as negative - but confirmed against BOOKOO's own reference decoder (`makerwolf/aiobookoo`, the library behind Home Assistant's built-in `bookoo` integration), the sign byte is actually the ASCII character `'+'` (0x2B) or `'-'` (0x2D), both of which are non-zero. So both a genuinely positive and a genuinely negative reading were read as negative, and only an exact-zero magnitude passed through correctly. Live testing confirmed this: the physical scale showed a normal positive weight while Barista Assist's own sensor showed negative for the same moment. This is very likely the root cause of every shot in earlier testing classifying as `invalid_measurement`/`near_zero_final_weight` with `actual_yield_g=0.0` - the flow-analysis system was working correctly against weight data that never actually showed a real rising positive value.
- **A stuck SwitchBot Bot connection attempt could leak a Bluetooth connection slot, eventually causing every future connection to the Bot to fail** (`BleakOutOfConnectionSlotsError`) even on hardware that supports several concurrent BLE connections. `_async_ensure_quick_stop_press` used to cancel an in-flight proactive Bot reprogram and immediately start a fresh one - but `bleak_retry_connector`'s `establish_connection()` has no cancellation cleanup, so cancelling it mid-connect never disconnects the partially-established client, and starting a second connection to the same device on top of that leak compounds the problem with every shot. It now waits (bounded to 3s) for the existing attempt to finish instead of cancelling it, and never starts a competing connection to the same Bot while one is still in flight - falling back to holding for the configured pre-infusion duration on that one press if the wait times out, exactly as it already did for a real failure.

### Testing

- Added `test_unrecognized_sign_byte_is_treated_as_zero` and corrected the two existing packet fixtures (which had been using an invalid sign byte that only worked by coincidence with the old, incorrect decode logic).
- Rewrote `test_fallback_cancels_stuck_proactive_reprogram_instead_of_racing` as `test_fallback_waits_for_stuck_proactive_reprogram_without_racing_it`, matching the new wait-not-cancel behavior.
- Full suite: 76 tests, all passing.

## 0.2.8

### Fixed

- **The actual root cause of the "calls async_write_ha_state from a thread other than the event loop" flood** (thousands of occurrences per session, logged as a RuntimeError, not just a warning): `entity.py`'s dispatcher-connected `_handle_runtime_update` was a plain, undecorated method. Home Assistant's job scheduler treats an undecorated callable passed to `async_dispatcher_connect` as possibly-blocking and defensively runs it in the executor thread pool instead of inline on the event loop - so *every* entity-state dispatch, from any trigger, was being routed off-thread regardless of the 0.2.6 BLE-callback marshaling fix. Marked it `@callback`, which is what actually told Home Assistant it's safe to run inline. This was also the root cause of several once-mysterious symptoms reported during live testing that all trace back to state writes silently failing to reach the frontend: the scale showing "Unavailable" while genuinely connected, the active bag reading "Unknown" after being set, the Bean slot select getting stuck after a second change, and Stop Compensation not staying in sync between Settings and the Live Shot tile.
- A second real BLE thread-safety bug, same class as 0.2.6's: `switchbot.py`'s Bot-response notification callback touched a plain `asyncio.Event`/dict directly from bleak's raw callback thread. Now marshals onto the event loop first, like `bookoo.py` already did.
- `mdi:coffee-bean` (the 0.2.6 "fix" for the Bags tab/active-bag/beans-remaining icons showing blank) turned out to be just as invalid as the `mdi:coffee-beans` it replaced - neither name exists in Material Design Icons. Switched to `mdi:sack`, and added `tests/test_icons.py`, which checks every `mdi:` reference in the repo against a real snapshot of the MDI catalog so an invented icon name can't ship undetected again.
- Every dashboard tile showed its entity's full "Barista Assist <name>" friendly name as its header, which overflows the tile on typical screen widths. Every tile now sets an explicit short `name:` instead.
- Swapped `mdi:coffee-maker` for `mdi:coffee-to-go` across the Brew view, its "Ready to brew" section heading, the System view heading, and the `brew` entity/service icons.

### Docs

- README: documented that Barista Assist needs at least 2 concurrent Bluetooth connection slots (the BOOKOO scale holds one continuously; brewing briefly opens a second to the SwitchBot Bot) - a single adapter/ESPHome proxy that only supports one connection at a time will fail the Bot connection with `BleakOutOfConnectionSlotsError` every time you brew while the scale is connected. This is a Bluetooth capacity limit, not a bug, and Barista Assist already degrades gracefully (falls back to a full-length press instead of an instant tap) when it happens; the fix is adding a second proxy/adapter near the machine.

### Testing

- Added `tests/test_entity.py`, confirming `_handle_runtime_update` is marked as a Home Assistant callback (fails without the fix, verified via `git stash`).
- Added `tests/test_switchbot.py`, reproducing the switchbot.py thread-safety bug the same way `test_bookoo.py` does (fails/hangs against the pre-fix code, verified via `git stash`).
- Added `tests/test_icons.py` (icon-name validation against the real MDI catalog) and a dashboard test asserting every tile declares a `name:`.
- Full suite: 75 tests, all passing.

## 0.2.7

### Changed

- **Replaced the Community Dashboard strategy with a YAML-mode dashboard file.** After shipping, v0.2.6's `Content-Type` fix for the "timeout waiting for strategy element ... to be registered" error turned out not to be the actual cause: further live debugging (including confirming against Home Assistant's own frontend source and community reports) traced it to a bug in Home Assistant 2026.5's own new `window.customStrategies` browser registration mechanism, affecting multiple unrelated projects that use it — not anything specific to this integration's code — and the community's suggested manual-resource workaround didn't reliably fix it on mobile clients either. Rather than depend on a mechanism outside this project's control, the integration now writes a fully token-substituted, `views:`-only dashboard file (`barista_assist_dashboard.yaml`) directly into the Home Assistant config directory on every setup/reload (`websocket.render_dashboard_yaml` / `websocket.async_write_dashboard_file`), for a plain YAML-mode Lovelace dashboard entry to read. This still auto-updates on every release with no browser-side registration step at all, at the cost of one additional one-time `configuration.yaml` edit instead of a menu click — see README's "Add the dashboard once" for the new setup. The now-irrelevant `.js`-`Content-Type` fix from v0.2.6 has been removed along with the dashboard-strategy class and the `barista_assist/get_dashboard` WebSocket command it used; the shot-export card and its WebSocket endpoint are unchanged.

### Testing

- Added `tests/test_dashboard_yaml.py` covering `render_dashboard_yaml`: substitutes entity tokens, emits `views:`-only YAML (no `title`), and leaves non-token strings untouched.
- Full suite passing (see repository `tests/` for the current count).

## 0.2.6

### Fixed

- **`bookoo.py` called `async_write_ha_state` from outside the event loop**, hundreds of times per shot, logged by Home Assistant as a thread-safety violation that "may cause Home Assistant to crash or data to corrupt." `BookooUltraClient` registered its BLE notification and disconnect handlers directly as bleak's raw callbacks, which are not guaranteed to run on the event loop — the exact thread depends on the platform's BLE backend. Both callbacks now marshal onto the event loop via `hass.loop.call_soon_threadsafe` before touching any state, regardless of which thread bleak actually calls them from. Found on a real install during v0.2.5 bring-up.
- `mdi:coffee-beans` isn't a real Material Design Icon (the real name is singular, `mdi:coffee-bean`), so every icon using it rendered blank — including the Bags dashboard tab, `active_bag`, `beans_remaining`, `bean_slot`, and `new_bag_coffee`. Fixed everywhere.
- `dashboard_template()` and `load_definitions()` were each cached once per process (`@lru_cache`), so a HACS update replacing `dashboard.yaml`/`definitions.yaml` had no effect until a full Home Assistant restart — contradicting the documented "takes effect after the integration/Home Assistant reloads" behavior. Both now re-parse automatically whenever the file's mtime changes, with no restart required.
- That reparsing is real file I/O (`read_text`), which Home Assistant flags as a blocking call if it happens directly on the event loop. Every call site that can trigger it (`async_setup`, `async_setup_entry`, and the `barista_assist/get_dashboard` websocket handler) now goes through `hass.async_add_executor_job` first, so a cache-refresh triggered by a live HACS update or a config-entry reload can't block the event loop.
- The Brew/Bags view's action-button tiles (Brew, Tare, Abort, Create bag) showed a live "time since last pressed" counter as their state text, which reads as a odd/confusing default for a momentary action button. Suppressed via `state_content: []`.
- The shot-export card's clipboard-success message said "You can paste it directly here" even though there's nothing on the card to paste into on the success path (that text only makes sense in the Clipboard-API-unavailable fallback, where a textarea does appear). Shortened to "Copied to clipboard."
- Removed a stale "### v0.2.0" implementation note from the System dashboard view.
- **The dashboard strategy could fail to register at all, with "timeout waiting for strategy element ... to be registered" and no other visible error.** `barista-assist-dashboard.js` was served with whatever `Content-Type` the host's system MIME database happens to map `.js` to; on hosts where that's not a recognized JavaScript type, browsers silently refuse to execute a `<script type="module">` served with the wrong type — the script's `customElements.define(...)` call for the strategy then never runs. Explicitly registers `.js` as `text/javascript` at startup instead of depending on the host's configuration.

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
