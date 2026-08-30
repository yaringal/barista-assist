# Barista Assist v0.2.16

Fixes Auto PI (added in 0.2.15) so it's actually reachable in the dashboard.

## Fixed

- **Auto PI now has a real toggle**: `switch.barista_assist_auto_pi`, shown on the System view's "Connection and control" card. It previously shipped as a config-flow-only option with no dashboard entity, so there was no way to turn it on from the UI.
- **The Pre-infusion tile hides via a dashboard `visibility` condition** on that switch, instead of rewriting the generated dashboard file on every toggle - it now updates instantly in the browser.

## Upgrade

No database migration. A full Home Assistant restart is needed to pick up this release. If you'd already tried to enable Auto PI via Settings on 0.2.15, that option is gone - use the new dashboard toggle instead (off by default, so existing setups are unaffected either way).
