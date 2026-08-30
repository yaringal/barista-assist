# Barista Assist v0.2.15

A status-sensor polish fix, plus a new opt-in Auto PI brewing mode.

## Added

- **The Status sensor now shows a proper label for every state** (e.g. "Connect scale" instead of the raw `connect_scale`) - previously every value showed as its raw internal name.
- **New "Auto PI" option** (Settings): brew with a single short tap and let the Barista Express run its own built-in pre-infusion (~8s) instead of Barista Assist holding the button for a per-bag duration.
  - Both the start and stop presses go through Home Assistant's switchbot integration only - no direct BLE connection to the Bot is opened at all, since there's no per-bag hold duration to program.
  - The Pre-infusion tile is hidden from the Recipe section of the dashboard while this is enabled, since its value no longer affects anything.
  - See the README's new "Auto PI" section for the full tradeoffs, including how it interacts with the Barista Express's own single-tap auto-stop-at-volume behavior.

## Upgrade

No database migration. A full Home Assistant restart is needed to pick up this release. Auto PI is off by default - existing setups keep behaving exactly as before unless you turn it on in the integration's options.
