# Barista Assist v0.3.7

A real classification bug fix, plus the last of the chart's "max weight" legend inaccuracies.

## Fixed

- A real, mechanically normal shot could be wrongly marked invalid ("too few samples") because of a stale leading scale reading (like a leftover portafilter weight) that wasn't being cleaned up before analysis. Two real shots that hit this now classify correctly.
- The shot chart's "Weight (max ...g)" legend could still show a higher number than the shot actually reached, in some cases even after last release's fix for this. It now always shows the real recorded max.

## Upgrade

No manual steps required. Update via HACS and restart Home Assistant as usual - no entities were renamed or removed in this release.

## Testing

- Added two more real shot fixtures from live reports of the classification bug above.
- Full suite: 331 tests, all passing (up from 326).
