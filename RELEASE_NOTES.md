# Barista Assist v0.2.9

Two significant fixes found during v0.2.8 bring-up: no new functionality.

## Fixed

- **The scale's weight and flow readings could show negative when the physical scale itself showed a normal positive value.** Traced this to a real decode bug: BOOKOO's scale sends its weight/flow sign as an ASCII `'+'` or `'-'` character, not a plain yes/no flag, and Barista Assist's decoder had been treating *any* non-zero sign byte as negative - which means both `'+'` and `'-'` (both non-zero) were read as negative. Confirmed against BOOKOO's own reference decoder library. This is very likely why every test shot so far was logged as an invalid/zero-yield measurement: the diagnosis system was working correctly against weight data that never actually showed a real, rising positive value. Real shots should now read correctly.
- **A brew could eventually stop being able to press the SwitchBot Bot at all**, failing with an "out of connection slots" error even on Bluetooth hardware that supports several simultaneous connections. The cause was a slow-building leak: when the fast, time-critical stop/abort path needed the Bot reprogrammed to an instant tap, it would cancel any reprogram attempt already in progress and immediately start a new one - but cancelling an in-progress Bluetooth connection attempt doesn't clean it up, so the abandoned connection stayed open, one leak per affected shot, until the adapter had no free connection slots left for anything. It now waits briefly for the existing attempt instead of cancelling it, and never opens a second connection to the same device while one is already in flight.

## Testing

- 3 new/rewritten regression tests covering both fixes.
- Full suite: 76 tests, all passing.

## Upgrade

No database migration. A full Home Assistant restart is needed to pick up this release.
