# Smart Barista Assist for Barista Express + DF54

**Status:** Design reference / V1 specification  
**Date:** 16 August 2026  
**Primary goal:** Build a non-invasive, Home Assistant–centred espresso assistant that automates shot control and logging, diagnoses extraction problems from scale-derived flow data, and recommends barista-style corrections to grind, yield, dose, temperature, and pre-infusion.

---

## 1. Project goals

The system should:

- Work with a **Breville/Sage Barista Express** without opening or electrically modifying the machine.
- Work with a **DF54 grinder** without motorising the adjustment collar.
- Use a **BOOKOO Themis Ultra Coffee Scale** as the primary measurement device.
- Use **Home Assistant (HA)** as the central controller, logger, and user interface.
- Keep the existing **SwitchBot Bot on the machine power button** for preheating.
- Add a second **SwitchBot Bot on the brew button** to control:
  - pre-infusion duration,
  - transition to full pump pressure,
  - automatic shot stop at target yield.
- Track **two active bags at once**, typically:
  - normal coffee,
  - decaf.
- Track each **physical bag separately**, including roast/open date and current dial-in state.
- Treat the **DF54 grind setting as discrete**, normally in whole-number steps and at most 0.5-unit increments.
- Log environmental context such as **relative humidity and ambient temperature**.
- Detect abnormal or suspicious shots from the **weight/flow curve** before making recipe changes.
- Recommend corrections in a structured, “expert barista” order rather than blindly optimising all parameters.
- Keep **Bayesian optimisation / taste preference optimisation out of V1**.

The guiding design principle is:

> **Diagnose first, correct second, optimise taste later.**

---

## 2. Non-goals for V1

The first version should deliberately avoid:

- opening or modifying the espresso machine internally;
- motorising the DF54 adjustment collar;
- adding a camera to read the pressure gauge;
- relying on smart-plug power monitoring for shot timing;
- using an accelerometer to infer pump timing;
- blindly feeding all variables into Bayesian optimisation;
- treating a suspicious flow curve as definitive proof of channeling;
- building a custom load-cell scale before the higher-level workflow works reliably.

---

## 3. Hardware architecture

### 3.1 Core hardware

```text
Raspberry Pi / Home Assistant
│
├── Bluetooth ── BOOKOO Themis Ultra
│                 ├── weight
│                 ├── timer
│                 ├── battery
│                 └── raw/derived flow data
│
├── Bluetooth ── SwitchBot POWER
│                 └── machine preheat / power
│
├── Bluetooth ── SwitchBot BREW
│                 ├── shot start
│                 ├── pre-infusion hold
│                 ├── release to full pressure
│                 └── automatic stop
│
└── Optional ESP32 + SHT45
                  ├── relative humidity
                  └── ambient temperature
```

Because the espresso machine is only about 2 m from the HA Raspberry Pi, a Bluetooth proxy is **not required by default**.

An ESP32 is still useful as a local environmental sensor. Bluetooth proxy functionality can be enabled later if the Pi's BLE link proves unreliable.

---

## 4. Why no accelerometer is needed

An accelerometer was originally considered as a non-invasive way to infer pump start.

It is unnecessary once the **brew SwitchBot becomes the actuator**.

If HA issues the brew command, HA already knows:

- when the shot was requested;
- how long pre-infusion was commanded;
- when the SwitchBot releases the button;
- when the stop command was sent.

This command timeline is cleaner and more deterministic than estimating pump state from vibration.

The shot timeline can therefore be represented as:

```text
t0                 HA commands brew SwitchBot
│
│  low-pressure pre-infusion
│
t0 + PI             SwitchBot releases brew button
│                   full-pressure extraction begins
│
t_first_flow        scale detects liquid entering cup
│
│                   extraction proceeds
│
t_stop_cmd          predictive controller commands stop
│
t_final             beverage mass settles
```

---

## 5. Shot-start workflow

The preferred interaction is:

1. User prepares the puck and places the cup on the scale.
2. HA confirms the selected active bag and current recipe.
3. User explicitly authorises the shot from:
   - HA dashboard,
   - phone,
   - watch,
   - a physical button,
   - or another simple trigger.
4. HA records the start timestamp.
5. HA commands the brew SwitchBot to press-and-hold the brew button.
6. The SwitchBot holds for the recipe's configured pre-infusion duration.
7. It releases, causing the machine to transition to full extraction pressure.
8. The BOOKOO scale records the beverage mass curve.
9. The controller predicts post-stop overshoot.
10. HA commands the brew SwitchBot to press the button again.
11. Final beverage mass is recorded once the scale settles.

This preserves the user's role as the person who authorises the shot while allowing the system to control timing precisely.

---

## 6. Controllable recipe variables

A recipe should contain:

```yaml
dose_g: 18.0
grind: 15
yield_g: 38.0
temperature_offset_c: 1
preinfusion_s: 7
```

### 6.1 Dose

Dose is adjustable, but only within a deliberately narrow working range.

Example initial operating range:

```text
17.6 g to 18.4 g
```

Typical increment:

```text
0.5 g
```

(Per James Hoffmann's "Understanding Espresso: Dose" video — a ~0.5g nudge
used only on a shot that's already close to good, not for gross corrections;
see `docs/data/DIAL_IN_RULES.md`.)

Dose is a secondary control because changing it also changes:

- puck depth,
- puck resistance,
- headspace,
- effective brew ratio.

It should not be the first variable changed when a shot simply runs too fast or too slow.

---

### 6.2 DF54 grind

The grinder is **discrete**, not continuous.

Allowed values:

```text
... 13, 13.5, 14, 14.5, 15, 15.5 ...
```

Normal recommendations should prefer whole-number changes.

Half-steps are fine adjustments.

Example policy (mirrored for the coarser/restrictive side; see
`definitions.yaml`'s `expert_rules.grind_correction` for the full table with
`duration_ratio` thresholds, and `docs/data/DIAL_IN_RULES.md` for
sourcing):

```text
grossly fast            -> 16 to 14
moderately fast         -> 16 to 15
slightly fast           -> 16 to 15.5
healthy                 -> no change
slightly restrictive    -> 15.5 to 15
moderately restrictive  -> 15 to 14
grossly restrictive     -> 14 to 12
```

Over time, the system should estimate the local response of the grinder, e.g.:

```text
1 DF54 unit finer ≈ +X seconds / lower flow around this recipe
```

This is **system identification**, not taste optimisation.

---

### 6.3 Yield

Yield is one of the main flavour and strength controls.

Typical increment:

```text
5 to 10 g for a sourness correction
2 to 3 g for general fine-tuning
```

James Hoffmann's "Understanding Espresso: Ratio" video states 2-3g as his
own ceiling before a yield tweak starts changing the style of the drink
rather than just fine-tuning it — good general guidance, but when
cross-checked against Lance Hedrick's and Matt Perger's own worked
sourness-correction examples (5-10g, 25% jumps), all three independently
use noticeably bigger increments than Hoffmann's stated ceiling for that
*specific* fix. See `docs/data/DIAL_IN_RULES.md` and
`docs/data/CROSS_CREATOR_RULE_CHECK.md`.

Yield should generally be adjusted only after the shot appears hydraulically healthy.

---

### 6.4 Brew temperature

The Barista Express exposes discrete brew-temperature offsets around its default:

```text
-2 °C
-1 °C
 0 °C
+1 °C
+2 °C
```

Temperature should be treated as a **small categorical variable**, not a freely continuous one.

For V1, temperature changes can remain manual:

```text
HA:
"Current bag requires +1 °C.
Machine currently set to 0 °C.
Change temperature before brewing."
```

If switching between normal and decaf makes this tedious, external actuation can be considered later.

No internal machine modification is required.

---

### 6.5 Pre-infusion

Pre-infusion becomes part of the recipe:

```text
PI = 7 s
```

The brew SwitchBot provides a known timing source.

V0.2.1 programs the SwitchBot Bot stored long-press duration per bag immediately before each shot. The Bot then performs the physical long press when the existing Home Assistant SwitchBot action is triggered.

Typical increment:

```text
1 second
```

**Adapt PI** (`switch.barista_assist_adapt_pi`, a controller-level toggle alongside `early_stop_margin_min_g`/`early_stop_margin_max_g` - see `BaristaRuntime.adapt_pi`, persisted in the runtime's own `Store`, not a config-entry option) selects whether Barista Assist itself adapts pre-infusion per bag, or leaves pre-infusion to the machine's own built-in default. When `adapt_pi` is on (the default), `async_brew` holds the button for the active bag's `preinfusion_s` as described above - `_async_prepare_brew_bot` programs that duration before `_async_press_brew_bot` holds it. When `adapt_pi` is off, the Bot is single-tapped instead: `_async_prepare_brew_bot` is skipped entirely for the start press, and the Barista Express runs its own built-in pre-infusion - a duration Barista Assist can't observe or control, so `BaristaRuntime.machine_pi_s` (a plain, user-editable controller setting like `early_stop_margin_min_g`, not a hardcoded constant) records what the barista has measured that duration to actually be, purely so shots are logged with the true value used. Either way the stop press itself goes through `_async_press_brew_bot` (Home Assistant's own switchbot integration); the direct-BLE `SwitchBotBotConfigurator` path (`_async_prepare_brew_bot`) is only ever used to program a hold duration, so it's skipped for the start press when `adapt_pi` is off, though it's still used afterward to reprogram the Bot back down to an instant tap ahead of the stop press when `adapt_pi` is on. The per-bag Pre-infusion tile is hidden from the dashboard when `adapt_pi` is off (replaced by a Machine pre-infusion tile) via plain `visibility`/`conditional` conditions on the packaged dashboard YAML (checking the switch's own state), not any server-side regeneration - the tiles swap purely client-side as soon as the switch changes.

---

## 7. Automatic brew-by-weight control

The target is not to stop exactly at the target mass shown on the scale.

There is a delay between:

- issuing the stop command,
- the SwitchBot physically pressing the button,
- the machine stopping the pump,
- residual liquid reaching the cup.

Therefore the controller should learn a **tail / overshoot model**.

Define:

- `w(t)` = current cup mass;
- `q(t)` = current flow rate;
- `L` = command and machine latency;
- `tail_hat` = predicted mass that will arrive after the stop command;
- `Y_target` = desired final beverage mass.

Stop when:

```text
w(t) + tail_hat(q, dq/dt, L, recent_shots) >= Y_target
```

After the shot:

```text
error = final_yield - target_yield
```

The tail model is updated slightly from the error.

This is a deterministic/adaptive controller and does **not** require Bayesian optimisation.

**Implemented, as a simplified version of the formula above** (`BaristaRuntime._effective_stop_margin_g`/`_smoothed_flow_g_s`): `tail_hat` is approximated as `q(t) * L`, where `q(t)` is `flow_g_s` averaged over the trailing 500ms of samples (smoothing single-reading noise, matching `flow_analysis_constants.smoothing_window_ms`) and `L` (`BaristaRuntime.stop_latency_normal_s` or `stop_latency_elevated_s` - see below for why there are two) is a persisted, per-installation estimate - not a fixed code constant, and not derived from `early_stop_margin_min_g` or any other setting (see below for why). The margin actually used is `min(early_stop_margin_max_g, max(early_stop_margin_min_g, q(t) * L))`: `early_stop_margin_min_g` is a floor, never reduced below what the user already calibrated, and `early_stop_margin_max_g` is an independent, separately-tunable ceiling - not a multiple of the floor, so raising one setting doesn't silently change how the other behaves. The live projection only ever raises the margin above the floor, up to that ceiling. This one-sided design was chosen after an earlier version - which instead derived `L` as `early_stop_margin_min_g / flow_analysis_constants.expected_flow_g_s` and let the projection replace the flat margin entirely, scaling it down at low flow too - was checked against a real recorded shot (`tests/fixtures/real_shots/good_shot_adapt_pi.txt`, `early_stop_margin_min_g=8.0`) and found to imply a physically impossible ~6.4s stop latency, regressing that shot's stop point by 6g/17% early. The floor-based version is provably safe regardless of how well-tuned `L` is: it can only add margin on top of an already-correct flat stop, never subtract from it.

`L` is learned, not fit once from history (`BaristaRuntime._update_learned_stop_latency`, called from `_async_finalize` for every shot that completes via the automatic target-weight stop and is flow-classifiable, regardless of its classification - see below), and it isn't a single value either. A first cut used one shared `stop_latency_s`: a fixed value backed out from two real "healthy" shots - `(final_weight - weight_at_the_stop_decision) / flow_g_s_at_that_same_decision` gives 3.26s and 3.56s for those two - did reproduce both of *their* real stop points exactly, but that's a weaker validation than it looks: at their actual flow rates, any `L` up to ~3.4-3.9s keeps the projected margin under the `early_stop_margin_min_g=8.0` floor for both shots regardless of value, so matching them doesn't distinguish between candidate `L`s the way it appears to - only checking whether a candidate `L` stays under the floor for shots that don't need the adaptive term at all, which is guaranteed by construction. Learning `stop_latency_s` from every completed shot (not just healthy ones) then risked a different problem: a fast/channeling shot's own flow doesn't hold roughly constant through the latency window the model assumes, and averaging its "observed latency" into the same shared estimate as normal shots could drag it past the point where the floor still protects an ordinary shot - checked against real data, a shot's own flow rate at the decision moment predicts almost perfectly how much extra latency it needs (correlation 0.97 between flow-at-decision and tail grams across 5 real shots). So instead of one shared value (or excluding shots by classification, which papers over the same problem instead of modeling it), there are two: `stop_latency_normal_s` for shots flowing below `_STOP_LATENCY_BUCKET_CUTOFF_G_S` (3.0 g/s) at the decision moment, `stop_latency_elevated_s` for shots at or above it - a shot flowing fast isn't a different *kind* of shot to be excluded, it's a normal point on a continuum that just needs a bigger latency estimate. Predictive, flow-rate-based stop-by-weight is an established technique (La Marzocco/Acaia's Connected Scale, the open-source Gaggiuino project), and per-installation online calibration - updating a running estimate a small step at a time from each real outcome, rather than batch-fitting historical data - is the standard way these systems are actually tuned. So: `stop_latency_normal_s`/`stop_latency_elevated_s` seed at 3.4s/4.3s (`defaults.controller` in `definitions.yaml` - themselves the average observed latency of the real shots on each side of the cutoff, reasonable starting points rather than uniquely correct ones) and after each usable shot, whichever bucket that shot's own flow-at-decision falls into moves toward that shot's own observed latency by `_STOP_LATENCY_LEARNING_RATE` (0.15, a slow step so no single anomalous shot swings it), clamped to `[_STOP_LATENCY_MIN_S, _STOP_LATENCY_MAX_S]` (0.5s-8s) and skipped entirely when flow at the decision was too slow to divide by meaningfully (`_MIN_FLOW_FOR_LATENCY_LEARNING_G_S`). Both are persisted (`BaristaRuntime._async_save_state`) and exposed read-only as `status` sensor attributes for visibility, not as editable dashboard entities - they're meant to correct themselves, not be hand-tuned. A continuous flow->latency regression (fitting a slope instead of two buckets) would be a more principled destination once there's enough real data across the flow range to trust a fitted slope rather than the handful of points available today.

A naive attempt at the `dq/dt` (flow-acceleration) term was tried and rejected before this: extending the margin formula to `q(t) * L + 0.5 * a(t) * L^2` (`a(t)` a linear-regression slope of flow over a trailing window, mirroring `mid_accel`/`late_accel` below) was meant to catch the specific overshoot shot that motivated this whole feature (36g target -> 47.9g actual), whose flow was still accelerating *during* the stop-latency window itself, after the current-flow-only projection's decision point. Checked against real shot data, this instead badly regressed both real healthy shots (triggering 4-6.5g early) - a normal shot's early flow ramp-up has a perfectly ordinary positive acceleration by construction, and squaring `L` amplifies even that into a large phantom margin; `mid_accel`/`late_accel` avoid this in the post-shot classifier by only computing acceleration over the *mid/late* portion of an already-finished shot and using it as a threshold gate, not a continuous multiplier, which a live, continuously-re-evaluated projection can't cleanly replicate without more real data to gate on than currently exists. Deliberately not implemented for now, and not likely to be revisited by hand-tuning a second parameter against the same small dataset that already couldn't reliably pin down the first one; needs either meaningfully more real shot history or a fundamentally different (e.g. threshold-gated, progress-gated) shape, not just a bigger sample of the same fit.

Sample collection previously only ran for a fixed 3.0s after the stop press before finalizing - one of the two real shots used above was still measurably gaining weight when that window ran out. It now runs for `max(stop_latency_normal_s, stop_latency_elevated_s) + _SETTLE_BUFFER_S` (floor `_MIN_SETTLE_S`, currently working out to ~6.3s at the seed values, and adapting as either learned latency does) - both to stop understating `actual_yield_g` on a slow-tailing shot and so every completed shot captures enough of its own tail to be a usable observation for the learning step above (for whichever bucket it belongs to), rather than being right-censored.

---

## 8. Scale data

The BOOKOO Themis Ultra should be treated as the primary sensor.

Prefer to save the **rawest available weight stream** rather than relying only on the scale's displayed flow value.

From the mass trace:

```text
m(t)
```

derive a smoothed flow estimate:

```text
q(t) = dm/dt
```

The implementation should preserve:

- original timestamped mass samples;
- filtered mass samples;
- derived flow;
- filtering parameters used.

This allows future re-analysis without needing to repeat the shot.

---

## 9. Shot features

Each shot should generate a compact feature set in addition to retaining the full trace.

Useful features include:

### Timing

```text
t_start
t_preinfusion_end
t_first_flow
t_10
t_50
t_90
t_stop_command
t_final
```

where `t_10`, `t_50`, and `t_90` are the times at which the beverage reaches 10%, 50%, and 90% of target yield.

### Flow

```text
early median flow
mid-shot median flow
late median flow
maximum flow
flow slope
flow curvature
flow variance
mid-shot acceleration
late-shot acceleration
```

### Outcome

```text
actual dose
target yield
final yield
yield error
shot duration
time to first flow
```

### Context

```text
bag_id
days since roast
days since opening
ambient RH
ambient temperature
DF54 setting
brew temperature
pre-infusion duration
```

---

## 10. Normalising the shot curve

Not all analysis should be performed against clock time.

It is also useful to represent progress by beverage mass:

```text
u = m(t) / Y_target
```

This gives a normalised shot progression from:

```text
0.0 -> 1.0
```

Features can then be compared at equivalent beverage progress rather than only at equivalent elapsed time.

For example:

```text
flow at 20% beverage mass
flow at 50%
flow at 80%
```

This should help compare shots with slightly different total durations.

---

## 11. Channeling and abnormal-flow diagnosis

The scale measures **total beverage flow**, not the spatial flow distribution inside the puck.

Therefore the system must **not claim to directly detect channeling**.

Instead it should produce a:

```text
channeling / non-uniformity suspicion score
```

The key distinction is:

```text
measured total flow:
Q(t) = integral of local flow over puck area

actual channeling:
spatially non-uniform q(x, y, t)
```

Different internal flow patterns can produce similar total cup flow.

Therefore:

> A suspicious mass/flow curve is evidence of abnormal extraction, not proof of channeling.

---

## 12. How to use suspicious shots

A suspicious shot should normally **not trigger a recipe change immediately**.

Example:

```text
Recipe:
18.0 g
DF54 15
38.0 g out
+1 °C
7 s PI

Observed:
- normal start
- normal early flow
- abrupt mid-shot acceleration
- unusually high late flow
- strong deviation from previous good shots

System response:
"Possible non-uniform extraction.
Repeat the same recipe with careful puck preparation.
Do not update the recipe from this shot."
```

Such a shot should also be excluded from updates to:

- grinder-response estimates;
- bag ageing model;
- normal-shot baseline.

This prevents poor puck preparation from contaminating the model.

**Implemented:** this section's "repeat the recipe, don't update it"
response no longer applies identically no matter how many times in a row
it happens for the same recipe. `storage.consecutive_puck_prep_issue_count`
counts how many of a bag's most recent classified shots, in a row, are
`puck_prep_issue` at an unchanged recipe (dose/yield/temperature/
preinfusion *and* grind all equal - a self-tried grind change counts as a
new attempt, not a continuation of the same unresolved problem). Once that
streak reaches `expert_rules.grind_correction.puck_prep_issue_streak_threshold`
(a placeholder, `3`, not sourced - no transcript gives a numeric threshold
for this case, needs real accumulated shot data to validate), an
occasional bad tamp is no longer a plausible explanation - the system
overrides "repeat, don't touch grind" and instead recommends coarsening by
`puck_prep_issue_streak_coarsen_delta` (also a placeholder), surfaced as a
note on the bag's dashboard summary. `runtime.py`'s `_async_finalize`
computes the override; `flow_analysis.py`'s classification itself is
unaffected - the override happens after classification, not by changing
when a shot is called `puck_prep_issue`.

---

## 13. Diagnostic architecture

V1 should use three conceptual stages.

### Stage 1 — Validate the shot

Ask:

- Was time to first flow plausible?
- Was total flow globally too high or too low?
- Did the flow change smoothly?
- Did resistance appear to collapse unexpectedly?
- Is the curve unusually different from accepted shots from this bag?
- Is the scale trace noisy or otherwise unreliable?

Possible classifications:

```text
healthy
globally too fast
globally too restrictive
unstable / suspicious
invalid measurement
```

If the shot is suspicious:

```text
repeat recipe
do not learn from this shot
```

---

### Stage 2 — Correct hydraulics

If the shot is mechanically coherent but globally too fast or too slow:

```text
too fast -> grind finer
too slow -> grind coarser
```

Prefer:

```text
±1 DF54 unit
```

for normal corrections and:

```text
±0.5
```

for fine corrections.

During this phase:

- hold dose constant;
- hold yield constant;
- hold temperature constant;
- hold PI constant unless there is a specific reason to change it.

This isolates the hydraulic correction.

---

### Stage 3 — Round off flavour

Only once the shot is hydraulically credible should the user be asked for sensory feedback.

Possible low-friction tags:

```text
balanced
sharp / sour
bitter / harsh
dry / astringent
thin
weak in milk
too strong
```

The expert system then chooses the most appropriate next lever.

Examples:

```text
healthy flow + slightly sharp
-> try longer yield first (+5 to +10 g), escalate to +1 °C only if it persists across shots

healthy flow + sharp but longer yield makes drink too weak
-> return yield and try +1 °C

healthy flow + bitter or harsh (watery/drying too)
-> try shorter yield first, escalate to -1 °C only if it persists across shots

healthy flow + bitter, but NOT watery/drying
-> do not assume over-extraction - darker roasts especially can taste
   bitter from fines while actually under-extracted (channeling); check
   for a puck-prep/channeling cause before touching yield or temperature

healthy flow + dry/astringent (with thin/watery)
-> try shorter yield first; if the over-extraction traces to a grind
   pushed too fine rather than a ratio problem, back off the grind instead

healthy flow + too intense in milk
-> consider slightly longer yield or small dose reduction

healthy flow + good flavour but too weak
-> consider a small dose increase

suspicious flow + sour and dry together
-> repeat puck prep before changing recipe
```

The bitter/harsh and dry/astringent rows, the sourness-yield magnitude
(+5 to +10 g, up from an earlier +2 to +3 g), and the fines/bitterness
caveat were added after cross-checking against Lance Hedrick's and Matt
Perger's videos alongside James Hoffmann's — see
`docs/data/CROSS_CREATOR_RULE_CHECK.md` and `expert_rules.flavor_correction` in
`definitions.yaml` for the full sourcing and reasoning. Not everything from
that cross-check was incorporated: creator-specific personal preferences
that conflicted with the wider consensus (or, in one case, with a
creator's own other videos) were deliberately left out — see that doc's
"queued decisions" for what was considered and rejected, and why.

This behaviour is intentionally hierarchical.

---

## 14. Expert-system principle

The controller should not ask:

> "Which parameter can I change?"

It should ask:

1. **Was this mechanically a valid shot?**
2. **If not, what hydraulic or preparation issue is most likely?**
3. **If yes, what sensory issue remains?**
4. **Which variable is the most appropriate lever for that issue?**
5. **What is the smallest reproducible adjustment?**

This is the intended “experienced barista” behaviour.

---

## 15. Variable priority

A useful default hierarchy is:

```text
1. Puck validity / repeatability
2. Grind
3. Yield / ratio
4. Temperature
5. Small dose adjustment
6. Pre-infusion refinement
```

This is not an absolute law.

It is a **decision prior** intended to avoid unnecessary multi-variable changes.

---

## 16. Home Assistant bag model

HA should own the concept of a **physical bag**, not just a bean name.

Use three levels:

```text
Coffee
  ↓
Bag
  ↓
Shot
```

### Coffee

Example:

```yaml
coffee_id: ona_raspberry_candy
roaster: ONA
name: Raspberry Candy
type: normal
roast_level: medium
```

### Bag

Example:

```yaml
bag_id: ona_raspberry_20260812_01
coffee_id: ona_raspberry_candy
roast_date: 2026-08-12
opened_date: 2026-08-16
starting_mass_g: 250
active: true
```

### Shot

Example:

```yaml
timestamp: 2026-08-18T08:03:21+01:00
bag_id: ona_raspberry_20260812_01
dose_g: 18.0
grind: 15
temperature_offset_c: 1
preinfusion_s: 7
target_yield_g: 38.0
actual_yield_g: 38.2
shot_class: healthy
```

The full weight and flow trace should be stored separately or referenced from this record.

---

## 17. Two active bean slots

Keep the UI simple.

Have exactly two common active slots:

```text
ACTIVE NORMAL BAG
ACTIVE DECAF BAG
```

Each points to a `bag_id`.

Example:

```text
Normal
  ONA Raspberry Candy
  opened 6 days ago
  estimated 142 g remaining
  current recipe:
    18.0 g
    DF54 15
    +1 °C
    PI 7 s
    38.0 g out

Decaf
  Example Decaf
  opened 10 days ago
  estimated 96 g remaining
  current recipe:
    17.8 g
    DF54 11
    -1 °C
    PI 7 s
    39.0 g out
```

The active bag determines which recipe, history, and model are used.

---

## 18. Starting a new bag

A new bag should be an explicit event.

Suggested HA actions:

```text
New normal bag
New decaf bag
Same coffee, new bag
```

Minimum information:

```text
coffee
roaster          optional
roast date
bag weight       default value allowed
```

HA automatically records:

```text
opened_at = now
new unique bag_id
```

The old bag is archived, not overwritten.

This allows bag-to-bag differences to remain visible.

---

## 19. Warm-starting a new bag

A new bag should not necessarily start from zero knowledge.

### Same coffee previously used

Start from the **early-life recipe** of the previous bag, not its final old-age recipe.

Example:

```text
Previous bag:
day 2  -> DF54 15
day 8  -> DF54 14
day 16 -> DF54 13

New fresh bag:
start near DF54 15
```

### Similar coffee

If there is no exact match:

```text
medium roast normal -> prior from similar normal coffees
```

### No similar coffee — fall back to a roast-level ratio prior

If there's no matching or similar coffee to warm-start from at all, the
bag's own `roast_level` (a flat, optional `bags.roast_level` column — a
single dropdown on the new-bag form, not the separate Coffee entity §16
sketches; nothing else in this codebase implements that three-level
Coffee/Bag/Shot split either, it's all flat bag fields, same as
coffee_name/roaster/roast_date) can still seed a starting ratio — Lance
Hedrick's stated guideline, but checked against and adjusted toward James
Hoffmann's own actual dial-in results, not taken as-is. This *is* now
implemented: `runtime.py`'s `async_new_bag` applies it only when a brand-new
bag is opened in a slot with no existing bag to inherit `target_yield_g`
from (there's still no "similar coffee" warm-start match implemented to
prefer over it — the paragraphs above this one remain unimplemented design,
not built code):

```text
dark   -> around 1:2
medium -> around 1:2.2
light  -> around 1:3 (open-ended - can run longer)
```

Lance's own stated numbers (dark 1:2, **medium 1:2.5**, light 1:3+, from two
of his videos that agree closely with each other) don't hold up against
Hoffmann's own two roast-labelled dial-ins: his medium-roast session ended
at 19g→36-38g, which Hoffmann himself calls "the classic two to one ratio"
(~1:1.9-2.0) — notably closer to Lance's *dark* tier than his medium one.
`medium` above was lowered from 2.5 to 2.2 to move toward that result
without fully collapsing it onto the dark tier (which would lose the
ordering this prior exists to encode) — a single Hoffmann data point isn't
enough to pin an exact number, only enough to say 2.5 was too high. `light`
hasn't been re-checked the same way: Hoffmann's one light-roast result
(1:2.47) is itself below Lance's light tier, but with only one data point
and Lance's own explicit "and above" hedge on that number, there's no clear
correction to make there yet.

Both Lance sources are explicit these are loose starting points, not fixed
targets — one of his own dark-roast dial-ins ended below 1:2 once actually
tasted. Worth weighing this whole prior lightly regardless of the exact
numbers: Hoffmann's own "Episode 0" is an extended argument against exactly
this kind of simplification ("not all two-to-ones are created equal" —
ratio depends on dose, basket, grinder, and water at least as much as roast
level). This is blended toward this installation's own accumulated ratio for
the same `roast_level`, across *other* bags that share it, as that pool
grows (`storage.roast_level_baseline`) — deliberately not toward the current
bag's own accepted shots, which would let bean-aging drift within one bag's
life quietly feed back into its own reference point - the flow classifier's
own expected-flow-rate prior is blended the same roast-level-keyed way, for
the same reason (see Phase 3b's fuller reasoning on that). See
`expert_rules.roast_level_ratio_prior` in
`definitions.yaml` and `docs/data/CROSS_CREATOR_RULE_CHECK.md`.

The same fallback now also seeds `dose_g` (`expert_rules.roast_level_dose_prior`,
sourced from Hoffmann's Dose episode's "the darker the roast... the less
work you need to do to extract it... lighter roasts, go for a lower dose")
and `temperature_offset_c` (`expert_rules.roast_level_temperature_prior`),
from Hoffmann's Temperature episode's own roast-level starting-temperature
ballparks (darker/developed 85-90°C, medium 88-92°C, lighter 90-95°C - "his
own benchmarks, taken loosely from roasters' recommendations", not a precise
rule). Those are
absolute temperatures on his own machine, though, and `temperature_offset_c`
is relative to whatever this installation's machine (the Breville/Sage
Barista Express, this project's target hardware) is already set to. The
Barista Express's own factory-default brew temperature is independently
documented as 93°C/200°F, explicitly described by that documentation as a
"middle-ground that works for most medium roasts" - i.e. the machine's own
default is already medium-oriented, corroborating (not just asserting) this
system's choice to anchor `medium` at `temperature_offset_c`'s existing 0.
See that key's own comment in `definitions.yaml` for how Hoffmann's
*relative* spacing between roast levels (not his absolute degree numbers,
which are a different machine) was mapped onto this system's discrete
offset scale.

### Decaf

Maintain a separate decaf prior.

This is simple transfer of prior experience, not Bayesian taste optimisation.

---

## 20. Bag ageing

For every accepted shot, log:

```text
days since roast
days since opening
grind
dose
yield
temperature
PI
RH
ambient temperature
flow features
```

Over time, HA can estimate user-specific drift such as:

```text
"Medium roast bags in this setup usually require
approximately 1 to 1.5 DF54 units finer between
day 3 and day 15 after opening."
```

Age should be treated as measured context, not as a hard-coded universal rule.

---

## 21. Tracking estimated coffee remaining

Given:

```text
starting bag mass
sum of doses
estimated purge / waste
```

estimate:

```text
remaining = starting_mass - sum(doses) - estimated_waste
```

Example:

```text
250 g start

doses:
18.0
18.1
17.9
18.0

used = 72.0 g
estimated remaining ≈ 178 g before waste correction
```

Because grinder purge and retention are uncertain, this is only an estimate.

The UI can still use it for useful messages such as:

```text
"Normal bag: approximately 2 shots remaining."
```

---

## 22. Environmental sensing

A simple:

```text
ESP32 + SHT45
```

near the grinder can measure:

```text
relative humidity
ambient temperature
```

These values should initially be **logged only**.

Do not begin with a rule such as:

```text
+10% RH -> grind X units finer
```

Instead, wait until the user's own shot history shows whether RH meaningfully predicts changes in:

- flow;
- required grind;
- repeatability.

Environmental variables must earn their place in the correction model.

---

## 23. Suggested data storage

A straightforward initial implementation could use:

### HA entities

For current state:

```text
active_normal_bag
active_decaf_bag
selected_bag
recommended_dose
recommended_grind
recommended_yield
recommended_temperature
recommended_PI
```

### SQLite / PostgreSQL / InfluxDB

For durable history:

```text
coffee
bags
shots
shot_samples
shot_features
diagnostics
recipe_changes
environment
```

Example logical schema:

```text
coffee
------
coffee_id
roaster
name
type
roast_level

bags
----
bag_id
coffee_id
roast_date
opened_date
starting_mass
closed_date

shots
-----
shot_id
bag_id
timestamp
dose
grind
temperature
PI
target_yield
actual_yield
classification
accepted_for_learning

shot_samples
------------
shot_id
time_ms
weight_g
flow_g_s

environment
-----------
timestamp
RH
ambient_temperature
```

---

## 24. Recommended V1 state machine

```text
IDLE
  ↓
BAG_SELECTED
  ↓
WAITING_FOR_CUP
  ↓
READY
  ↓
USER_AUTHORIZES_BREW
  ↓
PREINFUSION
  ↓
FULL_EXTRACTION
  ↓
PREDICTIVE_STOP
  ↓
SETTLING
  ↓
SHOT_ANALYSIS
  ↓
DIAGNOSIS
  ↓
RECOMMENDATION
  ↓
IDLE
```

Error states should include:

```text
scale disconnected
unstable tare
unexpected weight movement
SwitchBot failure
yield overshoot
invalid trace
user abort
```

---

## 25. Example shot decisions

### Example A — clearly fast but coherent

```text
18.0 g in
38.0 g out
DF54 16
PI 7 s
24 s total

Curve:
smooth
globally high flow
no large discontinuity

Decision:
DF54 16 -> 15
Hold all other variables constant.
```

---

### Example B — mechanically healthy but slightly sharp

```text
18.0 g in
38.0 g out
DF54 15
+1 °C
PI 7 s

Curve:
matches accepted baseline

Taste:
slightly sharp

Decision:
try 39.0 to 39.5 g yield
same grind
same temperature
same dose
```

---

### Example C — longer yield fixes acidity but weakens flat white

```text
Previous correction:
38.0 -> 39.5 g

Result:
less sharp
but too weak in milk

Decision:
return yield toward 38.0 g
test +1 °C temperature change
```

---

### Example D — suspicious resistance collapse

```text
18.0 g in
38.0 g out

Curve:
normal first flow
normal early phase
abrupt mid-shot acceleration
late flow far above baseline

Taste:
simultaneously sharp and drying

Decision:
possible non-uniform extraction
repeat exact same recipe
improve puck prep
do not update recipe model
```

---

## 26. Comparison with appliance-style Barista Assist

Commercial systems typically aim to keep the user near a manufacturer-defined “good espresso” region using:

- dose correction;
- grind recommendations;
- time/flow targets;
- bean profiles;
- preset temperature or brew controls.

This project differs in two important ways.

### 1. Explicit diagnostic layer

The system first asks whether the shot was mechanically trustworthy.

Bad or suspicious shots do not automatically cause recipe changes.

### 2. Transparent variable roles

The system uses known variable roles:

```text
grind       -> hydraulic resistance
yield       -> extraction / strength balance
temperature -> fine flavour / extraction adjustment
dose        -> small structural / intensity trim
PI          -> wetting and puck behaviour
```

Rather than treating all controls as equivalent optimisation dimensions.

The intended result is closer to an expert barista's troubleshooting sequence than to a generic appliance auto-dial algorithm.

---

## 27. Why Bayesian optimisation is deliberately postponed

Taste optimisation may eventually be useful, but it is not required to make V1 valuable.

First establish:

- reliable scale integration;
- repeatable SwitchBot shot control;
- predictive yield cutoff;
- per-bag logging;
- discrete DF54 recommendations;
- flow-based shot validation;
- expert correction rules.

Only after these are trustworthy should a taste optimiser be considered.

A future optimiser should work **inside the mechanically healthy region**, not replace the diagnostic system.

---

## 28. Implementation phases

### Phase 1 — Instrumentation ✅ Implemented

Implement:

- BOOKOO BLE connection;
- live weight logging;
- brew SwitchBot control;
- existing power SwitchBot;
- target-yield stop;
- raw trace storage.

Success criterion:

> Repeatably hit target beverage mass and retain a complete shot trace.

---

### Phase 2 — Bag tracking ✅ Implemented

Implement:

- normal and decaf active slots;
- new-bag workflow;
- roast/open dates;
- per-bag recipe;
- estimated remaining mass.

Success criterion:

> Every shot is automatically associated with the correct physical bag.

---

### Phase 3 — Flow analysis ✅ Implemented (thresholds still placeholders, see Phase 3b)

Implement:

- smoothing;
- flow derivative;
- first-flow detection;
- t10/t50/t90;
- early/mid/late flow features;
- baseline comparison.

Success criterion:

> System can distinguish obviously fast, slow, normal, and suspicious traces.

Status: `flow_analysis.py` implements the Stage 1 classifier (`healthy` /
`too_fast` / `too_restrictive` / `puck_prep_issue` / `invalid_measurement`)
and a channeling-suspicion score as a pure, dependency-free module (see its
tests in `tests/test_flow_analysis.py`). `puck_prep_issue` is judged
primarily against fixed mechanical priors (flow should not accelerate
upward mid/late shot under roughly constant pump pressure), so it works
from a bag's very first shot; a per-bag baseline, once enough history
exists, can only ever raise that suspicion score, never lower it, so a
recurring problem can't "normalize" itself out of detection. Mechanical
validity is judged before the fast/slow hydraulic check, not after, per
section 14's ordering ("was this mechanically a valid shot?" comes before
"what hydraulic issue?") — a shot that both finishes fast and shows a
channeling signature is `puck_prep_issue`, not `too_fast`. `storage.py` can
persist a shot's classification/suspicion/full analysis (schema v3:
`classification`, `channeling_suspicion`, `analysis_json` on `shots`) and
`recent_healthy_features(bag_id)` returns the median late-shot acceleration
and flow rate from a bag's recent healthy shots for `analyze_shot`'s
`baseline` argument. `runtime.py._async_finalize` now calls both of these
for every shot, so every shot really is classified and persisted with a
computed baseline. Two `last_shot`-sourced sensors (`shot_classification`,
`shot_channeling_suspicion`) expose this on the Brew view, with
`puck_prep_issue` displaying as "Puck prep issue" via a proper
`state`-translation block rather than the raw enum value. The shot-data
export (`export_shots_text`) now includes `classification`,
`channeling_suspicion`, and the full `analysis_json` in each shot's
metadata block, per section 8's goal of allowing future re-analysis
without repeating the shot.

`analyze_shot` also detects the cup or scale being disturbed (lifted,
bumped, moved) at any point in the trace: raw weight can only rise while
coffee is actually being collected, so a meaningful drop below its own
running peak so far that holds for at least `_DISTURBANCE_SUSTAIN_MS` is
unambiguous interference, not flow - a violent, turbulent gush can bounce a
sample or two below the running peak (droplets, crema settling, the cup
rocking) without any real interference, recovering within a couple hundred
ms and continuing to climb, so only a drop that holds for the full sustain
window (or runs out the rest of the shot without recovering) counts.
Everything from that point on is discarded before classification runs, on
whatever prefix remains - the user never needs to be told when it's "safe"
to touch the cup. This is a more general replacement for an earlier idea of
truncating at the stop command's timestamp: it also catches a bump mid-pour, doesn't
discard genuinely undisturbed settle-tail data, and needs no `runtime.py`
signature changes. Every `invalid_measurement` shot now also carries an
`invalid_reason` (too few samples, near-zero final weight, no detected
flow, flow starting before pre-infusion should have ended, or a disturbance
leaving too little trustworthy data) and `runtime.py` logs it, so an
invalid shot can be diagnosed - e.g. a BLE dropout vs. a disturbed cup -
instead of showing up as an unexplained `invalid_measurement`.

The duration thresholds (the expected total-flow rate and the too-fast/
too-restrictive factors) are calibrated against this section's own Example
A (18g -> 38g in 24s, explicitly "clearly fast") rather than picked
arbitrarily, but they're still a single anchor point, not derived data —
see Phase 3b. The mechanical-suspicion threshold remains an unvalidated
guess.

The expected flow rate is also a Bayesian shrinkage estimate, not just a
fixed constant: it blends the global prior with the median flow rate across
*other* bags sharing the same `roast_level`, weighted by how many such
shots exist, so a roast level that genuinely runs faster or slower than the
generic guess stops being called "too fast"/"too restrictive" once this
installation's own history for that roast level says otherwise. Deliberately
not blended toward the current bag's own history, unlike an earlier version
of this design — bean-aging drift within one bag's life is handled by grind
correction chasing a fixed reference instead, so a bag's own shots never
feed back into its own reference point. An earlier per-bag version of this
shrinkage was circular: `median_flow_g_s` was built only from shots this
same classifier already called `healthy`, itself defined relative to the
blended rate, so as grind corrections chased drift back toward that rate,
the rate just tracked wherever the corrections settled and `duration_ratio`
lost the ability to see the drift at all - freezing the value per bag only
delayed the contamination, since even a frozen value was set from that
bag's own early, possibly still-correcting shots. Roast level is the right
granularity for the one case a rigid global constant doesn't cover (a bean
that genuinely can't reach the global pace at any sensible grind without
sacrificing taste or risking channeling) - a property of the bean, not of
this individual bag's own history, which is exactly the axis that can
drift within a bag's life. This is
deliberately different treatment from mechanical suspicion above: a
roast level's characteristic pace is a reference point with
nothing to protect against, so it's allowed to fully self-normalize,
whereas the channeling-suspicion boundary is not, or a bag with a
recurring puck-prep problem would train the model to stop catching it.
This asymmetry also decides which shots feed each baseline:
`storage.roast_level_baseline` (flow rate) includes `too_fast`/
`too_restrictive` shots alongside `healthy` ones - they're clean pours
that simply ran at a different pace than currently expected, exactly the
signal a pace-characterization baseline needs, and excluding them risked a
bootstrapping deadlock (a roast level whose true pace sits outside the
initial global-prior-influenced window would have every shot classified
off-target forever, never entering the pool that exists to correct for
that). `storage.recent_healthy_features` (channeling's `median_late_accel`)
stays `healthy`-only, since a `too_fast`/`too_restrictive` shot hasn't been
independently verified non-channeling any more rigorously than a
`puck_prep_issue` one has. Both exclude `puck_prep_issue`/
`invalid_measurement` either way.

Section 13's "was time to first flow plausible?" is implemented
(flow detected well before the configured pre-infusion should have ended,
or never detected despite a real final weight, are both treated as
`invalid_measurement`); "did the flow change smoothly?" / "is the scale
trace noisy or otherwise unreliable?" is deliberately not implemented —
every variance-based noise metric tried during development also fired on
a genuine `puck_prep_issue` shot (a sustained trend deviates from a smooth
curve just as much as random jitter does), so it's left for either a
smarter noise metric or real recorded scale noise to calibrate against,
rather than shipping a check that could misclassify a real mechanical
problem as an unrelated measurement fault.

---

### Phase 3b — Data-driven thresholds

Once enough real shot history exists (across bags/recipes), replace
`flow_analysis.py`'s placeholder constants — the expected total-flow rate
used for the too-fast/too-restrictive check, and the channeling-suspicion
scaling — with values fitted to this project's own recorded shots, rather
than hand-picked guesses. This can only start once Phase 3 has been wired
in for long enough to accumulate a meaningful number of classified shots.

Success criterion:

> Fast/slow/suspicious thresholds are derived from this installation's own
> shot history rather than fixed guesses.

A handful of `flow_analysis.py` constants (`_DISTURBANCE_SUSTAIN_MS`,
`_MAX_PLAUSIBLE_WEIGHT_DROP_G`, `_DISTURBANCE_DETECTION_FLOOR_G`,
`_FIRST_FLOW_SUSTAIN_MS`, `FIRST_FLOW_THRESHOLD_G_S`) can't be fitted from
data at all, even once real history exists - each was hand-tuned against
one specific anomalous shot a human diagnosed by eye, and nothing in a
weight/flow trace alone tells the app "yes, the cup was actually bumped at
that timestamp" the way a completed shot's own final weight confirms or
refutes a stop-latency prediction every single shot. Rather than leave
these static and unexamined, `tests/test_constant_drift.py`'s
`ConstantDriftReport` re-runs `analyze_shot` against every real fixture in
`tests/fixtures/real_shots/` with each constant nudged ±20%, and flags any
fixture whose classification flips - a near-miss worth a human's attention,
not a failure (the report never fails the suite; it prints a finding for a
human to review). The same file's `StopLatencyBucketDriftReport` does the
analogous check for `_STOP_LATENCY_BUCKET_CUTOFF_G_S` (3.0 g/s): does it
still sit in a genuine low-density gap between the two `stop_latency_normal_s`/
`elevated_s` bucket populations, as new real shots accumulate. Both reports
grow more useful as the fixture set grows, on the working assumption that it
becomes representative of real usage over time.

Also revisit then: `_baseline_deviation_suspicion` in `flow_analysis.py`
doesn't grow more bag-dependent as a bag's healthy-shot count increases -
unlike the flow-rate expectation above, which does. This is deliberate,
not an oversight: `median_late_accel` (the channeling-suspicion baseline)
is built only from shots this same classifier already called `healthy` -
self-labeled, not independently verified - so if the fixed prior is even
slightly lenient, mildly-bad shots leak into that pool and pull a bag's
own tolerance toward the exact badness a channeling check exists to catch,
with nothing to correct the drift once it starts. Flow rate has no
equivalent risk (a bag's pace is a fact, not an evaluative judgment),
which is why only it gets full shrinkage. See
`docs/todo/ADAPTIVE_LEARNING_PLAN.md` §4 for the remaining open design
question (a safer partial version, gated on independently-verified
outcomes like a `balanced` flavor report on both axes, not yet built).

See `docs/todo/ADAPTIVE_LEARNING_PLAN.md` for the remaining constants still
awaiting real accumulated shot/taste-tag history before they can be
calibrated or reconsidered - the constants that had enough already decided
(the roast-level-keyed shrinkage above, the human-inspection-only
drift-detection reports, the `duration_ratio`-band baseline-inclusion rule)
have already shipped and moved into this document.

---

### Phase 4 — Expert grind correction

Implement discrete recommendations:

```text
-2
-1
-0.5
0
+0.5
+1
+2
```

DF54 units. (Widened from the original ±1 range to ±2 so a "grossly"
fast/restrictive shot isn't capped at the same correction as a "moderately"
fast/restrictive one. Note this range, and which magnitude maps to which
severity tier, is a project-level implementation choice, not something
sourced from the videos — none of the nine James Hoffmann transcripts give a
numeric grind-step size for any grinder; every adjustment he describes is
qualitative ("a little finer", "way too fast"). §6.2's own `16 to 14` example
predates this research and is equally a hand-written placeholder, not
corroborating evidence — see `definitions.yaml`'s `expert_rules.grind_correction`
table and `docs/data/DIAL_IN_RULES.md` for what is and isn't
actually sourced from the videos.)

Success criterion:

> Gross flow errors are corrected without changing multiple variables at once.

Rule source: `expert_rules.grind_correction` in `definitions.yaml`, derived
from `docs/data/DIAL_IN_RULES.md`. The `duration_ratio` band
boundaries beyond the existing 0.8/1.6 healthy split (i.e. the
slightly/moderately/grossly tiers) are placeholder anchors, not derived
data — same caveat as Phase 3b's thresholds — and should be revisited once
enough classified shots exist to calibrate them for real.

Every band's `duration_ratio_max`/`grind_delta` (other than
`grossly_restrictive`'s fixed catch-all max and `healthy`'s fixed `0.0`
delta) is also a dashboard-editable number entity (`grind_band_*`, in the
"Connection and control" card's "Grind correction" group) - the same
pattern `min_step_target_yield`/`min_step_dose`/`min_step_temperature_offset`
already use for `minimum_meaningful_step` above, seeded from the
`definitions.yaml` defaults and persisted like any other dashboard setting
(`BaristaRuntime._grind_correction_config`).

**Overshoot damping is implemented.** `grind_correction.recommend_grind_delta`
is otherwise a pure, memoryless function of the current shot's own
`duration_ratio` - but Episode 1 of "How I Dial-In Espresso" shows a
different shape after an overshoot specifically: correcting too coarse and
landing "too fast" is followed by an explicitly *smaller* step back
("just moving that grind just fractionally finer"), not a same-sized
reversal. `recommend_grind_delta` now detects this from the last two shots
on the same bag+recipe (`storage.previous_grind_correction_shot`, `bag_id`-
scoped so a bag swap never crosses into a different bag's history): if the
previous shot's own recommended correction was actually applied (the
recipe's `hold_constant` fields - dose/yield/temperature/preinfusion -
match, so the swing is attributable to grind alone) and this shot lands on
the *opposite* side of healthy from where the previous one was, the
correction is damped one band-tier back toward `healthy` instead of
returning the full-magnitude band value - with a floor so it never
collapses all the way to `healthy`'s own `0.0` delta (the shot is still
off, it still needs some correction). "One band-tier down" - not a halved
delta - and the floor are both project-level implementation choices, not
sourced from any transcript; the underlying step magnitudes themselves
still need real accumulated overshoot-then-correction pairs to validate,
per this project's own standing rule against inventing tuned constants
without checking them against real fixtures.

---

### Phase 5 — Flavour correction

**Why the shipped model replaced an earlier better/same/worse version**:
that design asked "did this improve `<axis>`? Better/Same/Worse," then -
only on `same`/`worse` - a separate "still `<tag>`?" confirmation. A
hypothetical stress-test (not sourced from any transcript - constructed
during design review): `bitter_harsh` applied 42→38→34 across three
shots, then overshoots. This exposed three problems: the real category
was only ever checked after a `same`/`worse` report, so a shot could
silently go `balanced` and never be caught; deciding "revert, then
escalate" needs the *previous* shot's own category, but `worse` only
captured an abstract relative judgment, not a direct one; and "worse"
doesn't make physical sense as a same-axis intensification - overshooting
`bitter_harsh` flips to the *opposite* problem (`sour_sharp`), not "more"
of the same one. Two real Hoffmann quotes validate the replacement:
*"I'm going to work on just moving that grind just fractionally finer"*
(**"How I Dial-In Espresso - Episode 1.txt"**, ~2:42-2:47) - an overshoot
gets a *smaller* same-lever nudge, not a revert or an intensification -
and *"if it's there time and time and time again, then that tells me
that this coffee might need a little bit more heat"* (**"Understanding
Espresso - Brew Temperature (Episode #5).txt"**, ~5:18-5:29) - persistence
escalates to a new lever, not a repeat of the same one.

Implemented via two independent taste-feedback push notifications sent
`flavor_feedback_delay_s` (definitions.yaml) after a shot finishes, only for
a shot classified `healthy` - taste feedback is only meaningful once Stage 1
(mechanical/hydraulic health, §13) has nothing left to correct - extraction
axis (sour/sharp, bitter/harsh, balanced) and mouthfeel axis (thin/weak,
dry/astringent, balanced), each answerable with a single tap and no
app-opening required (a plain actionable notification, 3 buttons). The two
axes are independent because the underlying symptoms are not mutually
exclusive - a shot can be sour and astringent at once - so collapsing them
into one notification with one winning tag would lose real signal.

Sequencing is a full state machine, not an independent per-tag lookup:
`flavor_correction.py`'s `resolve_flavor_state` replays each axis's own
answered-response history (derived fresh each time, nothing persisted in a
new table) to reconstruct which lever is active for that axis right now. A
tag's first report (or a persisting report of the same tag) recommends its
current stage's primary lever/delta at full strength immediately - no
persistence gate, even though a self-reported taste tag genuinely is
noisier than a directly-measured `duration_ratio` (which Stage 2's grind
correction reacts to on every qualifying shot, with no gating at all). Per
the Brew Temperature quote above, the caution is about *attribution* risk
(multiple things vary between any two shots at once), not about
distrusting a single report outright - so the fix doesn't need a
persistence-count rule at all: a shot can't keep tasting more sour forever
as yield keeps climbing, so sustained pushing in one direction is
physically bound to produce either `balanced` or the axis's *other* tag
within a bounded number of shots.

**Citation, pinned here since it's easy to lose track of**: the actual
yield magnitude each of those repeated pushes uses (`sour_sharp`'s
`delta_g: 4`) is *not* a project-level guess - it's sourced from a
specific cross-creator check. Stage 3's own original spec above (§13's
worked examples, "healthy flow + slightly sharp -> try longer yield first")
originally used Hoffmann's own tighter `+2` to `+3g` ceiling; three
independent cross-creator examples (Lance Hedrick's "Fix Sour" Tip 3: "maybe
five... maybe eight... maybe 10 grams more"; Lance's "Quick & Easy Guide":
demonstrated +5g; Matt Perger: +10g, a 25% jump) all used bigger jumps, so
the range was widened - see `docs/data/CROSS_CREATOR_RULE_CHECK.md`'s own
"`sharp_sour` — direction confirmed, magnitude conflicts" section for the
full worked comparison, and `definitions.yaml`'s own comment on
`sour_sharp.delta_g` for why `4` specifically (just below that widened
range's own low end) was picked as the one concrete number, rather than
re-deriving it from the stored range on every read.

**Side note - real evidence for the *decrease* direction too**
(`bitter_harsh`'s own lever, and its own `delta_g: 4`): Hoffmann's "How I
Dial-In Espresso - Episode 2" (barrel-aged Ethiopian, Sage/Breville dual
boiler) reports exactly this tag's own taste signature - *"it's a little
harsh and it almost has this kind of roastiness that it shouldn't have...
a certain sort of bitterness"* (~4:00-4:13), literally "harsh" plus
"bitterness" - then walks through the correction, spoken as loose ranges
rather than one clean number each time (worth deducing carefully rather
than skimming): *"I was like 19 to 40, 41 in that shot"* (~5:09-5:11) is
the starting point; *"I actually wanna bring that ratio down a little
bit... maybe more like 36 to 38"* (~5:21-5:26) is the correction, alongside
a 2°C temperature drop and a slightly finer grind; *"what we aim before we
got which is 19 in 38 out"* (~6:01) confirms where it actually landed; and
*"you could pull that just fractionally shorter maybe like 35, 36"*
(~7:08-7:10) is a further optional refinement he mentions but doesn't
necessarily execute. So the real sequence is **~41 → 36-38 (landing at
38) → 35-36**, all decreasing, a real ~3-5g step each move - consistent
with `bitter_harsh`'s own `delta_g: 4`, matching `sour_sharp`'s explicit
value above rather than being left to coincide with the floor.

Every notification is the exact same question - the axis's own two tags
plus `balanced` - there's no second question type to ask. Whether the
*other* tag showing up is a correction or an unrelated problem depends on
whether the two tags share a lever at the current stage ("coupled" - same
lever, opposite direction, e.g. `sour_sharp`/`bitter_harsh`, both `yield`
at the primary stage) or not ("uncoupled" - different levers, e.g.
`thin_weak`/`dry_astringent`, `dose` vs. `yield`). An uncoupled report is
just this axis's other, independent problem - treated exactly like a
fresh report, discarding whatever step was being tracked and starting
fresh at its own base. A coupled report is an overshoot signal, corrected
the way `grind_correction`'s own overshoot damping (Phase 4, above)
corrects an overshoot: the *current* step - shared across the coupled
pair, since they're two labels for the same physical lever - damped
(halved) in the reported tag's own direction, never a same-sized
reversal and never re-derived from that tag's own nominal step (a step
already known to be too coarse for this lever doesn't stop being too
coarse just because the other tag reported next). A further report of the
*same* tag keeps that current step exactly as it is - persisting doesn't
grow or shrink it - so once an overshoot has damped a lever down to a
finer scale, continuing to confirm that tag keeps fine-tuning at that
finer scale rather than jumping back to a coarser nominal one. Once the
damped step would be smaller than the lever's own
`minimum_meaningful_step`, the lever has converged as far as it can go -
escalate to the tag's `escalation` lever instead, if one is defined
(`thin_weak`/`dry_astringent` don't, so converging there just stops
offering an automatic recommendation - a same-lever tug-of-war with
nowhere further to go belongs in front of the user, surfaced in
`recommended_flavor_note`, not something to keep silently re-nudging).
`active_tag` always tracks whichever tag was *most recently* reported,
never the tag that started the sequence - escalating has to use the lever
mapping of whichever problem is actually current, or it moves the
escalated lever the wrong direction. `balanced` resets an axis back to no
active lever.

Whether a coupled overshoot produces a real damped nudge or escalates
immediately depends on *which* tag's report established the current step,
not just on the tags' individually configured magnitudes - a step only
survives one halving above the field's own floor once it's configured to
more than double that floor. With today's `definitions.yaml` values,
neither extraction tag clears that bar: both `bitter_harsh` and
`sour_sharp` have their own explicit `delta_g: 4`, which happens to be
exactly equal to the `target_yield_g` floor (`4`), not double it. So
today, a coupled overshoot on the
extraction axis escalates on the very first flip regardless of which tag
reports first (halving a value already at the floor always falls below
it) - not a fixed property of the algorithm, just where these two
particular numbers currently sit relative to each other (see
`flavor_correction`'s own tests: a synthetic fixture exercises the
genuine-nudge branch directly, and a separate live-config test derives
which branch to expect from whatever `definitions.yaml` currently says,
rather than assuming either outcome, so a future retune can't leave it
silently asserting a stale result). The damping ratio itself (a straight
halving) is a project-level
implementation choice, not sourced from any transcript - the same
treatment `grind_correction`'s own "one band-tier back" damping choice
gets above, for a different reason (grinders vary, so its corrections are
relative positions on a per-grinder ladder; yield/dose/temperature are
universal physical units, so a continuous halving is the simpler analog
here). `minimum_meaningful_step` is also user-adjustable, as three
dashboard-editable number entities (`min_step_target_yield`/
`min_step_dose`/`min_step_temperature_offset`, in the "Connection and
control" card) - the same pattern `early_stop_margin_min_g`/
`machine_max_shot_s` already use, seeded from the `definitions.yaml`
default and persisted like any other dashboard setting
(`BaristaRuntime._flavor_correction_config`).

Grind correction and flavor correction are coupled, not independent:
while a bag's last shot still needs grind correcting (not yet `healthy`),
flavor-lever recommendations are suppressed entirely for that bag, so only
one lever is ever the "live" suggestion at a time - matching the sourced
dial-in walkthroughs, where he is never seen acting on two levers'
recommendations at once. The bag's last-shot diagnostic summary still
shows every lever with a nonzero recommendation regardless (useful context
for a human even when only one is currently "active"); only the
per-recipe-field badges that drive the next shot's actual recommendation
apply the suppression and narrow to one. Separately, a bag+recipe landing
on `puck_prep_issue` `puck_prep_issue_streak_threshold` shots in a row
(§12's coarsen-override, described above) also interacts with this: it
fires from Stage 1 and overrides "repeat the recipe" with an actual grind
change, independent of anything flavor-feedback related.

Success criterion:

> System makes understandable, minimal, barista-style recipe changes.

---

### Phase 6 — Environmental context

Add:

- ESP32;
- SHT45;
- RH;
- ambient temperature.

Initially log only.

Later test whether environment materially improves predictions.

---

### Phase 7 — Optional future intelligence

Only once the previous phases are stable:

- richer bag-age model;
- similarity between coffees;
- preference learning;
- Bayesian optimisation;
- occasional refractometer/TDS integration.

---

## Shot-data export

The user-facing Brew view includes a package-managed **Copy all shot data** control. It requests a plain-text export from the integration and copies it directly to the browser clipboard. The export includes every stored shot plus its raw BOOKOO time series and the recipe/context metadata needed for diagnosis. Each sample carries a `post_stop` flag so samples recorded after the stop command remain visible; this is important because moving the scale or cup at the end of a shot can otherwise contaminate flow statistics.

The export format is deliberately paste-friendly rather than JSON: a metadata block is followed by a tab-separated sample table for each shot. This should be the standard way to share raw traces for future troubleshooting and diagnostic-model development.

## 29. Research and documentation references

Useful background material discussed during the design:

- Breville/Sage Barista Express instruction manual — shot controls, manual pre-infusion, temperature adjustment.
- BOOKOO open-source BLE documentation — scale integration.
- Home Assistant SwitchBot integration documentation — local Bluetooth control.
- SwitchBot BLE/API documentation — Bot long-press behaviour.
- Recent espresso-flow and porous-bed research — flow-curve interpretation and limits of total-flow measurements for diagnosing spatial channeling.
- Espresso extraction literature on grind, temperature, extraction yield, and reproducibility.

These sources should be re-checked during implementation because firmware, HA integrations, and device APIs can change.

---

## 30. Final V1 architecture

```text
                           ┌─────────────────────┐
                           │   Home Assistant    │
                           │                     │
                           │ bag state           │
                           │ recipe state        │
                           │ expert rules        │
                           │ logging             │
                           └───────┬─────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                    │
              ▼                    ▼                    ▼
      BOOKOO Themis Ultra   SwitchBot BREW      SwitchBot POWER
      weight + flow         PI/start/stop        preheat
              │
              ▼
      Shot trace analysis
              │
              ▼
      mechanical validation
              │
       ┌──────┴──────────┐
       │                 │
       ▼                 ▼
   suspicious          healthy
       │                 │
 repeat recipe           ▼
                  hydraulic correction
                         │
                         ▼
                  flavour correction
                         │
                         ▼
                  next-shot recipe


Optional:
ESP32 + SHT45 -> RH + ambient temperature
```

---

## 31. Core design rules to preserve

1. **Do not learn from obviously bad shots.**
2. **Do not call total-flow anomalies definitive channeling detection.**
3. **Fix hydraulics before fine-tuning flavour.**
4. **Change one meaningful variable at a time whenever practical.**
5. **Treat the DF54 as a discrete control.**
6. **Track physical bags independently.**
7. **Keep normal and decaf state separate.**
8. **Use the SwitchBot actuation timeline instead of inferring pump state.**
9. **Use predictive stop control for yield.**
10. **Keep BO out of the critical path until the deterministic system works.**

---

## 32. Current recommended V1 bill of materials

Already owned:

- Breville/Sage Barista Express
- DF54 grinder
- Home Assistant Raspberry Pi
- SwitchBot Bot on power button
- Tapo P110 smart plugs
- ESP32 boards
- soldering equipment
- 3D printer

Add:

- BOOKOO Themis Ultra Coffee Scale
- second SwitchBot Bot for brew button
- optional SHT45 humidity/temperature sensor

Not required:

- camera
- accelerometer
- machine pressure sensor
- internal espresso-machine modification
- motorised grinder
- custom scale
- Bluetooth proxy unless real-world connectivity requires one

---

## 33. Next engineering task

The best first build target is:

> **One-button shot with deterministic pre-infusion, complete BOOKOO weight logging, and automatic predictive stop at target yield.**

Everything else can be layered on after that works reliably.


### SwitchBot long-press and Barista Express safety

The brew SwitchBot must physically hold the Barista Express brew button for the bag's configured pre-infusion duration. Before each shot, Barista Assist directly programs the Bot's stored long-press duration over the published SwitchBot BLE protocol, then uses the existing Home Assistant SwitchBot entity to trigger the action.

**Confirmed against Breville's own instruction books** (BES875, BES878): the 1-CUP/2-CUP button has two distinct modes. A single tap starts "Pre-Programmed Shot Volume" mode, which auto-stops at its pre-set volume - press-and-hold instead starts "Manual Pre-Infusion & Extraction" mode (hold to pre-infuse, release to extract, press again to stop), which the manual describes without any equivalent auto-stop language. Barista Assist always holds the button (to drive pre-infusion), so every shot it pulls runs in that held mode, not the single-tap one.

That doesn't mean there's no machine-side backstop at all, though - live testing (a held water-only shot, no coffee) showed the machine cutting itself off after about 30s including a 7s pre-infusion hold, and this lines up with third-party reports that these machines track shot *volume* via an internal flow meter rather than pure elapsed time (e.g. a shot pulled with the portafilter empty/absent - i.e. almost no flow resistance - is reported to cut off after roughly 30s too). Whether that 30s cutoff is a fixed, generic safety timer or is actually tied to whatever volume is currently programmed into that specific CUP button is unconfirmed - the BES875 manual lists 30ml/60ml as the 1-CUP/2-CUP single-tap-mode *defaults*, suspiciously close to the water test's 30s result. Either way, how long it takes before cutting off depends on how fast liquid is actually flowing: a real, resistive coffee puck should take meaningfully longer to reach the same cutoff volume than water did in that test. This isn't documented with full confidence anywhere public, so `machine_max_shot_seconds` should be set from the user's own measured worst case with a real coffee puck (per the README's bench-test instructions), not assumed from a water test or any number quoted here.

Given that, the user must still program both shot buttons with a known maximum duration before enabling automatic control, and Barista Assist still treats that value as a hard limit with a safety margin, permitting automatic target-stop and abort commands only inside the protected window. After the protected deadline, Barista Assist deliberately does **not** press the brew button, because in the single-tap/programmed mode a press after the shot naturally ended could start a new one instead of stopping anything (this protection is conservative: in the held mode Barista Assist actually always uses, the current shot may in fact still be running rather than having ended, so this same press might have safely stopped it — but there is no reliable way for Barista Assist to tell those two situations apart from software alone, so it stays on the safe side and never presses either way past the deadline). It enters `manual_stop_required` instead and waits before finalising the log. The machine's own volume-based cutoff is a real, independently-observed backstop, but its exact behavior isn't something Barista Assist can rely on with full confidence - **the user should still treat physically stopping the machine as their own responsibility**, not something guaranteed to happen automatically.

That wait exists for two more reasons besides giving the user time to intervene: scale samples keep being appended to the in-progress shot for as long as it stays active, so finalising immediately at the protected deadline would record whatever the scale happened to read at that instant rather than the shot's true final weight, understating the actual yield; and keeping the shot marked active for that window also prevents a new brew from starting while the machine may still be pouring the previous one.

Adapt PI (§6.5) is the one case that deliberately opts *into* the single-tap "Pre-Programmed Shot Volume" mode described above instead of avoiding it - trading that held mode's press-again-to-stop certainty for not needing to hold the button or program a per-bag duration at all. The same protected-deadline/`manual_stop_required` logic still applies unchanged either way; only the backstop that would fire past it differs (programmed volume vs. the water-only cutoff observed above).
