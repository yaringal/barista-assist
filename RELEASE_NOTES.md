# Barista Assist v0.3.11

A follow-up tuning correction to 0.3.10's actuator-delay fix: the grey pre-infusion band was still running about a second too long.

## Fixed

- 0.3.10 added a budget for the delay between a held pre-infusion's programmed duration and when flow can actually begin - but that delay itself was still about a second too long, reported live from watching the chart: real flow was visibly already rising while still inside the grey band, on most shots. Checked against real shot data, which confirmed it and pinned down the correction.

## Upgrade

No manual steps required. Update via HACS and restart Home Assistant as usual - no entities were renamed or removed in this release.

## Testing

- Full suite: 340 tests, all passing.
