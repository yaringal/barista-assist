# Barista Assist v0.2.6

Safety and UI fixes found during v0.2.5 bring-up on a real install: no new functionality.

## Fixed

- Home Assistant logged hundreds of "calls `async_write_ha_state` from a thread other than the event loop" warnings per shot, which HA itself flags as something that "may cause Home Assistant to crash or data to corrupt." The BOOKOO scale's BLE notification and disconnect handlers weren't guaranteed to run on Home Assistant's event loop — the exact thread depends on your system's Bluetooth backend — so every scale reading during a shot could trigger this. Both callbacks now always hand off to the event loop safely before touching any state, no matter which thread the Bluetooth stack actually calls them from. If you saw this warning in your logs, updating to this release resolves it.
- The Bags dashboard tab's icon (and a few entity icons: Active bag, Estimated beans remaining, Bean slot, New bag coffee) rendered blank. The icon name used, `mdi:coffee-beans`, isn't a real Material Design Icon — the actual name is singular, `mdi:coffee-bean`. Fixed everywhere it was used.
- Editing `dashboard.yaml` or `definitions.yaml` (including via a HACS update) had no effect until a full Home Assistant restart, even though the README describes updates taking effect "after the integration/Home Assistant reloads." Both files are now re-read automatically whenever they change on disk — no restart needed to pick up a new dashboard or definitions layout.
- The Brew/Bags/System button tiles (Brew, Tare, Abort, Create bag) showed a live "X minutes ago" counter under their name — the default state display for a momentary action button, which isn't meaningful here. Removed.
- The shot-export card said "Copied to clipboard. You can paste it directly here" even on a plain successful copy, where there's nothing on the card to paste into. Shortened to "Copied to clipboard."
- Removed a stale "v0.2.0" implementation note from the System dashboard view.

## Testing

- 2 new tests (`tests/test_bookoo.py`) reproduce the BLE thread-safety bug directly: invoking the callbacks from a background thread and confirming the resulting state change lands on the main/event-loop thread instead. Verified against the pre-fix code that they fail as expected.
- 1 new test (`tests/test_definitions.py`) reproduces the stale-cache bug: bumping `definitions.yaml`'s mtime and confirming `load_definitions()` re-parses instead of returning the old cached result.
- Full suite: 65 tests, all passing.

## Upgrade

No database migration. A full Home Assistant restart is still needed once to pick up this release's code changes (as with any integration update) — after that, future `dashboard.yaml`/`definitions.yaml` changes won't require a restart.
