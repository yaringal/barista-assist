# Barista Assist v0.3.1

A dashboard-polish follow-up to v0.3.0: shot history now shows flavor tags, grind-correction bands show a live "how many seconds is this" hint, pending recipe recommendations get a ⚠️ to draw the eye, and a couple of layout/step-size tweaks.

## Added

- **Shot history now shows each shot's own Extraction/Mouthfeel flavor tags**, right after Roaster in the Shots view's expanded detail.
- **Grind-correction band tiles now show a live "how many seconds is this" hint** next to each band's raw duration_ratio value (e.g. "≤ 16.9s" or "16.9s .. 21.1s"), computed from your own live settings rather than a fixed number.
- **Dose/Grind/Target yield/Temperature offset now show a ⚠️ next to their "current → recommended" note when a recommendation is pending**, so it's easier to spot at a glance.

## Changed

- Recipe target yield's dashboard +/- step is now 1g, not 2.5g.
- The System view's settings were reorganized: "Integration settings (Configure)" moved to the very end of the view.

## Fixed

- A dashboard-internal bug where a live template referencing an entity ID embedded inside a longer string (rather than as the whole value) silently failed to resolve.

## Upgrade

No manual steps required. Update via HACS and restart Home Assistant as usual - no entities were renamed or removed in this release.

## Testing

- Full suite: 316 tests, all passing (up from 311).
