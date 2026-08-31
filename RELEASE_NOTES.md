# Barista Assist v0.2.21

A real bug fix (Adapt PI's on/off meaning was backwards), a smarter shot classifier, and brew-safety settings consolidated onto the dashboard.

## Changed

- **Adapt PI's on/off meaning was backwards and has been corrected.** Enabled (the default) now means the app holds the button for your bag's own Pre-infusion setting, exactly as its name always implied; disabled means the machine's own built-in pre-infusion runs instead. This is the reverse of the previous release's behavior - a real hardware-affecting bug, not just a naming change - so double-check which mode you want after upgrading.
- **The machine's own pre-infusion duration is no longer assumed to be a fixed 8 seconds.** With Adapt PI off, a new "Machine pre-infusion" setting (shown on the dashboard) lets you tell Barista Assist what your machine is actually programmed with.
- **Shot records now log the pre-infusion duration that actually ran**, not always the bag's recipe value - "Copy all shot data" reflects the true number either way.
- **"Barista Express programmed maximum shot duration" and "Stop safety margin" moved from Settings to the dashboard**, next to Stop compensation. Your existing values carry over automatically; Settings now only asks you to confirm the machine limit.

## Fixed

- Three shot-classification bugs, each traced back to a real recorded shot that came back with an obviously wrong result: a healthy shot flagged invalid because of one noisy scale reading, a normal shot flagged invalid because of a stale leftover scale packet, and a fast, splashy shot flagged as too-restrictive because a single scale bounce truncated the data before the pour's real outcome was recorded. All three now classify correctly.

## Docs

- Corrected the README's Adapt PI section for the fix above.
- Noted a real overshoot (36g target, 47.9g actual) as the motivation for a future dynamic stop-time improvement - not implemented yet, just documented.

## Upgrade

Includes a database migration (adds an `adapt_pi` column to the shots table) - applied automatically on first load. A full Home Assistant restart is needed to pick up this release.
