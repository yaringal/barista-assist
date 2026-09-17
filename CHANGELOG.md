# Changelog

## 0.3.5

### Fixed

- **Fixed another fresh-install/update failure, the same shape as 0.2.5's `bleak-retry-connector` fix**: `manifest.json` hard-pinned `bleak-retry-connector==4.6.3`, which now conflicts with a newer Home Assistant's own `bluetooth` component constraint (`bleak-retry-connector==4.7.1` as of HA 2026.9). HA's requirements installer failed with `RequirementsNotFound`, and the integration wouldn't load at all. Rather than bumping the pin again (which would only defer the same failure to HA's next bump), the explicit requirement is dropped entirely: Barista Assist already depends on `bluetooth_adapters`, which itself depends on `bluetooth`, so `bleak-retry-connector` is already guaranteed to be installed at whatever version HA core pins - the library's actual API surface this integration uses (`establish_connection`, `BleakClientWithServiceCache`) hasn't had a breaking change between 4.6.3 and 4.7.1.

### Testing

- Full suite: 321 tests, all passing (no test coverage for `manifest.json` itself - this can only really be verified by a live HA install).

## 0.3.4

### Added

- **Shot charts now show a "Stop Prediction" marker** (a dashed orange line + label) alongside the existing "Stop Sent" marker (renamed from plain "Stop") - Stop Sent is when the automatic-stop command was actually issued; Stop Prediction is when that same decision expected the pour to actually finish, accounting for the machine's own physical stop latency. Computed on demand from the shot's own recorded samples plus the current live stop-latency settings (`runtime_shot.py`'s `_predicted_stop_elapsed_ms`) - nothing new is stored per shot.
- **Shot charts now have y-axis (weight) tick marks and labels**, matching the existing x-axis (time) treatment.

### Changed

- **The target-yield dashed line now spans the whole chart width**, not just the healthy window - the target weight applies for the entire shot, not only during that window.
- **Chart layout polish**: the plot's own bottom edge now sits close to the region labels/tick numbers below it (tightened padding); the chart's y-range now has a little headroom so a healthy shot's own curve or target line doesn't sit flush against the very top edge; the Pre-infusion/Healthy/Stop Prediction labels now sit tight against the *top* of the chart instead of overlapping "Stop Sent" underneath it.
- **The shot-history summary row's yield column no longer truncates** (it could show e.g. "35.0 / 3..." on narrower screens) - it now sizes to its own content instead of shrinking with the other columns.
- **The chart's "healthy window" and the machine's stop-latency bucket decision are now each computed once, server-side, and simply drawn by the frontend** (`flow_analysis.py`'s new `expected_shot_seconds`/`healthy_window_ms`, shared with `analyze_shot`'s own classification; `runtime_shot.py`'s `_stop_latency_for_flow`/`_predicted_stop_elapsed_ms`) - previously both formulas were duplicated in the dashboard's own JavaScript, which could have silently drifted out of sync with the backend if either changed.

### Testing

- Full suite: 321 tests, all passing (up from 318).

## 0.3.3

### Changed

- **`too_fast_factor`/`too_restrictive_factor` (Stage 1 classification) and `grind_correction.bands` (Stage 2 grind correction) used to hardcode the same `0.88`/`1.10` duration-ratio boundaries in two separate places, with no link between them.** Split into a single shared, stage-agnostic `flow_analysis_constants.duration_ratio_bands` ladder (7 named bands, `grossly_fast` through `grossly_restrictive`) that both `flow_analysis.analyze_shot` and `grind_correction.recommend_grind_delta` now read from, plus a separate `expert_rules.grind_correction.grind_deltas` mapping that holds grind-correction's own per-band delta policy (its only remaining independent piece of tuning data). The dashboard-editable band-boundary entities now genuinely drive both stages at once instead of two independently-editable copies that could silently diverge.
- **The System view's grind-correction section is now two separate cards**: "Shot health categories" (the 6 shared duration-ratio band boundaries, now used by classification and grind correction alike) and "Grind correction" (the 7 grind-delta entities plus the puck-prep streak overrides) - reflecting that the band boundaries are no longer grind-correction-specific.
- **The 6 boundary entities were renamed to drop "grind" from their names**, since they're no longer grind-correction-specific: `grind_band_grossly_fast_max` → `duration_ratio_band_grossly_fast_max` (and equivalently for `moderately_fast`/`slightly_fast`/`healthy`/`slightly_restrictive`/`moderately_restrictive`). The 7 grind-delta entities (`grind_band_*_delta`) are unchanged - they remain grind-correction's own policy. This is a straight rename, not a migration: the old entities become unavailable and any dashboard-tuned override values under the old names are not carried forward (see Upgrade below).
- **Shot charts now shade the "healthy" completion window in green (matching how pre-infusion is already shaded in grey) and draw a dashed target-yield line across it**, instead of a single-point "expected completion" marker. The window's edges are the same `too_fast_factor`/`too_restrictive_factor` bounds classification itself uses (never re-derived or hardcoded in the chart), so it can't silently drift out of sync with what actually classified the shot.
- **Charts now label the pre-infusion, healthy, and stop regions directly under the chart** (grey/green/red text respectively, matching each region's own shading), positioned just below the plot itself, above the x-axis tick numbers.

### Testing

- Full suite: 318 tests, all passing.

## 0.3.2

### Fixed

- **A real shot with a small, sustained noise wobble near the tare baseline (~1.5s in, raw weight oscillating between -0.5g and -0.1g) could be wrongly classified `invalid_measurement`/`flow_started_before_preinfusion_end`**, even though pre-infusion had genuinely been honored - the noise happened to cross `first_flow_threshold_g_s` for exactly the old 300ms `first_flow_sustain_ms` window. Raised `first_flow_sustain_ms` to 600ms (`definitions.yaml`) - verified across the whole 300-1200ms range against every real shot fixture on file, this fixes the false positive with zero change to any other fixture's own classification.
- **A fast-flowing shot's automatic stop projected too much margin and undershot target by 3.5g/9%** (37.5g target, 34.0g actual). Traced to `stop_latency_normal_s`'s seed value (3.4s): averaged across the 4 real "normal"-bucket shots now on file (flow 0.97-2.81 g/s at the stop decision), the true observed latency is 3.17s, not 3.4s - lowered the seed to 3.2s. This measurably helps (that shot's own projected error drops from +3.45g to +2.89g) but doesn't fully resolve it - the 4-point sample actually shows observed latency *falling* as flow rises within the bucket, the opposite of the trend the two-bucket model assumes, which isn't enough data yet to justify a bigger redesign (a continuous flow→latency model) rather than a nudge to the shared average. The "elevated" bucket (4.3s) has its own real gaps (still under/overshooting on two of its four fixtures) but wasn't touched - out of scope for this fix.

### Testing

- Added two new real shot fixtures (`tests/fixtures/real_shots/choked_adapt_pi.txt`, `too_fast_but_flagged_invalid_adapt_pi.txt`) with matching regression tests, and fixed two tests that hardcoded the old `stop_latency_normal_s` value instead of reading it live.
- Full suite: 318 tests, all passing (up from 316).

## 0.3.1

### Added

- **Shot history now shows each shot's own Extraction/Mouthfeel flavor tags**, right after Roaster in the Shots view's expanded detail - the data was already being recorded, just wasn't surfaced there before.
- **Grind-correction band tiles (System view → "Connection and control" → "Grind correction") now show a live "how many seconds is this" hint alongside each band's raw duration_ratio value**, e.g. "(≤ 16.9s)" or "(16.9s .. 21.1s)" - duration_ratio alone isn't an intuitive unit, but a shot length in seconds is. It's computed server-side (`runtime_entities.py`'s `_grind_band_seconds_hint`/`_grind_band_beyond_seconds_hint`) from a fixed 1:2-ratio reference shot using definitions.yaml's own live defaults (dose_g/target_yield_g/preinfusion_s/expected_flow_g_s) - not a hardcoded constant, so it can't silently drift out of sync with those settings - and shown via each entity's own `seconds_hint`/`beyond_seconds_hint` attribute, the same mechanism the existing "recommended" attribute already uses. (An inline Jinja template directly on the tile's `name` was tried first, but Home Assistant's tile card doesn't support templating on `name`/`icon`/`color` at all - an explicitly rejected upstream feature request - so it rendered as literal, broken `{{ ... }}` text instead of evaluating.)
- **The Dose/Grind/Target yield/Temperature offset recipe tiles now prefix their "current → recommended" note with a ⚠️ when a recommendation is pending**, e.g. "⚠️ 15.0 → 14.0" - a tile's own `color` only tints its icon, never this secondary text, and (as above) tile fields can't be templated at all, so the emphasis lives in the attribute's own text instead of in dashboard.yaml.

### Changed

- **Recipe target yield's dashboard +/- step is now 1g, not 2.5g.**
- **The System view's settings layout was reorganized**: "Integration settings (Configure)" moved to its own section at the very end of the view (it was previously stuck in the middle, above the grind-correction settings); the grind-correction band ladder's separator markdown cards were simplified.

### Fixed

- **The packaged dashboard's `__TOKEN__` → real-entity-id substitution only ever matched a token that was the *entire* string value of a YAML field**, so it couldn't resolve a token embedded inside a larger string (needed for the markdown "Severely restrictive" card's own live template above, which reads `state_attr('__GRIND_BAND_MODERATELY_RESTRICTIVE_MAX__', ...)` - the token is only part of that string). `websocket.py`'s `_replace_tokens` now substitutes a token wherever it appears in a string, not just when it's the whole value.

### Testing

- Added tests for the grind-band seconds hints (first-band edge case, mid-band range, live-edit tracking, and the trailing unbounded-band hint) and the token-substitution fix.
- Full suite: 316 tests, all passing (up from 311).

## 0.3.0

### Added

- **Barista Assist now recommends grind changes, not just diagnoses shots.** `grind_correction.py` (new) maps a shot's `duration_ratio` onto a discrete DF54 correction - `-2`/`-1`/`-0.5`/`0`/`+0.5`/`+1`/`+2` - via seven severity bands (`grossly_fast` through `grossly_restrictive`), so a "grossly" fast/restrictive shot gets a bigger nudge than a "moderately" one instead of being capped at the same correction (the original design only went to ±1). The band boundaries and step sizes are documented placeholders, not derived data - none of the source video transcripts (`docs/data/DIAL_IN_RULES.md`) give a numeric grind-step size for any grinder - and are called out as such rather than presented as validated science. **Overshoot damping is included**: if the previous shot on the same bag+recipe landed on the opposite side of healthy from this one, and nothing but grind changed between them, the correction is damped one band-tier back toward healthy instead of applying the full-magnitude step again - mirroring Hoffmann's own "just moving that grind just fractionally finer" behavior after he overshoots in "How I Dial-In Espresso" Episode 1, rather than swinging the same distance back and forth forever.
- **Barista Assist now also recommends recipe changes from how a shot actually tasted, via two independent push-notification prompts sent after each healthy shot** - one for extraction (Sour/Sharp, Bitter/Harsh, Balanced), one for mouthfeel (Thin/Weak, Dry/Astringent, Balanced) - each a plain 3-button actionable notification, no app-opening required. This only fires for a `healthy`-classified shot (taste feedback is only meaningful once the mechanical/hydraulic side has nothing left to correct) and only if you've set a notification target (new, optional "Taste-feedback notification target" dropdown in the integration's setup/options flow, populated from your already-registered `notify.*` services; leave it unset and nothing is sent). The underlying logic (`flavor_correction.py`, new) is a real state machine, not a one-shot lookup: a tag's first report recommends its lever's full step immediately (no "wait for a pattern" gate, since Hoffmann's own persistence caution is about *attribution* risk between shots, not distrust of a single report); a further report of the *opposite* tag sharing the same lever (e.g. Sour/Sharp vs. Bitter/Harsh, both riding on yield) is treated as an overshoot and the step is halved rather than reversed or repeated at full size, straight from the same Dial-In Episode 1 moment the grind-overshoot damping above is sourced from; and once a damped step would fall below that field's own minimum meaningful size, the axis escalates to a different lever instead (e.g. from yield to temperature) rather than continuing to nudge something that's converged. Grind correction and flavor correction never compete for attention: while a bag's last shot still needs grinding corrected, flavor recommendations are suppressed for that bag entirely, so only one lever is ever "live" at a time. The actual step sizes were checked against more than just Hoffmann - `sour_sharp`/`bitter_harsh` both use a 4g step after Lance Hedrick's and Matt Perger's own worked examples (5-10g corrections) turned out consistently bigger than Hoffmann's own stated 2-3g ceiling; see `docs/data/DIAL_IN_RULES.md` Part 5 for the full cross-creator comparison, including the one change (a coarser-grind-by-default bias) that was deliberately *not* adopted for being a stated personal preference rather than a mechanism.
- **A brand-new bag with no matching or similar existing coffee to warm-start from now gets a roast-level-based starting recipe instead of a flat default for every bag.** A new "roast level" field on the new-bag form seeds `target_yield_g` (dark ≈1:2, medium ≈1:2.2, light ≈1:3+), `dose_g` (lighter roasts start ~2g lower), and `temperature_offset_c` (dark -1°C / medium 0°C / light +1°C from your machine's own baseline) - sourced from James Hoffmann's and Lance Hedrick's videos and cross-checked against each other where they disagreed (Lance's own medium-roast ratio, 2.5, didn't hold up against Hoffmann's actual medium dial-in result and was revised down to 2.2). These are explicitly loose starting points, not fixed targets, and only apply to a genuinely empty slot - a bag with a same-coffee or similar-coffee match still takes priority over the roast-level fallback.
- **The flow classifier's "is this shot too fast/too slow" expected flow rate now adapts per roast level, not just globally.** As shots accumulate for a given `roast_level` across your other bags, the expected extraction-phase flow rate blends toward that roast level's own observed pace (a real Bayesian shrinkage estimate, weighted by how much history exists), so a bean that genuinely runs faster or slower than the generic guess stops being flagged `too_fast`/`too_restrictive` forever. This is deliberately blended toward *other bags sharing the same roast level*, never toward the current bag's own shot history - blending toward a bag's own history would have let normal bean-aging drift within that bag's life quietly retrain the reference point it's supposed to be measured against, which grind correction (chasing a fixed target) already handles on its own. See `docs/DESIGN.md` Phase 3 for the fuller reasoning, including why this asymmetric treatment is safe here but would be unsafe for the channeling-suspicion threshold.
- **A recurring, unresolved puck-prep problem on the same bag+recipe is no longer met with an endless "repeat the recipe, don't change grind" response.** Once a bag+recipe combination lands on `puck_prep_issue` for `puck_prep_issue_streak_threshold` shots in a row (default 2) with nothing else changed, an occasional bad tamp is no longer a plausible explanation - Barista Assist overrides the usual "repeat, don't touch grind" advice and recommends coarsening the grind by `puck_prep_issue_streak_coarsen_delta` instead, surfaced as a note on the bag's dashboard summary. Both numbers are dashboard-editable (System view → "Connection and control" → "Grind correction").
- **Every grind-correction band boundary/step and the puck-prep-issue streak settings are now dashboard-editable number entities** (System view → "Connection and control" → "Grind correction"), the same pattern already used for the stop-margin and minimum-step settings - seeded from `definitions.yaml`'s defaults, but tunable per-installation without editing YAML.
- **Both shot charts (Live Shot and the Shots view's per-shot detail) now overlay a dashed "idealized" expected-weight line** - flat through pre-infusion, then a straight ramp to target yield at the shot's own expected flow rate - alongside a shaded pre-infusion band and a marker for when the stop command actually fired, so you can see at a glance how far a real pour's shape diverged from what was expected. The flow-rate line is now drawn as a smoothed moving average (display-only - nothing fed into classification or storage) instead of the naturally jumpy raw per-sample values.

### Changed

- **`flow_analysis.py`'s expected-shot-duration formula double-counted (or rather, silently omitted) pre-infusion, systematically making every shot look slower than expected.** `duration_s` is measured from the brew press, so it already includes the full pre-infusion hold, but the expected-duration formula (`target_yield_g / expected_flow_g_s`) never budgeted any pre-infusion time on the expected side. Fixed to `expected_s = preinfusion_s + target_yield_g / expected_flow_g_s`, using each shot's own real pre-infusion duration. This required recalibrating `expected_flow_g_s` itself (1.3 → 1.7 g/s, re-derived from the same James Hoffmann "How I Dial-In Espresso" transcript data the original value came from - see `docs/data/DIAL_IN_RULES.md` Part 4) because the old value was implicitly calibrated around the buggy formula and broke a real, barista-confirmed-healthy shot fixture once the formula was corrected. The same bug existed in the roast-level flow-rate baseline described above (`storage.roast_level_baseline` computed flow rate directly from the pre-infusion-inclusive shot duration); it's now fixed to subtract each pooled shot's own pre-infusion first, so the pooled "observed" rate stays the same post-pre-infusion quantity `expected_flow_g_s` is measured in - otherwise blending would have silently reintroduced the same bias as real shots accumulated. Extensive doc comments were added throughout `flow_analysis.py`, `definitions.yaml`, and `docs/DESIGN.md` making this explicit, specifically to prevent this class of bug recurring.
- **Flavor-taste recommendations no longer reset just because *any* shot was brewed on a bag - only once the recipe actually changed.** They used to be derived from the bag's live/current recipe and reset on every new shot regardless of whether anything had changed; now they're anchored to the specific shot they were originally tagged against, and only invalidate once any of `dose_g`/`target_yield_g`/`temperature_offset_c`/`preinfusion_s` has genuinely moved since - not just the one field the recommendation's own axis happens to touch, since an unrelated change (say, dose) also means "the conditions that earned this recommendation no longer hold" for a yield-based one.
- **Flow-rate/hydraulic classification, grind correction, and flavor correction all now read their tunable numbers from `definitions.yaml` instead of hardcoded Python constants** (`flow_analysis_constants`, `expert_rules`), which is what makes the new dashboard-editable settings above possible, and what let the pre-infusion fix above be validated and shipped as a data change alongside a formula change rather than a silent code edit.
- Internal reorganization, no behavior change: `runtime.py` (previously ~1,850 lines) was split into `runtime_bag.py`/`runtime_entities.py`/`runtime_peripherals.py`/`runtime_shared.py`/`runtime_shot.py` mixins; `definitions.yaml` was split, pulling all Home Assistant entity-wiring metadata out into its own `entities.yaml`; `storage.py`'s `last_shot()` (broad: full shot + joined bag columns, no classification filter) was renamed to `latest_shot_bag()` to stop it being confused with the pre-existing, differently-scoped `latest_shot()` (narrow: classification/recommended-grind/recipe-snapshot columns only); and several now-unreachable v0.1-era upgrade-migration fallbacks (an old `settings` table read, superseded config-flow-option fallbacks for settings that have been dashboard-editable since earlier releases) were deleted, since anyone already running a recent release migrated through them long ago.

### Fixed

- **Viewing the decaf bag's grind tile right after pulling a shot on the normal bag (or vice versa) could show a recommendation computed from the *wrong bag's* last shot.** `BaristaRuntime.last_shot` was scoped globally across the whole installation instead of to the currently selected bag/slot, affecting the `last_yield`/`shot_classification`/`shot_channeling_suspicion`/`recommended_grind` sensors and the Live Shot/Shot History chart's "no active shot" fallback. Fixed by scoping the underlying query to the selected bag and by making a bean-slot switch actually refresh the cache (it previously only refreshed on a recipe edit or a new bag, so switching slots without editing anything left it stuck showing the previously-selected bag's data).
- **The Live Shot card's frozen graph of the last completed shot went blank after every Home Assistant restart**, even though that shot's own metadata (classification, yield, etc.) correctly reloaded from the database. The raw sample points behind that plot were only ever populated in memory when a shot finished during the current runtime session; they're now reloaded from storage on startup too.
- **A scheduled automatic stop could, in a rare race, fire against a different, newer shot than the one it was scheduled for.** `async_stop_at_target` is queued as a background task when the live weight crosses the stop threshold, but didn't check which shot it had actually been scheduled for - if that original shot was instead finalized before the task ran, the stop would still fire later against whatever shot happened to be active by then. It's now pinned to the specific shot id it was scheduled for and no-ops if that shot is no longer the active one.

### Testing

- Added `tests/test_constant_drift.py`, a set of human-inspection-only drift-detection reports (never fail the suite - they print findings for a human to review) that grow more useful as the real-shot fixture set grows: `ConstantDriftReport` re-runs shot classification against every real fixture with each hand-tuned mechanical constant nudged ±20% and flags any fixture whose classification flips; `StopLatencyBucketDriftReport` checks whether the stop-latency bucket cutoff still sits in a genuine low-density gap between real shots' observed latencies; `GrindFlavorConsistencyReport` flags any real fixture whose recorded flavor tag implies the opposite extraction direction from what its own duration-based classification concluded.
- Full suite: 311 tests, all passing (up from 178).

## 0.2.24

### Changed

- **Adding Barista Assist's dashboard resource is now a one-time manual step (see the README's "Add the dashboard once" section) instead of automatic.** The integration used to call `frontend.add_extra_js_url` to register its own cards' JavaScript automatically, but that API injects a `<script>` tag into the server-rendered frontend shell HTML - a different, and in practice unreliable, loading path than a real Lovelace resource (which the already-running frontend fetches and injects itself). This caused cards (especially the Shots view) to intermittently show "Configuration error (timeout)" on both desktop and mobile, roughly 90% of the time on a full restart. Registering a real Lovelace resource programmatically from an integration isn't safe either - a still-open Home Assistant core bug means doing so at the wrong moment can silently wipe out every other Lovelace resource on the system - so the reliable fix is a manual, one-time "Add Resource" step instead of another automatic mechanism.
- **The integration's static file server no longer sends cache headers**, so a browser reload reliably picks up a new version of the bundled JS after an update, without needing to bump a cache-busting query string by hand.

### Fixed

- **"Copy all shot data" silently truncated the export on the Home Assistant Companion app.** The card called the Clipboard API and reported "Copied to clipboard" as long as the call didn't throw - but the Companion app's WebView clipboard can silently cut off a large `writeText()` write without throwing, so the truncated-but-"successful" copy went undetected. The Companion app (detected the same way Home Assistant's own frontend does) now skips the Clipboard API entirely and shows the manual copy-from-textbox fallback right away, instead of only falling back on an actual thrown error.

### Testing

- Full suite: 178 tests, all passing. (No new tests: there's no existing harness in this repo for exercising `__init__.py`'s integration setup flow, which is where this fix lives.)

## 0.2.23

### Added

- **A new "Yield Prediction" subsection on the System view's "Connection and control" card** groups Minimum/Maximum early stop margin under a "Stop at weight = target yield - margin" heading, alongside two new read-only sensors - "Learned latency (normal flow)" and "Learned latency (fast flow)" - showing the two stop-latency estimates that were previously only visible as hidden `status` sensor attributes.
- **Individual shot-history entries now show Total duration, Effective stop margin, and Roaster.** Total duration (from the first brew press to the stop/abort press, next to Pre-infusion) replaces "Ended". Effective stop margin replaces "Stop compensation" and is a new persisted per-shot field recording the actual live-projected margin used at that shot's own automatic stop decision - which can run higher than the configured minimum for a fast-flowing shot - rather than always showing the flat floor value; it's blank for a manually aborted/timed-out shot, since no such margin was ever computed for it.
- **Both shot charts (Live Shot and the Shots view's own per-shot detail) now show second-labeled x-axis ticks, and a hover (desktop) / touch-drag (mobile) tooltip** with elapsed time, weight, and flow at the nearest sample, plus a guideline marking it.

### Changed

- **"Machine pre-infusion" is now always visible and adjustable** on the "Connection and control" card, instead of only while Adapt PI is off - lets you set it up in advance.

### Fixed

- **The Live shot graph's frozen last-shot view scrolled out of sight and disappeared after about a minute.** It used the third-party ApexCharts Card, whose rolling time window always tracks real wall-clock time, not the timestamp of the data it was last given - a completed shot anchored to its own real press time silently drifted out of view as time passed. Replaced with a small bundled custom card (`barista-assist-live-shot-card`, sharing its chart rendering with the Shots view's own per-shot chart) that plots elapsed seconds since the shot's own start instead of real time, so a frozen shot has nothing to do with "now" and can never drift out of view. The ApexCharts Card HACS dependency is no longer needed for this dashboard at all.
- **The "Started" column in the Shots view overflowed and didn't show the time.** Dates there are now formatted as `dd/mm hh:mm:ss` instead of a full locale-dependent string.
- **The old `number.barista_assist_stop_compensation` entity was left behind, permanently unavailable, after last release's rename.** A one-time migration on startup now remaps it in place to the new `early_stop_margin_min` entity (carrying its entity_id/history forward), instead of leaving a greyed-out orphan next to a freshly-created duplicate.
- **The Minimum/Maximum early stop margin entity names overflowed their dashboard row.** The explanatory "(stop at weight = target yield - margin)" text is now a section subheading instead of being appended to each entity's name.

### Testing

- Added tests for the effective-stop-margin recording (including that it's `None` for a manually aborted shot), the roaster/duration fields round-tripping through storage, the elapsed-time-relative shot-plot points, and the two new learned-latency sensors.
- Full suite: 178 tests, all passing (up from 170).

## 0.2.22

### Added

- **A new "Shots" dashboard view lists every stored shot, most recent first.** Click a row to expand it into full recipe/result details and a weight/flow graph of that shot's own raw samples. Each row has a delete button (with a confirmation prompt); deleting a shot also removes its raw samples and updates that bag's estimated remaining beans accordingly. The shot currently brewing can't be deleted.
- **Automatic stop now projects a margin from the shot's own live flow rate, not just a flat number.** Barista Assist still stops once weight reaches `target yield - margin`, but the margin is now the larger of your calibrated "Minimum early stop margin" or a live projection (current smoothed flow rate × an estimated stop latency), clipped to a separately-tunable "Maximum early stop margin". A real shot had overshot from 36g to 47.9g because flow was still accelerating when a flat margin fired - the live projection catches that by raising the margin (never lowering it below your calibrated minimum) once flow runs unusually fast. See the README's "Adaptive stop margin" section for the full design, including the earlier, rejected approaches (deriving latency from the margin setting itself; extrapolating flow acceleration) and why each regressed a real recorded shot.
- **The stop-latency estimate behind that projection is learned from your own machine's shots, not fixed.** It's split into two independently-learned values - one for shots flowing at a normal rate at the moment of the stop decision, one for shots flowing faster - since a shot's own flow rate at that moment predicts almost perfectly how much extra latency it needs (correlation 0.97 across 5 real recorded shots). Each nudges a small step toward what a newly-completed shot on its own side actually needed, so no single unusual shot swings either estimate far, and it keeps improving with real usage rather than being a one-time fit.

### Changed

- **"Stop compensation" is renamed "Minimum early stop margin", and its previous hardcoded 3x ceiling is now its own independently-tunable "Maximum early stop margin" setting** (System view → Connection and control), rather than a fixed multiple of the minimum - raising one no longer silently changes the other. Renaming the underlying entity means Home Assistant creates it fresh on upgrade; your previously-calibrated value carries over automatically (a legacy-key fallback reads the old stored value), but the old `number.barista_assist_stop_compensation` entity is left behind, unavailable, in the entity registry - safe to delete once you've confirmed the new "Minimum early stop margin" entity shows the right value.

### Docs

- Updated the README's "Adaptive stop margin" section and DESIGN.md's dynamic-stop-margin notes for the two-bucket latency model and the independent minimum/maximum settings, replacing the earlier single-latency/3x-ceiling description.

### Testing

- Added tests for the two-bucket learned latency (including bucket isolation - learning from an elevated-flow shot doesn't touch the normal-flow estimate, and vice versa) and the independent minimum/maximum margin settings.
- Full suite: 170 tests, all passing (up from 141).

## 0.2.21

### Changed

- **Adapt PI's meaning was inverted from what its name promised, and has been corrected.** "Adapt PI" now means what it says: enabled (the default) means the app adapts pre-infusion itself, holding the button for the active bag's own Pre-infusion recipe value; disabled means the Barista Express's own built-in pre-infusion runs instead, via a single short tap. This is the reverse of the old `auto_pi` switch (renamed to `adapt_pi`), where enabling it switched to the machine's own default - a real, live-hardware-affecting bug, not just a naming issue. The switch's on/off dashboard labels are now "App-controlled"/"Machine-controlled" to make the direction unambiguous.
- **The machine's own pre-infusion duration is no longer a hardcoded 8-second assumption.** It's now a "Machine pre-infusion" number entity (System view → Connection and control, shown only while Adapt PI is off) that you set to whatever your machine is actually programmed with, and update if you reprogram it.
- **Every shot now logs the pre-infusion duration that was actually used, not always the bag's recipe value.** With Adapt PI off, the export used to show the bag's `preinfusion_s` even though the machine's own (different) pre-infusion is what really ran - "Copy all shot data" and the stored shot record now reflect the true value either way, alongside a new `adapt_pi` field recording which mode the shot ran in.
- **"Barista Express programmed maximum shot duration" and "Stop safety margin" are no longer set from Settings.** They moved to the Barista Assist dashboard's "Connection and control" card as ordinary number entities (matching Stop compensation), so all three brew-safety settings live in one place; existing values already saved via Settings carry over automatically. The Settings dialog now only asks you to confirm the machine limit.

### Fixed

- **A shot with a single noisy scale reading during pre-infusion could be wrongly flagged `invalid_measurement`.** The scale only reports weight in 0.1g steps, so a negligible trickle could occasionally produce one sample whose instantaneous flow rate spiked past the "has flow started?" threshold, even though it immediately dropped back down and no real flow had begun. `t_first_flow_ms` now requires the crossing to hold for at least 300ms before counting it, rather than triggering on a single sample.
- **A shot could be wrongly flagged `disturbance_left_too_few_samples` because of stale leftover scale data, not an actual disturbance.** A live shot's first two samples carried a leftover BLE notification from before the scale's own clock had been reset for this shot (recognizable because its `scale_ms` ran tens of seconds ahead of the shot's own elapsed time) - the drop from that stale reading down to a real 0g then looked like a cup/scale disturbance and truncated the shot down to just those two garbage samples. Leading samples whose scale clock is out of sync with the shot are now recognized and dropped before disturbance detection runs, the same way an implausible negative leading weight already was.
- **A violent, splashy shot could be wrongly classified `too_restrictive` instead of reflecting what actually happened.** Turbulent flow can bounce the scale reading down by a few grams for a sample or two (droplets, crema settling, the cup rocking) before recovering and climbing further - a real shot's very first such bounce was previously enough to trip disturbance detection and truncate everything that followed, hiding a huge overshoot and leaving too little data to ever reach 90% of yield (silently falling back to `too_restrictive`). A drop must now hold for at least a second before it's treated as a genuine disturbance, rather than triggering on the first instantaneous dip.

### Docs

- Corrected the README's Adapt PI section and DESIGN.md's flow-analysis notes for the inverted-semantics fix and the sustained-crossing/sustained-disturbance fixes above.
- Added a "Not implemented yet" note on dynamic, flow-projection-based stop-time adjustment, prompted by a real shot that overshot from 36g to 47.9g because flow was still accelerating when the fixed stop-compensation threshold fired.

### Testing

- Added 8 real, hand-annotated shot exports as regression fixtures (previously 0), covering healthy, too-fast, too-restrictive, channeling, and multiple previously-misclassified-invalid shots, plus direct unit tests for each of the three classifier fixes above.
- Full suite: 141 tests, all passing.

## 0.2.20

### Fixed

- **A failed stop/abort press could leave a shot permanently stuck.** `stop_triggered` is set to `True` right before attempting the press (to stop a second concurrent caller from also pressing), but was never reset back on failure - so once a press failed once (e.g. a transient `BleakOutOfConnectionSlotsError`), every later stop/abort attempt on that shot silently no-op'd forever, including a manual Abort click and the protected-deadline timeout's own safety-net abort. `active_shot` never cleared and Brew never re-enabled, with no way out short of restarting/reloading. `_async_press_stop` now resets the flag on failure, so a retry (manual or automatic) can actually try again - `_actuation_lock`, held for the whole call by both callers, still fully prevents a real concurrent double-press.

### Changed

- **Flow rate is now drawn behind Weight in the "Live shot" graph, at 10% opacity**, so it reads as a subtle backdrop rather than competing with the weight curve for attention.

### Docs

- **Documented that a Raspberry Pi's onboard Bluetooth adapter is a common way to hit the connection-slot limit** described in the SwitchBot requirement section - live testing showed it struggling to hold and toggle between even two BLE connections, with a wedged connection that survived a full Home Assistant restart and needed the Bluetooth integration itself reloaded to clear. Added concrete step-by-step instructions for setting up an ESPHome Bluetooth proxy as the fix.
- Removed leftover commented-out `vertical: true` lines and a stray `fill_raw` option (both dead from earlier iterations of the Live shot graph) from the packaged dashboard.

### Testing

- Added a regression test proving a shot can be retried and successfully aborted after an earlier failed press, instead of silently no-op'ing forever.
- Full suite: 103 tests, all passing.

## 0.2.19

### Changed

- **Reworked "Live shot" again: it now uses the [ApexCharts Card](https://github.com/RomRider/apexcharts-card) (a new HACS dependency - see the README's "Add the dashboard once" section) instead of the built-in `history-graph` used in 0.2.18.** Weight and Flow rate now plot on genuinely separate y-axes (0-60g / 0-6 g/s) rather than needing the `flow_rate_x10` scaling workaround from 0.2.18, which is removed.
- **The graph now shows exactly one shot at a time instead of a rolling wall-clock window.** A new `shot_plot` attribute on the `status` sensor (`BaristaRuntime._shot_plot_points`) holds the active shot's own samples while one is running - so the chart grows live, anchored to when that shot actually started - and freezes on the last completed shot's samples afterward, instead of continuing to scroll with real time and losing the shot off-screen. The chart reads this via ApexCharts' `data_generator`, which bypasses the normal history-window fetch entirely. Capped at 300 points per shot regardless of its length.
- `scale_weight`/`flow_rate` are no longer placed directly in the dashboard (superseded by the chart above) and lost the `requires_active_shot` gating 0.2.18 added for the old approach - both sensors are available under the same conditions as before 0.2.18 (just needing a connected scale) for anyone using them elsewhere (e.g. automations).

### Testing

- Added regression tests for `_shot_plot_points`: empty before any shot has run, growing live during an active shot with real epoch timestamps anchored to `press_wall_time`, and frozen (not cleared) at the last shot's data after finalizing.
- Full suite: 102 tests, all passing.

## 0.2.18

### Fixed

- **The Tare button stayed pressable with no scale connected**, unlike Brew/Abort which both already require one. Added the same `requires_scale` guard.
- **Bean slot's dropdown showed lowercase "normal"/"decaf"** - capitalized to "Normal"/"Decaf". Display-only; the underlying stored slot values are unchanged.

### Changed

- **"Live shot" now shows Weight and Flow rate as a single live history graph** instead of two separate number tiles, so you can see the pour's shape over time (a native `history-graph` card, no new dependency) rather than just the instantaneous value. Flow rate is plotted scaled ×10 (a new `flow_rate_x10` sensor, alongside the existing raw `flow_rate`) so its line doesn't read as flat next to weight's much larger range on the shared axis. The raw `flow_rate` sensor still exists (e.g. for automations) but is no longer placed in the packaged dashboard.

## 0.2.17

### Fixed

- **"Bag details", "Current recipe", and "Connection and control" entity rows showed the device name prefixed onto every label** (e.g. "Barista Assist New bag coffee" instead of "New bag coffee"). Those are `type: entities` cards, which - unlike `tile` cards - render `has_entity_name` entities' raw `friendly_name` (device name + entity name) rather than stripping the device prefix. Added an explicit `name:` override to every row in all three cards.
- **Reworked the Brew view's layout.** Last yield, Shot diagnosis, and Channeling suspicion moved out of "Live shot" into a new "Last brew" section at the bottom, since they describe the *previous* shot, not the one in progress. Stop compensation was removed from the Brew view entirely (it's a global setting, not per-shot - it stays on the System view's "Connection and control" card). The Active bag tile no longer repeats `remaining_g` (already shown by its own "Beans remaining" tile next to it), which also fixed the tile's roast-date text overflowing. The three separate Brew/Tare/Abort button tiles under "Controls" are now one compact entities card instead.

## 0.2.16

### Fixed

- **Auto PI (added in 0.2.15) had no way to actually turn it on.** It shipped as a config-flow option, which isn't a Home Assistant entity and never showed up anywhere in the dashboard. Replaced with `switch.barista_assist_auto_pi`, a real toggle now shown on the System view's "Connection and control" card, persisted the same way as `stop_compensation` (the runtime's own storage, not a config-entry option) rather than requiring a trip to Settings.
- **The Pre-infusion tile now hides via a plain dashboard `visibility` condition** on that same switch, instead of rewriting the generated dashboard YAML file server-side every time the toggle flips - simpler, and it updates instantly in the browser instead of needing a regenerate round-trip.

### Testing

- Updated the Auto PI regression tests to drive the new `runtime.auto_pi` attribute directly instead of a config-entry option, and replaced the dashboard-stripping test with one confirming token substitution reaches inside `visibility` conditions, not just `entity:` keys.
- Full suite: 98 tests, all passing.

## 0.2.15

### Added

- **The `status` sensor now has proper display labels for every value** (e.g. "Connect scale" instead of the raw `connect_scale`) - it never had a `state` translation map at all before, so every status ("idle", "connecting_scale", "manual_stop_required", etc.) showed as its raw snake_case string. Added the full mapping, matching the pattern `shot_classification` already used.
- **New "Auto PI" option**: brew with a single short tap and let the Barista Express run its own built-in pre-infusion (~8s) instead of Barista Assist holding the button for a per-bag duration. When enabled, both the start and stop presses go through Home Assistant's switchbot integration only - the direct-BLE Bot-reprogram step is skipped entirely, since there's no hold duration to configure - and the Pre-infusion tile is hidden from the Recipe section of the dashboard, since its value no longer affects anything. See the README's new "Auto PI" section for the tradeoffs.

## 0.2.14

### Added

- **The `status` sensor now shows `connect_scale` instead of `idle` whenever there's no active shot and the scale isn't connected.** Plain "idle" reads as "everything's fine," but nothing can actually happen (brewing requires a connected scale) until it's reconnected. Purely a display-time override on the existing `status` property - the underlying shot-phase state machine is unaffected, and this never shows mid-shot even if the scale drops out then (that's `manual_stop_required`/the scale-disconnect auto-abort's job, unchanged).

### Fixed

- **The Brew/Tare/Abort/Create-bag tiles still showed a timestamp ("X minutes ago") or "Unavailable" text**, despite `state_content: []` supposedly suppressing it since 0.2.6. Checked Home Assistant's own tile-card docs directly: `state_content: []`'s behavior for an empty list was never actually documented, and the real, documented option for this is a separate `hide_state: true` - switched all four tiles to it.
- **A slow Bluetooth connection to the brew Bot could silently eat into the shot's safety deadline and corrupt its flow-analysis timing.** A live coffee shot showed ~50s of BLE connection delay (a first connect attempt hitting `BleakOutOfConnectionSlotsError`, recovered by the retry-once fix above) counted as if it were part of the shot itself: recorded samples showed a ~60s flat prefix before any real flow, the shot classified as `too_restrictive` even though the puck itself extracted at a completely normal rate once flow actually started, and it got cut off by the safety timeout despite the machine having only been running a normal amount of time. Root cause: `elapsed_ms`/the safety-deadline check were both measured from when brewing was *requested* (`ActiveShot.started_monotonic`), not from when the brew Bot was actually pressed - so any BLE connection delay before that point was silently counted as shot time. Added `ActiveShot.press_monotonic`, set once the initial press actually lands: the safety-deadline check (`async_stop_at_target`/`async_abort`) now measures elapsed time from it instead, and scale readings that arrive before it (i.e. during Bot connection setup) are no longer recorded as part of the shot at all - both the deadline and every sample's timeline now reflect when the machine was actually engaged, not how long Bluetooth took to cooperate.
- **The scale's own physical timer/tare had the identical problem, one level down.** `async_tare_and_start_timer` used to run *before* the brew Bot connection was even attempted, so on a slow connection the scale's own on-device timer - and the zero-weight reference it captures - would already be running for however long Bluetooth took before the machine was actually engaged. Tare and timer-start now happen right after the press lands instead, alongside `press_monotonic`. Since the machine is already pouring by that point, a tare failure there can't fall back to "the shot never happened" like earlier failures in `async_brew` do - it now just logs a warning and continues with an untared baseline rather than aborting a shot that's already physically running.

### Testing

- Added regression tests for the new `connect_scale` status override, including that it doesn't leak into an active shot if the scale drops mid-brew.
- Added regression tests proving readings before the press are dropped (with elapsed time reset relative to the press), that a large gap between request and press doesn't trip the safety deadline, and that the scale is tared/timer-started after the press lands, not before.
- Full suite: 94 tests, all passing.

## 0.2.13

### Fixed

- **Regression in 0.2.12: a SwitchBot Bot connection that just needed a second attempt now failed permanently on the first one.** 0.2.12 fixed a multi-minute stall (see its own changelog entry below) by removing all retrying of a failed connect step - but live testing then showed a *different*, real regression: a connection that's genuinely just transient/marginal rather than truly unavailable, which 0.2.10 would sometimes recover on a second or third attempt, now failed outright every time. Settled on retrying the whole connect-and-configure sequence exactly **once** (two total attempts) - enough to recover a transient case, without reproducing the multi-attempt stall a truly unrecoverable one caused. Worst case for a genuinely unrecoverable failure is now ~2×36s ≈ 72s (was ~36s after 0.2.12's fix, ~108s before it).

### Testing

- Added regression tests proving a hard connect failure is retried exactly once (not zero, not three-plus) and that a connect failure followed by success recovers, plus kept the existing test proving a post-connect disconnect still gets exactly one retry.
- Full suite: 89 tests, all passing.

## 0.2.12

### Fixed

- **Regression in 0.2.11: the brew Bot could become completely unresponsive for a long time, with Brew stuck and Stop/Abort not doing anything.** 0.2.10 wrapped `SwitchBotBotConfigurator.async_set_long_press_duration` entirely in `bleak_retry_connector`'s `retry_bluetooth_connection_error`, intending to retry only a rare, narrow failure (the Bot dropping the link right after a successful connect, before the first GATT operation lands). But `establish_connection()` already retries the connect step internally - its own attempt count can reach the high single digits on constrained hardware - so wrapping the *entire* method multiplied that already-slow retry loop several times over on every genuine failure (e.g. the adapter simply being out of connection slots, which retrying more doesn't fix), live-observed in 0.2.11 as the brew button becoming unresponsive for a long stretch. Restructured so only the narrow case it was meant for (connect succeeds, then the GATT sequence fails) triggers one reconnect-and-retry; a hard failure of the connect step itself now fails immediately.

### Added

- **The scale's own onboard timer now stops when a shot finalizes.** Barista Assist starts it (`async_tare_and_start_timer`) at the beginning of every shot, but never told it to stop, so the scale's own display kept counting up indefinitely after a shot ended instead of freezing at the real shot duration. `BookooUltraClient` gained `async_stop_timer()` (BOOKOO command `0x05`), called from `_async_finalize` - so it only fires once a shot has actually and confidently ended, not on an ambiguous `stop_error`.
- **Added debug/info-level logging across the shot lifecycle and Bot BLE operations**, to make live issues like the one above diagnosable from the log alone: every shot-phase transition, brew/abort/stop entry (with elapsed time and reason), scale connect/disconnect events, Bot connect/program/press attempts (including `_bot_lock` waits and reconnect-retries), and shot finalization. Enable debug logging for `custom_components.barista_assist` to see it (see README's new "Debug logging" section).

### Docs

- The README's `configuration.yaml` example still showed the old `mdi:coffee-maker` sidebar icon after the earlier coffee-maker → coffee-to-go icon sweep - that example is copied into the user's own config, not anything Barista Assist regenerates, so it wasn't touched by that sweep. Updated the example; anyone who already copied it needs to update their own `configuration.yaml` to match (and restart, since YAML-mode dashboard config is only read at startup).

### Testing

- Added a regression test proving a hard connect failure is not retried again (only one `establish_connection()` call), plus kept the existing test proving a post-connect disconnect still gets exactly one retry.
- Added a regression test proving `_async_finalize` stops the scale's own timer.
- Full suite: 88 tests, all passing.

## 0.2.11

### Fixed

- **Brew and stop/abort could race each other's BLE session to the same brew Bot.** `async_brew` serializes under `_shot_lock` and `async_stop_at_target`/`async_abort` serialize under the separate `_actuation_lock` (intentionally - so a fast abort never has to wait behind brew's own slow scale-connect/tare preamble), but both eventually call the same `_async_prepare_brew_bot`/`_async_press_brew_bot`, and nothing previously stopped those from running concurrently against the same physical device from the two different locks. A live shot showed the likely consequence: the stop command fired at the correct target weight, but the pour continued for several more seconds and ~19g past target - consistent with the instant-tap reprogram silently losing a race and the press falling back to holding for the full configured pre-infusion duration. Both methods now also serialize through a dedicated `_bot_lock`. The actual press (`_async_press_brew_bot`, used by brew/stop/abort alike) only waits up to 2s for it before proceeding anyway, since it must never be blocked indefinitely behind a slow/stuck prepare call - it's the one time-critical action that has to happen regardless.

### Added

- **Brew and Abort** now also show as unavailable without a connected scale, alongside the existing bag/active-shot checks - starting a shot (or, per explicit request, stopping one) without the scale that drives the whole workflow doesn't make sense. Note this means Abort can gray out if the scale drops mid-shot; the physical machine and the Bot's own switch entity remain usable regardless.
- **A shot is now auto-aborted (best-effort) if the scale disconnects while it's active**, and its state is cleared once that abort succeeds. Previously a scale dropout left `active_shot` set forever with no way to start a new shot - even after reconnecting the scale - short of restarting Home Assistant, since Brew now also requires an active shot to *not* be set. Like a manual abort, this still correctly leaves the shot in `stop_error` rather than clearing it if the stop press itself also fails, rather than pretending a possibly-still-pouring machine is safely stopped.

### Docs

- **Clarified the shot-duration safety documentation**, confirmed against Breville's own instruction books (BES875, BES878) and live testing: the 1-CUP/2-CUP button has two distinct modes - a single tap starts "Pre-Programmed Shot Volume" mode, auto-stopping at a user-set dose, while press-and-hold (what Barista Assist always uses, to drive pre-infusion) starts "Manual Pre-Infusion & Extraction" mode instead, which the manual doesn't describe as having an equivalent auto-stop. The machine does still appear to have its own cutoff in this mode too - a held water-only test shot stopped itself after ~30s - but third-party reports suggest this is based on total pumped *volume* via an internal flow meter, not elapsed time. Whether that specific 30s is a fixed generic safety timer or tied to whatever volume happens to be programmed for that button (the BES875 manual lists 30ml/60ml as the 1-CUP/2-CUP single-tap defaults, suspiciously close to the water test) is unconfirmed - but either way, how long it takes depends heavily on flow resistance, so a real coffee puck should take meaningfully longer than a water test to hit the same cutoff. Since this isn't documented with full public confidence, `machine_max_shot_seconds` should be set from the user's own measured worst case with real coffee, not a water test. Updated README, `docs/DESIGN.md`, and the in-app options-flow description accordingly.

### Testing

- Added a regression test proving `_async_press_brew_bot` waits for an in-flight `_async_prepare_brew_bot` call rather than racing it.
- Added regression tests for Brew/Abort's new scale-connected requirement.
- Added regression tests for the scale-disconnect auto-abort.
- Full suite: 86 tests, all passing.

## 0.2.10

### Fixed

- **Brewing could still eventually fail to press the SwitchBot Bot** with `BleakDBusError: [org.bluez.Error.NotConnected]`, raised from `start_notify` immediately after `establish_connection` had already reported success - a BLE peripheral is free to drop the link right after connecting, before the first GATT operation lands. `SwitchBotBotConfigurator.async_set_long_press_duration` is now wrapped in `bleak_retry_connector`'s own `retry_bluetooth_connection_error`, which retries the whole connect-use-disconnect sequence (a fresh connection, not just the failed step) - the documented way this library expects a mid-operation disconnect to be handled.
- **A real, completed shot with a normal yield (confirmed live: ~53g from a water test) could still be classified `invalid_measurement`/`near_zero_final_weight`.** `flow_analysis.py`'s disturbance detection (see 0.2.5's changelog entry) treated any >0.5g drop below the running weight peak as the cup/scale being disturbed, with no lower bound - but scale settling noise during pre-infusion, before any real coffee mass has accumulated, can easily span several tenths of a gram on its own. A sub-gram noise dip minutes before the real pour began was truncating away the entire real pour that followed. Disturbance detection now only arms once the running peak clears 2 g, leaving its sensitivity to genuine disturbances during/after a real pour unchanged.

### Added

- **Brew** and **Abort** now correctly show as unavailable/disabled when they don't apply: Brew while a shot is already active, Abort when there's no active shot. Both previously stayed enabled at all times regardless of shot state; pressing them as a no-op wasn't unsafe, but looked like the button was broken. Backed by two new `EntityDefinition` flags, `requires_active_shot`/`requires_no_active_shot`, following the same pattern as the existing `requires_bag`/`requires_scale`.

### Testing

- Added a regression test reproducing the SwitchBot mid-connection disconnect and confirming the whole operation retries via a fresh connection.
- Added regression tests for Brew/Abort availability across shot state transitions.
- Added regression tests reproducing the pre-infusion-noise false positive and confirming a genuine mid/post-pour disturbance is still caught.
- Full suite: 81 tests, all passing.

## 0.2.9

### Fixed

- **`protocol.py` misread the BOOKOO weight/flow sign byte, so nearly every non-zero reading came out negated.** The scale's own protocol doc doesn't name the sign byte's values, and the previous decode treated any non-zero byte as negative - but confirmed against BOOKOO's own reference decoder (`makerwolf/aiobookoo`, the library behind Home Assistant's built-in `bookoo` integration), the sign byte is actually the ASCII character `'+'` (0x2B) or `'-'` (0x2D), both of which are non-zero. So both a genuinely positive and a genuinely negative reading were read as negative, and only an exact-zero magnitude passed through correctly. Live testing confirmed this: the physical scale showed a normal positive weight while Barista Assist's own sensor showed negative for the same moment. This is very likely the root cause of every shot in earlier testing classifying as `invalid_measurement`/`near_zero_final_weight` with `actual_yield_g=0.0` - the flow-analysis system was working correctly against weight data that never actually showed a real rising positive value.
- **A stuck SwitchBot Bot connection attempt could leak a Bluetooth connection slot, eventually causing every future connection to the Bot to fail** (`BleakOutOfConnectionSlotsError`) even on hardware that supports several concurrent BLE connections. `_async_ensure_quick_stop_press` used to cancel an in-flight proactive Bot reprogram and immediately start a fresh one - but `bleak_retry_connector`'s `establish_connection()` has no cancellation cleanup, so cancelling it mid-connect never disconnects the partially-established client, and starting a second connection to the same device on top of that leak compounds the problem with every shot. It now waits (bounded to 3s) for the existing attempt to finish instead of cancelling it, and never starts a competing connection to the same Bot while one is still in flight - falling back to holding for the configured pre-infusion duration on that one press if the wait times out, exactly as it already did for a real failure.

### Testing

- Added `test_unrecognized_sign_byte_is_treated_as_zero` and corrected the two existing packet fixtures (which had been using an invalid sign byte that only worked by coincidence with the old, incorrect decode logic).
- Rewrote `test_fallback_cancels_stuck_proactive_reprogram_instead_of_racing` as `test_fallback_waits_for_stuck_proactive_reprogram_without_racing_it`, matching the new wait-not-cancel behavior.
- Full suite: 76 tests, all passing.

## 0.2.8

### Fixed

- **The actual root cause of the "calls async_write_ha_state from a thread other than the event loop" flood** (thousands of occurrences per session, logged as a RuntimeError, not just a warning): `entity.py`'s dispatcher-connected `_handle_runtime_update` was a plain, undecorated method. Home Assistant's job scheduler treats an undecorated callable passed to `async_dispatcher_connect` as possibly-blocking and defensively runs it in the executor thread pool instead of inline on the event loop - so *every* entity-state dispatch, from any trigger, was being routed off-thread regardless of the 0.2.6 BLE-callback marshaling fix. Marked it `@callback`, which is what actually told Home Assistant it's safe to run inline. This was also the root cause of several once-mysterious symptoms reported during live testing that all trace back to state writes silently failing to reach the frontend: the scale showing "Unavailable" while genuinely connected, the active bag reading "Unknown" after being set, the Bean slot select getting stuck after a second change, and Stop Compensation not staying in sync between Settings and the Live Shot tile.
- A second real BLE thread-safety bug, same class as 0.2.6's: `switchbot.py`'s Bot-response notification callback touched a plain `asyncio.Event`/dict directly from bleak's raw callback thread. Now marshals onto the event loop first, like `bookoo.py` already did.
- `mdi:coffee-bean` (the 0.2.6 "fix" for the Bags tab/active-bag/beans-remaining icons showing blank) turned out to be just as invalid as the `mdi:coffee-beans` it replaced - neither name exists in Material Design Icons. Switched to `mdi:sack`, and added `tests/test_icons.py`, which checks every `mdi:` reference in the repo against a real snapshot of the MDI catalog so an invented icon name can't ship undetected again.
- Every dashboard tile showed its entity's full "Barista Assist <name>" friendly name as its header, which overflows the tile on typical screen widths. Every tile now sets an explicit short `name:` instead.
- Swapped `mdi:coffee-maker` for `mdi:coffee-to-go` across the Brew view, its "Ready to brew" section heading, the System view heading, and the `brew` entity/service icons.

### Docs

- README: documented that Barista Assist needs at least 2 concurrent Bluetooth connection slots (the BOOKOO scale holds one continuously; brewing briefly opens a second to the SwitchBot Bot) - a single adapter/ESPHome proxy that only supports one connection at a time will fail the Bot connection with `BleakOutOfConnectionSlotsError` every time you brew while the scale is connected. This is a Bluetooth capacity limit, not a bug, and Barista Assist already degrades gracefully (falls back to a full-length press instead of an instant tap) when it happens; the fix is adding a second proxy/adapter near the machine.

### Testing

- Added `tests/test_entity.py`, confirming `_handle_runtime_update` is marked as a Home Assistant callback (fails without the fix, verified via `git stash`).
- Added `tests/test_switchbot.py`, reproducing the switchbot.py thread-safety bug the same way `test_bookoo.py` does (fails/hangs against the pre-fix code, verified via `git stash`).
- Added `tests/test_icons.py` (icon-name validation against the real MDI catalog) and a dashboard test asserting every tile declares a `name:`.
- Full suite: 75 tests, all passing.

## 0.2.7

### Changed

- **Replaced the Community Dashboard strategy with a YAML-mode dashboard file.** After shipping, v0.2.6's `Content-Type` fix for the "timeout waiting for strategy element ... to be registered" error turned out not to be the actual cause: further live debugging (including confirming against Home Assistant's own frontend source and community reports) traced it to a bug in Home Assistant 2026.5's own new `window.customStrategies` browser registration mechanism, affecting multiple unrelated projects that use it — not anything specific to this integration's code — and the community's suggested manual-resource workaround didn't reliably fix it on mobile clients either. Rather than depend on a mechanism outside this project's control, the integration now writes a fully token-substituted, `views:`-only dashboard file (`barista_assist_dashboard.yaml`) directly into the Home Assistant config directory on every setup/reload (`websocket.render_dashboard_yaml` / `websocket.async_write_dashboard_file`), for a plain YAML-mode Lovelace dashboard entry to read. This still auto-updates on every release with no browser-side registration step at all, at the cost of one additional one-time `configuration.yaml` edit instead of a menu click — see README's "Add the dashboard once" for the new setup. The now-irrelevant `.js`-`Content-Type` fix from v0.2.6 has been removed along with the dashboard-strategy class and the `barista_assist/get_dashboard` WebSocket command it used; the shot-export card and its WebSocket endpoint are unchanged.

### Testing

- Added `tests/test_dashboard_yaml.py` covering `render_dashboard_yaml`: substitutes entity tokens, emits `views:`-only YAML (no `title`), and leaves non-token strings untouched.
- Full suite passing (see repository `tests/` for the current count).

## 0.2.6

### Fixed

- **`bookoo.py` called `async_write_ha_state` from outside the event loop**, hundreds of times per shot, logged by Home Assistant as a thread-safety violation that "may cause Home Assistant to crash or data to corrupt." `BookooUltraClient` registered its BLE notification and disconnect handlers directly as bleak's raw callbacks, which are not guaranteed to run on the event loop — the exact thread depends on the platform's BLE backend. Both callbacks now marshal onto the event loop via `hass.loop.call_soon_threadsafe` before touching any state, regardless of which thread bleak actually calls them from. Found on a real install during v0.2.5 bring-up.
- `mdi:coffee-beans` isn't a real Material Design Icon (the real name is singular, `mdi:coffee-bean`), so every icon using it rendered blank — including the Bags dashboard tab, `active_bag`, `beans_remaining`, `bean_slot`, and `new_bag_coffee`. Fixed everywhere.
- `dashboard_template()` and `load_definitions()` were each cached once per process (`@lru_cache`), so a HACS update replacing `dashboard.yaml`/`definitions.yaml` had no effect until a full Home Assistant restart — contradicting the documented "takes effect after the integration/Home Assistant reloads" behavior. Both now re-parse automatically whenever the file's mtime changes, with no restart required.
- That reparsing is real file I/O (`read_text`), which Home Assistant flags as a blocking call if it happens directly on the event loop. Every call site that can trigger it (`async_setup`, `async_setup_entry`, and the `barista_assist/get_dashboard` websocket handler) now goes through `hass.async_add_executor_job` first, so a cache-refresh triggered by a live HACS update or a config-entry reload can't block the event loop.
- The Brew/Bags view's action-button tiles (Brew, Tare, Abort, Create bag) showed a live "time since last pressed" counter as their state text, which reads as a odd/confusing default for a momentary action button. Suppressed via `state_content: []`.
- The shot-export card's clipboard-success message said "You can paste it directly here" even though there's nothing on the card to paste into on the success path (that text only makes sense in the Clipboard-API-unavailable fallback, where a textarea does appear). Shortened to "Copied to clipboard."
- Removed a stale "### v0.2.0" implementation note from the System dashboard view.
- **The dashboard strategy could fail to register at all, with "timeout waiting for strategy element ... to be registered" and no other visible error.** `barista-assist-dashboard.js` was served with whatever `Content-Type` the host's system MIME database happens to map `.js` to; on hosts where that's not a recognized JavaScript type, browsers silently refuse to execute a `<script type="module">` served with the wrong type — the script's `customElements.define(...)` call for the strategy then never runs. Explicitly registers `.js` as `text/javascript` at startup instead of depending on the host's configuration.

## 0.2.5

### Added

- Added `flow_analysis.py`: a pure, dependency-free Stage-1 shot-flow classifier (`healthy` / `too_fast` / `too_restrictive` / `puck_prep_issue` / `invalid_measurement`) plus a channeling-suspicion score, per `docs/DESIGN.md`'s diagnostic architecture (sections 8-13). `puck_prep_issue` is judged primarily against fixed mechanical priors so it works from a bag's first shot, and takes priority over the fast/slow duration check (a shot that's both fast and shows a channeling signature is a puck-prep problem to fix first, per section 14). A per-bag baseline, once one exists, can only ever raise that suspicion score, never lower it, so a recurring problem can't normalize itself out of detection. The expected flow rate used for the fast/slow duration check, by contrast, is a genuine Bayesian shrinkage estimate that blends the global prior with a bag's own healthy-shot history and is allowed to fully self-normalize toward that bag's characteristic pace — there's no recurring-problem risk to protect against for "this bag just runs faster/slower than average." `analyze_shot` also flags a shot `invalid_measurement` when its first detected flow is implausible - either never detected despite a real final weight, or detected well before the configured pre-infusion should have ended. The too-fast/too-restrictive duration thresholds are anchored against `docs/DESIGN.md`'s own worked Example A rather than picked arbitrarily. The thresholds remain a single calibration anchor, not derived data (tracked as Phase 3b in `docs/DESIGN.md`). A flow-smoothness/noise check was deliberately not added - every variance-based approach tried also fired on a genuine `puck_prep_issue` shot, so it's left for real recorded scale noise to calibrate against.
- `storage.py` (schema v3) can now persist a shot's flow-analysis result — `classification`, `channeling_suspicion`, and the full feature set as `analysis_json` — and `recent_healthy_features(bag_id)` computes the median late-shot acceleration and flow rate from a bag's recent healthy shots, shaped for `flow_analysis.BaselineFeatures`.
- `runtime.py._async_finalize` now calls `flow_analysis.analyze_shot` for every finalized shot, using the bag's own recent healthy-shot history as the baseline, and persists the result.
- Added `shot_classification` and `shot_channeling_suspicion` sensors (new tiles on the Brew view's Live shot section), so every shot's flow diagnosis is now visible. `puck_prep_issue` displays as "Puck prep issue" via a proper state-translation entry, not the raw stored value.
- `export_shots_text` now includes `classification`, `channeling_suspicion`, and the full `analysis_json` in each shot's metadata block, so exported traces carry their diagnosis for future re-analysis.
- `analyze_shot` now detects the cup or scale being disturbed (lifted, bumped, moved) anywhere in the trace — raw weight can only rise during a real pour, so a meaningful drop below its own running peak is unambiguous interference — and discards everything from that point on before classifying the trustworthy prefix. No timestamp-based cutoff or user awareness of "when it's safe to touch the cup" is needed.
- Every `invalid_measurement` shot now carries an `invalid_reason` (too few samples, near-zero final weight, no detected flow, flow starting before pre-infusion should have ended, or a disturbance leaving too little trustworthy data), and `runtime.py` logs it — so an invalid shot can be diagnosed (e.g. a BLE dropout vs. a disturbed cup) instead of showing up as an unexplained `invalid_measurement`.

### Changed

- Reorganized `runtime.py` into clearly labeled sections and removed several duplicated code paths (background-task cancellation, the stop/abort press-and-raise handling, `entity_value`'s source dispatch, and `async_new_bag`'s per-field validation) with no behavior change.

### Fixed

- The README's "Not implemented yet" list still claimed dynamic SwitchBot long-press programming was missing; it shipped back in 0.2.1. Removed the stale entry.

### Docs

- `docs/DESIGN.md`: marked Phases 1-2 as implemented, recorded Phase 3's actual status, and added Phase 3b (deriving thresholds from real shot data) as a named follow-up. Also documented why a late abort waits for the machine's own timer before finalising the shot log instead of stopping immediately.

## 0.2.4

### Fixed

- Fixed a fresh-install failure on Home Assistant 2026.8: `manifest.json` pinned `bleak-retry-connector==4.6.0`, which conflicts with HA 2026.8's own constraint of `bleak-retry-connector==4.6.3` for that shared dependency. HA's requirements installer failed with `RequirementsNotFound`, and the frontend surfaced it as "Config flow could not be loaded: 500 internal server error." The pin now matches what HA 2026.8 already requires.

### Repository

- Removed the redundant top-level `VERSION` file; nothing read it, and `manifest.json`'s `version` field (already read by `const.py` at runtime) is now the single place that needs bumping per release. `PUBLISHING.md`'s release commands are parametrized with an exported `$VERSION` accordingly.
- Removed the current version number from README's section headers ("Architecture", "What this integration does", "Not implemented yet" (renamed from "Deliberately not in..."), "SwitchBot requirement") so the README doesn't need editing purely to stay in sync with a release.

## 0.2.3

### Fixed

- **Automatic stop-at-target-weight never actually pressed the brew button.** A flag reused for two purposes (scheduling the stop vs. confirming it happened) made the auto-stop task a no-op every time — the shot would keep pouring past its target. Also silently disabled the timeout safety net and manual abort once triggered.
- **A failed stop/abort press could be reported as a successful one.** `abort` swallowed a failed brew-button press and finalized the shot anyway, even though the machine may still have been pouring unattended.
- **The stop/abort press held for the full pre-infusion duration instead of an instant tap**, because nothing reprogrammed the SwitchBot Bot's stored press-hold time back down before pressing to stop. Added a proactive reprogram once extraction begins, with a race-safe fallback (cancel-and-retry, not a shared lock) so the fallback can't itself introduce a concurrent-BLE-connection race or block the urgent press behind non-urgent prep work.
- **Config options flow accepted a stale/removed brew SwitchBot entity** without validation, unlike initial setup; it now validates the same way. Fixed a possible crash (`KeyError`) in initial setup if the BOOKOO scale stops advertising between showing and submitting the form.
- **`scale_battery` sensor stayed "available" showing a frozen reading forever after the scale disconnected** — it now correctly goes unavailable like its sibling sensors.
- **The Brew button never went unavailable with no bag selected.**
- **Bag creation only validated the grind field**, so an out-of-range value for dose, target yield, temperature offset, or pre-infusion could be stored unvalidated and later crash the temperature-offset select entity. All recipe fields are now validated.
- Removed an unreachable dead-code safety check in the config flow (the selector bounds already made it impossible to trigger) and completed missing `options.error` translations.
- The dashboard's WebSocket endpoint no longer blocks the event loop reading/parsing its YAML template on the first request after a restart; the cache is now warmed at startup like `definitions.yaml` already was.
- The shot-data clipboard-copy fallback (when the Clipboard API is unavailable, e.g. in the companion app) now shows the export text in a visible, selectable field instead of an unreliable scripted copy across the shadow-DOM boundary.
- Replaced `enum.StrEnum` (Python 3.11+ only) with a Python 3.10-compatible equivalent.

### Tests

- Added a runtime state-machine test harness (fakes for `hass`/BLE, no real Home Assistant install required) covering the shot-control fixes above, plus a `services.yaml`/`definitions.yaml` consistency check. Suite grew from 21 to 37 tests.

## 0.2.2

- Keep `PUBLISHING.md` in the source/release archive while retaining it in `.gitignore` so local publishing instructions are not committed to GitHub.
- Add a package-managed **Copy all shot data** card to the Brew view.
- Add a WebSocket export endpoint that returns all persisted shot metadata and raw scale samples as paste-friendly text.
- Mark samples recorded at or after the stop command with `post_stop=1` to make late scale movement and settling artefacts easy to inspect.

## 0.2.1

- Implement real SwitchBot Bot long-press programming for per-bag pre-infusion using the published BLE protocol.
- Add a user-confirmed Barista Express programmed maximum shot-duration safety limit plus configurable stop margin.
- Prevent target-stop and abort commands from pressing the brew button after the protected window, avoiding accidental new shots.
- Add explicit `manual_stop_required` state for late aborts.
- Keep stable `barista_assist.brew`, `barista_assist.abort`, `barista_assist.tare`, and `barista_assist.select_slot` actions.
- Keep local `PUBLISHING.md` in the repository workspace, gitignored and excluded from release archives.


## v0.2.0 - 2026-08-19

### Changed

- Refactored user-facing entity metadata, ranges, defaults, mappings, slots and dashboard tokens into `definitions.yaml`.
- Replaced one-class-per-field entity implementations with generic declarative entity adapters.
- Reduced the custom WebSocket API to the single endpoint required by the Community Dashboard strategy.
- Kept the stable `barista_assist.brew`, `barista_assist.abort`, `barista_assist.tare` and `barista_assist.select_slot` Home Assistant actions.
- Made Barista Assist explicitly single-config-entry.
- Moved entity names to custom-integration translations and icons to `icons.json`.
- Simplified the dashboard YAML and removed repeated entity names/icons where entity metadata already supplies them.
- Changed first-bag default target yield from 38 g to **36 g** for an 18 g / 1:2 flat-white starting recipe.
- Moved pre-infusion from an integration-wide setting into each physical bag's recipe.
- Moved selected slot and stop compensation out of SQLite history storage into lightweight Home Assistant state storage.
- Stopped persisting unfinished new-bag form fields.
- Moved SQLite schema DDL into versioned `.sql` migration files.
- Added partial recipe-field updates instead of rewriting an entire recipe for each numeric change.
- Replaced the custom runtime listener set with Home Assistant dispatcher updates.
- Removed the unused runtime snapshot/recent-shot cache from the UI path.
- Version is now read from `manifest.json` by runtime code rather than duplicated in Python constants.

### Migration

- Existing v0.1.x bag and shot history is retained.
- Database schema migrates from v1 to v2 by adding per-bag `preinfusion_s`.
- The old global PI is copied to existing active bags during migration.
- Legacy selected slot and stop compensation are adopted into the new application state on first v0.2 load.

### Tests

- Added definitions/default validation.
- Added dashboard-token consistency checks.
- Added partial recipe update coverage.
- Added explicit v0.1 -> v0.2 SQLite migration coverage.

## v0.1.1 - 2026-08-16

- Moved the visible dashboard to package-owned Lovelace YAML.
- Added Community Dashboard strategy registration.
- Added dashboard-facing recipe and bag entities.

## v0.1.0 - 2026-08-16

- Initial self-contained Barista Assist custom integration.
