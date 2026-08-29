# Barista Assist v0.2.10

More fixes found during v0.2.9 bring-up, plus two small dashboard usability fixes.

## Fixed

- **Brewing could still occasionally fail to press the SwitchBot Bot**, even after v0.2.9's connection-leak fix, with an "already disconnected" style error. A Bluetooth accessory can drop its connection right after connecting, before Barista Assist gets to actually use it - now, if that happens, Barista Assist automatically retries the whole operation (a fresh connection) instead of giving up on the first attempt.
- **A real, fully-poured shot could still be logged as an invalid/near-zero measurement** even with a normal yield. The shot-diagnosis feature was over-eagerly treating ordinary scale settling noise during pre-infusion (before any real coffee mass has landed) as if the cup had been physically disturbed, discarding the entire real pour that followed. It now waits until a shot has genuinely accumulated some weight before it starts watching for a real disturbance.

## Added

- **Brew** and **Abort** now show as unavailable when they don't apply - Brew while a shot is already brewing, Abort when nothing is brewing - instead of always looking pressable regardless of what's actually happening.

## Testing

- 4 new regression tests (SwitchBot reconnect-and-retry, Brew/Abort availability, disturbance-detection floor).
- Full suite: 81 tests, all passing.

## Upgrade

No database migration. A full Home Assistant restart is needed to pick up this release.
