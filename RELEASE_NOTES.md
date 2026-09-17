# Barista Assist v0.3.4

A shot-chart polish release: a new "Stop Prediction" marker, y-axis ticks, a fuller-width target line, tighter layout, and no more clipped/truncated text. Under the hood, two formulas that used to be duplicated between the backend and the dashboard's own JavaScript are now computed once, server-side.

## Added

- Shot charts now show a "Stop Prediction" marker - when the pour was actually expected to finish, accounting for the machine's own physical stop latency - alongside the existing "Stop Sent" marker (when the stop command was issued). Computed on the fly from the shot's own data; nothing new is stored.
- Shot charts now have y-axis (weight) tick marks and labels, matching the existing time-axis ticks.

## Changed

- The target-yield dashed line now spans the whole chart, not just the healthy window.
- Chart layout is tighter overall, with headroom so the curve/target line never sits flush against the top edge, and region labels no longer overlap each other.
- The shot-history list's yield column no longer truncates on narrower screens.

## Upgrade

No manual steps required. Update via HACS and restart Home Assistant as usual - no entities were renamed or removed in this release.

## Testing

- Full suite: 321 tests, all passing (up from 318).
