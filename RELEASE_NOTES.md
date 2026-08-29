# Barista Assist v0.2.8

Fixes found during v0.2.7 bring-up on a real install: no new functionality, but this closes out a cluster of bugs that were all secretly one bug.

## Fixed

- **Found the actual cause of the "calls async_write_ha_state from a thread other than the event loop" errors** that kept appearing even after 0.2.6's BLE thread-safety fix (thousands of occurrences per session, and Home Assistant treats these as hard errors, not just warnings). The real problem was in `entity.py`: the function Home Assistant calls whenever any Barista Assist entity needs to refresh wasn't marked as a Home Assistant "callback," so Home Assistant defensively ran it in a background thread instead of on its own event loop, every single time. That's also what was silently causing several other confusing symptoms during live testing, because the entity's new state was never actually reaching the frontend:
  - the scale showing **Unavailable** even while genuinely connected;
  - the active bag reading **Unknown** after being set;
  - the **Bean slot** selector getting stuck after switching it a second time;
  - **Stop Compensation** not staying in sync between Settings and the Live Shot tile.

  All four should now update normally.
- Fixed a second, similar BLE thread-safety bug in the SwitchBot Bot connection code (same class of bug as 0.2.6's scale fix, just in a different file).
- **The Bags tab, active bag, and beans-remaining icons were still blank.** 0.2.6's fix (`mdi:coffee-bean`) turned out to be just as made-up as the icon name it replaced - neither exists in Material Design Icons. Switched to a real icon (`mdi:sack`) this time, and added an automated check against the actual icon catalog so this can't happen a third time.
- **Every dashboard tile's title overflowed**, showing the full "Barista Assist <name>" text instead of a short label. Every tile now shows a short, readable name.
- Swapped the coffee-maker icon for a coffee-to-go icon across the Brew view and a few related entities/services, per request.

## Docs

- Documented a real Bluetooth limitation some setups will hit: Barista Assist needs **two** concurrent Bluetooth connections available (one held continuously by the scale, one briefly opened per shot for the SwitchBot Bot). A single adapter or Bluetooth proxy that only supports one connection at a time will fail the Bot connection while the scale is connected - this is a capacity limit of your Bluetooth setup, not a bug, and Barista Assist already falls back gracefully when it happens. See the README's "SwitchBot requirement" section for the fix (an additional Bluetooth proxy near the machine).

## Testing

- 4 new regression tests added, each verified to fail against the pre-fix code, for: the missing `@callback`, the SwitchBot thread-safety bug, invalid icon names, and missing tile names.
- Full suite: 75 tests, all passing.

## Upgrade

No database migration. A full Home Assistant restart is needed to pick up this release, same as any integration update.
