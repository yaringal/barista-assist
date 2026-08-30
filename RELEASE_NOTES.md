# Barista Assist v0.2.11

A fix for a real overshoot seen on a live shot, plus a clarified safety note on shot-duration limits - please read the "Important" section below even if you skip the rest.

## Important: how the machine's own safety cutoff actually works

Barista Assist always drives the 1-CUP/2-CUP button by holding it down (to program pre-infusion), which Breville's own instruction books document as a distinct "manual" mode from a plain single tap. The machine still has its own cutoff in this mode - live testing confirms it - but it appears to be based on total pumped *volume* (via an internal flow meter), not a fixed time: a water-only test shot with no coffee stopped itself after ~30 seconds. Whether that's a fixed generic safety timer or tied to whatever volume is currently programmed for that button (the manual's own single-tap-mode defaults, 30ml/60ml, are suspiciously close) isn't confirmed - but either way, a real shot through a resistive coffee puck should take meaningfully longer to hit that same cutoff than water did. **Set `machine_max_shot_seconds` from your own measured worst case with a real coffee puck, not a water test or any number quoted here** - this isn't documented publicly with full confidence. Past its own protected safety window, Barista Assist stops pressing the button automatically and logs `manual_stop_required`; treat the machine's cutoff as a last-resort backstop, not a substitute for watching the shot yourself. See the README's "Barista Express shot-duration safety requirement" section for the full explanation.

## Fixed

- **A shot could pour well past its target weight even though the stop command fired at the right moment.** A live shot showed the stop command firing exactly on time, but the pour continuing for several more seconds and landing about 19g over target. The cause: brewing and stopping/aborting are deliberately allowed to proceed somewhat independently (so a fast abort never has to wait behind brew's own slower setup steps), but nothing prevented both from talking to the brew Bot's Bluetooth connection at the same time - and when that happened, the button could end up pressed with the wrong (long) hold duration instead of an instant tap, causing the machine to keep pouring. Brewing and stopping/aborting now always take turns talking to the Bot, closing this race. The final press itself still can't be held up for more than a couple of seconds by this, since it's the one action that has to happen no matter what.

## Added

- **Brew and Abort** now also show as unavailable when the scale isn't connected, alongside the existing checks (no bag selected, shot already in progress). Note Abort can now gray out if the scale drops mid-shot - the physical machine and the Bot's own switch entity always remain usable as a backup.
- **A shot now automatically aborts if the scale disconnects while it's active**, so reconnecting the scale is enough to start a new shot - previously the interrupted shot stayed "active" forever, blocking Brew, until Home Assistant was restarted.

## Testing

- 4 new regression tests covering all three code fixes.
- Full suite: 86 tests, all passing.

## Upgrade

No database migration. A full Home Assistant restart is needed to pick up this release.
