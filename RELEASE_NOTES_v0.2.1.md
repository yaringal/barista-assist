# Barista Assist v0.2.1

## Safety and actuation

- Implements real SwitchBot Bot long-press programming for per-bag pre-infusion using the published BLE command.
- Uses the existing Home Assistant SwitchBot entity for the physical press/stop action.
- Adds a user-confirmed Barista Express programmed maximum shot duration.
- Adds a configurable stop safety margin (3 s default).
- Automatic target stop and abort never press the brew button after the protected window; late aborts enter `manual_stop_required`.
- Migrates the old v0.2.0 timeout option non-destructively but requires re-confirmation of the physical machine limit before brewing.

## Packaging

- `PUBLISHING.md` is kept locally in the source workspace but is gitignored and excluded from release ZIPs.
