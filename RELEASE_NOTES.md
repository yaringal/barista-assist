# Barista Assist v0.3.9

A real crash fix: pressing Brew (or adding a new bag) could fail outright and leave the dashboard stuck on "Connecting scale".

## Fixed

- Pressing "Brew" (or adding a new bag) could crash with a `float()` error, and leave the dashboard stuck showing "Connecting scale" with no way to brew until the integration was reloaded. Caused by a timeout/choked shot - a normal, if disappointing, real-world outcome - being missing one particular timing value that a shared roast-level lookup assumed was always present. That lookup now skips such shots gracefully instead of crashing.
- As a safety net, any failure between the scale connecting and a shot actually starting no longer leaves the dashboard stuck on "Connecting scale".

## Upgrade

No manual steps required. Update via HACS and restart Home Assistant as usual - no entities were renamed or removed in this release.

## Testing

- Added a direct regression test for the crash, plus an end-to-end test reproducing the exact real sequence that triggered it (add a bag sharing a roast level with an affected shot, then brew).
- Added a regression test confirming a mid-setup brew failure clears the phase instead of leaving it stuck.
- Full suite: 338 tests, all passing (up from 335).
