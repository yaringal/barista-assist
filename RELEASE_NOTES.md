# Barista Assist v0.2.23

Dashboard polish and fixes following up on last release's adaptive stop margin and Shots view: a frozen live-shot graph that no longer disappears, a cleaner "Yield Prediction" settings section, more detail in shot history, and an automatic fix for the orphaned entity 0.2.22 left behind.

## Added

- **A new "Yield Prediction" section** on the System view's "Connection and control" card groups Minimum/Maximum early stop margin together with two new read-only sensors showing the learned stop-latency estimates for normal- and fast-flowing shots.
- **Shot-history entries now show Total duration, Effective stop margin, and Roaster.** Total duration measures from the first brew press to the stop press. Effective stop margin is the actual live-projected margin used at that shot's own stop decision (can be higher than your configured minimum for a fast pour), replacing the old flat "Stop compensation" value.

## Changed

- **"Machine pre-infusion" is now always visible on the dashboard**, not just while Adapt PI is off, so you can set it up in advance.

## Fixed

- **The Live shot graph's frozen last-shot view used to scroll out of sight and disappear after about a minute.** It's now kept in view indefinitely.
- **The old `number.barista_assist_stop_compensation` entity, orphaned by last release's rename, is now automatically migrated to the new "Minimum early stop margin" entity on startup** - no manual cleanup needed (supersedes last release's "safe to delete" note).
- The Shots view's "Started" column no longer overflows and hides the time.
- The Minimum/Maximum early stop margin entity names no longer overflow their dashboard row.

## Testing

- Full suite: 178 tests, all passing (up from 170).

## Upgrade

Includes a database migration (adds an `effective_stop_margin_g` column to the shots table) and an entity-registry migration (remaps the old stop-compensation entity) - both applied automatically on first load. A full Home Assistant restart is needed to pick up this release.
