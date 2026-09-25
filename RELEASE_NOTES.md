# Barista Assist v0.3.8

A real classification-accuracy fix: shots that finish inside the chart's own "healthy" window no longer get flagged too fast.

## Fixed

- A shot could classify "too fast" even though it visibly finished inside the shot chart's own shaded healthy time window. Classification was driven by time-to-90%-of-target instead of the actual completion time the chart itself shows - a shot with a slower late-stage pour could cross 90% just barely too early while its real completion landed safely inside the healthy window. Classification now prefers a shot's real completion time when it reaches one, only estimating from the 90% mark for shots that never reach their target.
- Double-checked this fix against James Hoffmann's own published dial-in results before changing anything else - the existing too-fast/too-restrictive boundaries didn't need to move, they were already right for this corrected measurement.

## Upgrade

No manual steps required. Update via HACS and restart Home Assistant as usual - no entities were renamed or removed in this release.

## Testing

- One real shot fixture now correctly classifies healthy (the exact shot that prompted this fix); three others now correctly classify too_restrictive instead of healthy, a more accurate read on shots that were undershoots with a slow finish.
- Full suite: 335 tests, all passing (up from 331).
