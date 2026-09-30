# Barista Assist v0.3.10

A second classification-accuracy fix, plus a matching chart correction: shots with a held (app-controlled) pre-infusion get a fairer read.

## Fixed

- A shot with a held pre-infusion (Adapt PI on) could still be classified "too restrictive" even when a barista judged it healthy or balanced - reported live: "the PI programmed is 7s but we count 10s with no flow." The app-controlled Bot needs real time to physically engage, hold, and release before flow can begin, beyond the programmed hold duration itself - the expected-time budget now accounts for that. A machine-controlled pre-infusion is unaffected.
- The shot chart's grey "Pre-infusion" band now shows the real held window instead of starting from the very beginning of the shot - it was shading a stretch of time that wasn't actually pre-infusion.
- That delay was tuned down by about a second after a live report that flow was visibly already rising while still inside the grey band on most shots - checked against real shot data, which confirmed it.

## Upgrade

No manual steps required. Update via HACS and restart Home Assistant as usual - no entities were renamed or removed in this release.

## Testing

- Four real shot fixtures reclassify under the fix - three correctly back to healthy, one to too fast (with the chart's own shading confirmed to still agree with the new classification).
- Full suite: 340 tests, all passing (up from 338).
