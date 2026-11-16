# Barista Assist v0.3.14

More accurate shot timing when the scale's Bluetooth hiccups, and clearer Shot History details.

## Fixed

- **Bluetooth hiccups distorting shots.** When the scale's Bluetooth stalls and then delivers a burst of readings at once, those readings used to look like a huge flow spike and drew a step in the chart. Readings are now timed by the scale's own clock, so the stop decision, the shot diagnosis and the charts all see the pour as it really happened.
- **"Total duration" in Shot History** now runs to when the brew Bot actually pressed stop, not to when the stop was sent (up to ~2s earlier).

## Added

- **"Yield vs target"** in each shot's details in Shot History, e.g. "+2.9g (8% over)". The Result column only judges how long the shot took, so this is where an overshoot shows.

## Upgrade

Update via HACS, then **restart Home Assistant**. No database changes. Past shots keep their recorded diagnosis; their charts are redrawn with the new timing.
