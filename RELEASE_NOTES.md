# Barista Assist v0.2.14

A real shot-timing bug fix, based on a live coffee shot, plus two small dashboard/status polish fixes.

## Fixed

- **A slow Bluetooth connection to the brew Bot could silently eat into a shot's safety deadline and throw off its flow diagnosis.** A live coffee shot showed a ~50-second Bluetooth delay connecting to the Bot counted as if it were part of the shot itself - the exported data showed a long flat prefix before any real flow, the shot got mislabeled `too_restrictive` even though the coffee itself extracted at a totally normal rate once it actually started, and the shot got cut off by the safety timeout despite the machine only having run for a normal amount of time. The fix: the safety deadline and every recorded sample's timing are now both measured from when the brew Bot was **actually pressed**, not from when brewing was requested - so Bluetooth connection delays no longer count against either the shot's safety window or its measured duration. The scale's own physical timer and tare have the same fix: they now start right after the press lands too, instead of before the Bot connection was even attempted, so the scale's own on-device display and its zero-weight reference reflect the real start of the shot as well.
- **The Brew/Tare/Abort/Create-bag tiles still showed a timestamp or "Unavailable" text** that was supposed to have been hidden since v0.2.6. The setting used for that (`state_content: []`) turned out to never have been the officially documented way to do this - switched to the real option, `hide_state: true`, confirmed against Home Assistant's own tile-card documentation.

## Added

- **The Status sensor now shows "Connect scale" instead of "Idle" when there's no active shot and the scale isn't connected** - "idle" reads as "everything's fine," which isn't true if brewing can't actually start yet.

## Testing

- 5 new regression tests: the status sensor's new behavior, readings before the brew Bot press are dropped and timing resets to the press, a slow connection doesn't trip the safety deadline, and the scale is tared/timer-started after the press, not before.
- Full suite: 94 tests, all passing.

## Upgrade

No database migration. A full Home Assistant restart is needed to pick up this release.
