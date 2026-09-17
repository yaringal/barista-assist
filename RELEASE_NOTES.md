# Barista Assist v0.3.5

A critical fix: the integration failed to load at all after updating to a recent Home Assistant version.

## Fixed

- Home Assistant now ships a newer `bleak-retry-connector` (4.7.1) than the exact version Barista Assist's `manifest.json` pinned (4.6.3), so Home Assistant's requirements installer refused to load the integration at all ("Requirements for barista_assist not found"). The pin has been removed entirely - Barista Assist already depends on Home Assistant's own Bluetooth stack, which guarantees a compatible version is installed, so pinning our own copy was both redundant and the actual cause of the conflict. This is the same failure shape as a previous release's fix (see CHANGELOG), which only bumped the pin rather than removing it - so it recurred the next time Home Assistant moved its own version.

## Upgrade

Update via HACS and restart Home Assistant as usual. If you're currently stuck on a version that won't load, this update should resolve it once installed.

## Testing

- Full suite: 321 tests, all passing. This specific fix has no automated test coverage (it can only really be verified by a live Home Assistant install), so please report back if you still see a load failure after updating.
