# Barista Assist v0.2.12

Fixes the brew Bot becoming unresponsive after updating to v0.2.11, plus much more detailed logging.

## Fixed

- **The brew Bot could become unresponsive for a long time**, with Brew stuck and Stop/Abort not doing anything - a regression introduced in v0.2.11. An earlier fix for a rare BLE hiccup ended up retrying much more aggressively than intended on *every* connection failure, including ones retrying can't fix (like the adapter simply being full) - live-observed as the whole integration seizing up around brewing. Now only retries the narrow case it was meant for; a real connection failure fails promptly again, like it did before v0.2.11.

## Added

- **The scale now stops its own onboard timer when a shot finishes**, instead of leaving it running indefinitely - its display should now freeze at the actual shot duration.
- **Much more detailed debug logging** across shot phases, brew/stop/abort calls, scale connection events, and every brew-Bot Bluetooth operation - see the README's new "Debug logging" section for how to turn it on. Should make the next live issue much faster to diagnose from the log alone.

## Docs

- Fixed the sidebar-icon example in the README's `configuration.yaml` setup snippet, which still showed the old icon after the previous coffee-maker → coffee-to-go icon change (that example lives in your own config, so the earlier sweep never touched it). **If you already added the `lovelace.dashboards` block from the README, update its `icon:` line yourself to match** - Barista Assist has no way to update your `configuration.yaml` for you.

## Testing

- 2 new regression tests: a hard connect failure is not retried again, and finalizing a shot stops the scale's timer.
- Full suite: 88 tests, all passing.

## Upgrade

No database migration. A full Home Assistant restart is needed to pick up this release (and again after editing `configuration.yaml`, if you update the sidebar icon).
