# Barista Assist v0.2.3

A safety- and correctness-focused patch release. No new features and no database migration — every change here is a bug fix found and verified during a full review of the shot-control state machine, config flow, and declarative entity layer.

## Most important fix

**Automatic stop-at-target-weight never actually pressed the brew button.** A single flag was being used for two different purposes — "a stop has been scheduled" and "the stop actually happened" — and setting it for the first purpose accidentally satisfied the guard for the second. The result: once a shot crossed its target weight, the auto-stop task silently no-opped instead of pressing the button, and the same flag also disabled the timeout safety net and manual abort from that point on. If you were relying on automatic brew control, **update before your next shot**.

## Also fixed

- A failed stop/abort press could be reported as a successful one instead of leaving the shot flagged for attention.
- The stop/abort press held for the full pre-infusion duration instead of an instant tap, because the SwitchBot Bot's stored press-hold time wasn't reprogrammed back down before pressing to stop.
- The options flow (Settings → editing an existing install) accepted a stale or removed brew SwitchBot entity without validation; a possible setup-flow crash if the BOOKOO scale stops advertising mid-form.
- `scale_battery` stayed "available" showing a frozen reading forever after the scale disconnected.
- The Brew button never went unavailable with no bag selected.
- Bag creation only validated the grind field; other out-of-range recipe values could be stored unvalidated.
- The dashboard's WebSocket endpoint no longer blocks the event loop on its first request after a restart.
- The shot-data clipboard-copy fallback now shows the export text in a visible, selectable field instead of an unreliable scripted copy.
- Restored Python 3.10 compatibility.

See `CHANGELOG.md` for the complete list.

## Testing

This release adds a new runtime state-machine test harness with 16 tests directly covering the shot-control fixes above (auto-stop, abort/stop failure handling, instant-tap reprogramming, and the deadline safety invariant), plus a consistency check between `services.yaml` and `definitions.yaml`. Total suite: 21 → 37 tests, all passing.

## Upgrade

No database migration is required. If automatic brew control is enabled, re-confirm the Barista Express's programmed maximum shot duration is still correct before your first shot after upgrading, and re-run the water bench test described in the README.
