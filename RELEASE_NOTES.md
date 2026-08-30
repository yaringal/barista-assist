# Barista Assist v0.2.13

Fixes a regression introduced by v0.2.12's own fix: no new functionality.

## Fixed

- **A SwitchBot Bot connection that just needed a second attempt now failed permanently on the first try.** v0.2.12 fixed a multi-minute stall by removing all retrying of a failed connection attempt - but that overcorrected: some connections are genuinely just transient/marginal rather than truly unavailable, and used to succeed on a second attempt (this is how v0.2.10 behaved, before its own unrelated bug). Now retries the whole connect sequence exactly once - enough to recover a marginal connection, without reintroducing the multi-minute stall a truly unrecoverable one caused. Worst case for a genuinely unrecoverable failure is now about 72 seconds (was ~36s right after v0.2.12, ~108s before it).

## Testing

- 2 new regression tests: a hard connect failure is retried exactly once (not zero, not three-plus), and a connect failure followed by success recovers.
- Full suite: 89 tests, all passing.

## Upgrade

No database migration. A full Home Assistant restart is needed to pick up this release.
