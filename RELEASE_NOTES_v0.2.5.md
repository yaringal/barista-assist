# Barista Assist v0.2.5

Every shot now gets a flow-curve diagnosis: `healthy`, `too_fast`, `too_restrictive`, `puck_prep_issue`, or `invalid_measurement`, plus a channeling-suspicion score, shown as two new tiles on the Brew view. This is the first real, user-visible piece of the flow-diagnostics work described in `docs/DESIGN.md`.

## Added

- A new `flow_analysis.py` module implementing Stage-1 shot-flow classification (`healthy` / `too_fast` / `too_restrictive` / `puck_prep_issue` / `invalid_measurement`) and a channeling-suspicion score. `puck_prep_issue` is judged primarily against fixed mechanical priors (flow shouldn't accelerate upward mid/late shot under roughly constant pump pressure), so it works from a bag's very first shot, and takes priority over the fast/slow duration check — a shot that's both fast and shows a channeling signature is treated as a puck-prep problem, not a grind-speed one. A per-bag baseline, once enough history exists, can only ever raise that suspicion score, never lower it, so a recurring problem can't normalize itself out of detection. The expected flow rate used for the fast/slow check is handled differently: it's a true Bayesian shrinkage estimate that blends toward a bag's own healthy-shot history and is allowed to fully self-normalize, since a bag genuinely running faster or slower than average isn't a problem to guard against. A shot is also flagged `invalid_measurement` when its first detected flow is implausible — either never detected despite a real final weight, or detected well before the configured pre-infusion should have ended. The too-fast/too-restrictive duration thresholds are anchored against `docs/DESIGN.md`'s own worked example of a "clearly fast" shot rather than picked arbitrarily, though they remain a single calibration point, not derived data.
- `storage.py` can now persist a shot's flow-analysis result (new `classification`, `channeling_suspicion`, and `analysis_json` columns on `shots`, schema v3) and compute a bag's baseline from its recent healthy shots via `recent_healthy_features(bag_id)`.
- `runtime.py` now actually calls `flow_analysis.analyze_shot` when a shot finalizes, passing that bag's own healthy-shot history as the baseline, and stores the result. Every completed, aborted, or errored shot is genuinely classified from here on.
- Two new sensors, `shot_classification` and `shot_channeling_suspicion`, added as tiles on the Brew view next to Last yield. `puck_prep_issue` displays as "Puck prep issue," not the raw stored value.
- The **Copy all shot data** export now includes each shot's `classification`, `channeling_suspicion`, and full analysis feature set, so exported traces carry their diagnosis.
- The diagnosis now ignores the cup or scale being lifted, bumped, or moved anywhere during the pour: real weight can only rise while coffee is being collected, so any drop is treated as interference and everything after it is discarded before classifying. You don't need to wait for any particular moment before touching the cup.
- When a shot can't be classified, the log now says why (too few samples, near-zero final weight, no detected flow, flow starting implausibly early, or a disturbance leaving too little data) instead of just "invalid_measurement" — useful for telling a BLE dropout apart from an interrupted pour.

## Changed

- Internal reorganization of `runtime.py` into clearly labeled sections and removal of several duplicated code paths (background-task cancellation, stop/abort press-and-raise handling, entity-value source dispatch, recipe-field validation). No behavior change.

## Fixed

- The README's "Not implemented yet" list still claimed dynamic SwitchBot long-press programming was missing; it actually shipped back in v0.2.1. Removed the stale entry.

## Docs

- `docs/DESIGN.md` now marks which implementation phases are actually done, records the new flow-analysis module's real status, and adds "Phase 3b — Data-driven thresholds" as a named follow-up for once enough real shot history exists to replace the placeholder constants.
- Documented why a late abort waits for the machine's own timer before finalizing the shot log instead of stopping immediately.

## Testing

- 17 tests for the flow-analysis classifier (`tests/test_flow_analysis.py`), including a shot with an obvious end-of-pour cup-removal signature classifying identically to the same shot without it, and a disturbance that leaves too little trustworthy data getting its own distinct invalid reason.
- 5 tests for the storage layer (`tests/test_storage.py`).
- 3 end-to-end runtime tests (`tests/test_runtime.py`): a real brewed-and-finalized shot gets classified and persisted with a coherent result, a shot too sparse to classify is excluded from the next baseline query, and an invalid shot's reason actually reaches the log.
- Total suite: 37 → 62 tests, all passing.

## Upgrade

The database migrates automatically to schema v3 on first load (three new nullable columns on `shots`; nothing existing is touched or backfilled, and past shots simply show no diagnosis). Nothing about existing shot control, bag tracking, or dashboard layout changes beyond the two new tiles. The new sensors will read "Unknown" until your next shot. The duration/suspicion thresholds behind the new diagnosis are still placeholder constants (see `docs/DESIGN.md`'s Phase 3b), so treat `puck_prep_issue`/`too_fast`/`too_restrictive` calls as a first read, not gospel, until they've been checked against more real shots.
