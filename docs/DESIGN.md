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
0.1 to 0.2 g
```

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

Example policy:

```text
grossly fast        -> 16 to 14
moderately fast     -> 16 to 15
slightly fast       -> 16 to 15.5
healthy             -> no change
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
0.5 g
```

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

**Auto PI** (`switch.barista_assist_auto_pi`, a controller-level toggle alongside `stop_compensation` - see `BaristaRuntime.auto_pi`, persisted in the runtime's own `Store`, not a config-entry option) is an alternative to per-bag `preinfusion_s` entirely: instead of holding the button for a configured duration, the Bot is single-tapped and the Barista Express is assumed to run its own built-in ~8 s pre-infusion (`AUTO_PI_DURATION_S` in `runtime.py`) before ramping to full pressure. Because there's no hold duration to program, `async_brew` skips `_async_prepare_brew_bot` entirely when `auto_pi` is set - both the start and stop presses go through `_async_press_brew_bot` (Home Assistant's own switchbot integration) only, never the direct-BLE `SwitchBotBotConfigurator` path. The per-bag Pre-infusion tile is hidden from the dashboard in this mode (see `websocket.py`'s `_strip_auto_pi_tile`), and toggling the switch regenerates the dashboard file immediately (`async_set_entity_value`'s `auto_pi` branch) so the tile hides/reappears without a reload.

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
-> try slightly longer yield

healthy flow + sharp but longer yield makes drink too weak
-> return yield and try +1 °C

healthy flow + too intense in milk
-> consider slightly longer yield or small dose reduction

healthy flow + good flavour but too weak
-> consider a small dose increase

suspicious flow + sour and dry together
-> repeat puck prep before changing recipe
```

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
coffee is actually being collected, so any meaningful drop below its own
running peak so far is unambiguous interference, not flow. Everything from
that point on is discarded before classification runs, on whatever prefix
remains - the user never needs to be told when it's "safe" to touch the
cup. This is a more general replacement for an earlier idea of truncating
at the stop command's timestamp: it also catches a bump mid-pour, doesn't
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
fixed constant: it blends the global prior with a bag's own median flow
rate from its recent healthy shots, weighted by how many such shots exist,
so a bag that genuinely runs faster or slower than the generic guess stops
being called "too fast"/"too restrictive" once its own history says
otherwise. This is deliberately different treatment from mechanical
suspicion above: a bag's characteristic pace is a reference point with
nothing to protect against, so it's allowed to fully self-normalize,
whereas the channeling-suspicion boundary is not, or a bag with a
recurring puck-prep problem would train the model to stop catching it.

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

Also revisit then: `_baseline_deviation_suspicion` in `flow_analysis.py`
doesn't grow more bag-dependent as a bag's healthy-shot count increases,
unlike the flow-rate expectation (which already does, via Bayesian
shrinkage). This is deliberate, not an oversight — see the `TODO` comment
on that function for the full reasoning — but it's worth reconsidering once
there's a way to check a bag's own baseline against real, independently
verified outcomes (not just shots this same classifier already called
"healthy"), since only then can more bag-dependence be added there without
risking a recurring problem normalizing itself out of detection.

---

### Phase 4 — Expert grind correction

Implement discrete recommendations:

```text
-1
-0.5
0
+0.5
+1
```

DF54 units.

Success criterion:

> Gross flow errors are corrected without changing multiple variables at once.

---

### Phase 5 — Flavour correction

Add user sensory tags and rules for:

- yield;
- temperature;
- small dose adjustment;
- PI refinement.

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

**Confirmed against Breville's own instruction books** (BES875, BES878): the 1-CUP/2-CUP button has two distinct modes. A single tap starts "Pre-Programmed Shot Volume" mode, which auto-stops at its pre-set volume - press-and-hold instead starts "Manual Pre-Infusion & Extraction" mode (hold to pre-infuse, release to extract, press again to stop), which the manual describes without any equivalent auto-stop language. Barista Assist always holds the button (to drive pre-infusion), so every shot it pulls runs in that manual mode, not the single-tap one.

That doesn't mean there's no machine-side backstop at all, though - live testing (a held water-only shot, no coffee) showed the machine cutting itself off after about 30s including a 7s pre-infusion hold, and this lines up with third-party reports that these machines track shot *volume* via an internal flow meter rather than pure elapsed time (e.g. a shot pulled with the portafilter empty/absent - i.e. almost no flow resistance - is reported to cut off after roughly 30s too). Whether that 30s cutoff is a fixed, generic safety timer or is actually tied to whatever volume is currently programmed into that specific CUP button is unconfirmed - the BES875 manual lists 30ml/60ml as the 1-CUP/2-CUP single-tap-mode *defaults*, suspiciously close to the water test's 30s result. Either way, how long it takes before cutting off depends on how fast liquid is actually flowing: a real, resistive coffee puck should take meaningfully longer to reach the same cutoff volume than water did in that test. This isn't documented with full confidence anywhere public, so `machine_max_shot_seconds` should be set from the user's own measured worst case with a real coffee puck (per the README's bench-test instructions), not assumed from a water test or any number quoted here.

Given that, the user must still program both shot buttons with a known maximum duration before enabling automatic control, and Barista Assist still treats that value as a hard limit with a safety margin, permitting automatic target-stop and abort commands only inside the protected window. After the protected deadline, Barista Assist deliberately does **not** press the brew button, because in the single-tap/programmed mode a press after the shot naturally ended could start a new one instead of stopping anything (this protection is conservative: in the manual/hold mode Barista Assist actually always uses, the current shot may in fact still be running rather than having ended, so this same press might have safely stopped it — but there is no reliable way for Barista Assist to tell those two situations apart from software alone, so it stays on the safe side and never presses either way past the deadline). It enters `manual_stop_required` instead and waits before finalising the log. The machine's own volume-based cutoff is a real, independently-observed backstop, but its exact behavior isn't something Barista Assist can rely on with full confidence - **the user should still treat physically stopping the machine as their own responsibility**, not something guaranteed to happen automatically.

That wait exists for two more reasons besides giving the user time to intervene: scale samples keep being appended to the in-progress shot for as long as it stays active, so finalising immediately at the protected deadline would record whatever the scale happened to read at that instant rather than the shot's true final weight, understating the actual yield; and keeping the shot marked active for that window also prevents a new brew from starting while the machine may still be pouring the previous one.

Auto PI (§6.5) is the one case that deliberately opts *into* the single-tap "Pre-Programmed Shot Volume" mode described above instead of avoiding it - trading the manual mode's press-again-to-stop certainty for not needing to hold the button or program a per-bag duration at all. The same protected-deadline/`manual_stop_required` logic still applies unchanged either way; only the backstop that would fire past it differs (programmed volume vs. the water-only cutoff observed above).
