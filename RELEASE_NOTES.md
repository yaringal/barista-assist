# Barista Assist v0.2.22

A live, flow-adaptive automatic stop that learns from your own machine's shots instead of a hand-tuned constant, plus a new Shots view for reviewing and cleaning up past brews.

## Added

- **A new Shots view lists every stored shot**, most recent first - click one to see its full recipe/result details and a weight/flow graph, or delete it (with confirmation). Deleting a shot updates that bag's estimated remaining beans.
- **Automatic stop now adapts to how fast a shot is actually pouring.** It still stops at `target yield - margin`, but the margin is now the larger of your calibrated minimum or a live projection from the shot's own current flow rate, capped at a separately-tunable maximum - so a fast-running shot gets stopped earlier (in grams-so-far) instead of overshooting the way a flat margin could. A real shot had overshot from 36g to 47.9g this way before this change. The projection's underlying stop-latency estimate is learned from your own shots (split into two buckets, for normal and fast-flowing shots, since a shot's own flow rate turns out to predict almost perfectly how much extra latency it needs), not a fixed number - see the README's "Adaptive stop margin" section for the full design.

## Changed

- **"Stop compensation" is now two settings: "Minimum early stop margin" and "Maximum early stop margin"**, both on the System view's "Connection and control" card. The maximum used to be a fixed 3x multiple of the minimum; it's now independently tunable. Your existing calibrated value carries over automatically under the new name.

## Docs

- Updated the README's "Adaptive stop margin" section and DESIGN.md for the two-bucket latency model and the independent minimum/maximum settings.

## Testing

- Full suite: 170 tests, all passing (up from 141).

## Upgrade

Renaming the stop-compensation setting creates its Home Assistant entity fresh; your calibrated value carries over automatically (a legacy-key fallback reads the old stored value), but the old `number.barista_assist_stop_compensation` entity is left behind, unavailable, in the entity registry - safe to delete once you've confirmed the new "Minimum early stop margin" entity shows the right value. A full Home Assistant restart is needed to pick up this release.
