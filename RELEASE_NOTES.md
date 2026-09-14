# Barista Assist v0.3.0

Barista Assist now closes the loop from "here's what happened" to "here's what to change": automatic grind-correction recommendations, taste-feedback push notifications that suggest recipe tweaks, and roast-level-aware starting points for new bags - alongside a real pre-infusion-accounting bug fix that was quietly skewing shot classification.

## Added

- **Automatic grind-correction recommendations.** Every shot's flow classification now maps to a discrete DF54 grind adjustment (from -2 to +2), with a smaller "just move it fractionally" step recommended after an overshoot instead of a full-sized correction back.
- **Taste-feedback push notifications.** After a healthy shot, two optional 3-button notifications ask how it tasted (extraction: Sour/Sharp, Bitter/Harsh, Balanced; mouthfeel: Thin/Weak, Dry/Astringent, Balanced) and turn the answer into a recipe recommendation - a first report acts immediately, a same-lever overshoot gets a halved step, and a stalled lever escalates to a different one. Opt in via a new "Taste-feedback notification target" option in the integration's settings (Settings → Devices & Services → Barista Assist → Configure). Grind and flavor recommendations never compete: flavor suggestions are suppressed while a bag's grind still needs correcting.
- **Roast-level-aware new-bag defaults.** A new "roast level" field on the new-bag form seeds starting yield ratio, dose, and temperature offset for a bag with no similar coffee to warm-start from, and the flow classifier's own "too fast/too slow" expectation now adapts per roast level too, as your own shot history for that roast level accumulates.
- **A recurring puck-prep problem on the same recipe now gets an actual grind-coarsening suggestion** after 2 unresolved shots in a row (dashboard-editable), instead of an indefinite "just repeat it" response.
- **Shot charts now show a dashed "expected" weight line, a shaded pre-infusion band, and a stop-command marker**, so you can see how a real pour diverged from what was expected at a glance.
- All grind-correction band thresholds/steps and the puck-prep streak settings are now dashboard-editable (System view → "Connection and control" → "Grind correction").

## Changed

- **Fixed a real bug in shot-duration expectation: pre-infusion time was silently missing from the "expected" side of the too-fast/too-restrictive check**, systematically making every shot look slower than it should. This required recalibrating the expected flow-rate constant (1.3 → 1.7 g/s) to match the corrected formula - re-derived from the same published dial-in data the original number came from. The same issue existed in the roast-level flow-rate baseline and has been fixed there too.
- Flavor-taste recommendations now stay valid until the recipe actually changes, instead of resetting on every new shot regardless of whether anything changed.
- Internal code reorganization only (no behavior change): `runtime.py` was split into several smaller modules, `definitions.yaml` was split to pull out Home Assistant entity-wiring metadata into its own `entities.yaml`, and some now-unreachable very-old-version upgrade-migration code was removed.

## Fixed

- Switching bean slots (or just viewing the other slot) could show a grind recommendation computed from the *other* bag's last shot instead of its own.
- The Live Shot card's frozen graph of the last completed shot went blank after a Home Assistant restart.
- A rare race could let a scheduled automatic stop fire against a different, newer shot than the one it was meant for.

## Upgrade

No manual steps required. Update via HACS and restart Home Assistant as usual - no entities were renamed or removed in this release, and the packaged dashboard regenerates itself on reload, so any new cards/settings appear automatically. If you'd like taste-feedback push notifications, opt in from the integration's Configure options after upgrading (off by default).

## Testing

- Full suite: 311 tests, all passing (up from 178).
