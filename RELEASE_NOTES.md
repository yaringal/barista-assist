# Barista Assist v0.3.2

A calibration fix-up from two newly-logged real shots: a false "invalid" classification is fixed, and the automatic stop's margin projection is measurably (not perfectly) improved for fast-flowing shots.

## Fixed

- A shot with a brief noise wobble right after pressing brew could be wrongly marked invalid even though pre-infusion was genuinely honored - fixed by requiring a slightly longer sustained crossing before counting it as real flow.
- Fast-flowing shots near the top of the "normal" flow range were projecting too much stop margin and undershooting target by a few grams. The seed latency for that range has been lowered based on real shot data - noticeably better, though not a complete fix (see CHANGELOG for the full numbers and what's still open).

## Upgrade

No manual steps required. Update via HACS and restart Home Assistant as usual - no entities were renamed or removed in this release.

## Testing

- Full suite: 318 tests, all passing (up from 316).
