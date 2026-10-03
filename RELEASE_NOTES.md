# Barista Assist v0.3.12

Three real fixes from live use: a more precise actuator-delay correction, a notification that wasn't dismissing itself, and a display bug in the flavor-recommendation card.

## Fixed

- The actuator-delay correction from 0.3.11 was still about a second too long - a direct physical timing measurement (the brew Bot's own button release, timed at 8.3s, against the dashboard showing 9.3s) pinned down a more precise correction. This also fully resolves the one shot classification that still disagreed with its own taste call - it's now a clean match, no disagreement left.
- Tapping a flavor-feedback notification button recorded your answer but left the notification sitting there looking unanswered - it's now properly dismissed after tapping.
- The "Future shot recommendation" card could show a raw field name instead of a readable one (e.g. "target_yield_g" instead of "Target yield").

## Upgrade

No manual steps required. Update via HACS and restart Home Assistant as usual - no entities were renamed or removed in this release.

## Testing

- Added three more real shot fixtures from live reports.
- Full suite: 349 tests, all passing (up from 340).
