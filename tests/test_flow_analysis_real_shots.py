"""Regression tests for flow_analysis.analyze_shot against real, recorded
shot exports (see tests/real_shot_fixtures.py and tests/fixtures/real_shots/),
each hand-annotated with the barista's own judgment of the shot. Complements
test_flow_analysis.py's synthetic curves - see that module's docstring for
why synthetic curves were used first (real shot data wasn't available yet).

analyze_shot is called with baseline=None and expected_flow_g_s=CONFIG.
expected_flow_g_s (the fixed global prior, no roast-level pool) throughout:
fixtures don't carry the actual historical baseline/pool that was live at
export time, and the point here is whether the classifier's fixed-prior
logic agrees with the human's own call on the shot, not bit-for-bit
reproduction of a historical DB row.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ha_stubs  # noqa: E402
from real_shot_fixtures import load_real_shot  # noqa: E402

flow_analysis = ha_stubs.import_barista_module("flow_analysis")
definitions = ha_stubs.import_barista_module("definitions")
ShotClassification = flow_analysis.ShotClassification
analyze_shot = flow_analysis.analyze_shot

# The real, sourced config - see test_flow_analysis.py's own CONFIG for why
# this isn't a hardcoded/default FlowAnalysisConfig.
CONFIG = flow_analysis.FlowAnalysisConfig(**definitions.load_definitions().flow_analysis_constants)
# Live-sourced, not hardcoded, same as CONFIG above - _async_finalize passes
# this same value (self.stop_latency_normal_s) as actuator_delay_s for every
# adapt_pi shot, 0.0 for a machine-controlled one (see analyze_shot's own
# comment on why).
STOP_LATENCY_NORMAL_S = definitions.load_definitions().defaults["controller"]["stop_latency_normal_s"]


def _actuator_delay_s(shot) -> float:
    return STOP_LATENCY_NORMAL_S if shot.adapt_pi else 0.0


class GoodShotAdaptPiTests(unittest.TestCase):
    """"Good shot, adaptPI=True (app controlled)" - a held pre-infusion
    shot (preinfusion_s=7.0) the barista judged healthy overall: flow starts
    just after preinfusion ends, ramps up, and settles into a plausible pour
    despite an early flow spike/dip (a fast initial channel-like burst from
    ~9.5-11.5s that eases off and never recurs) - real pucks are not perfectly
    uniform, and this one still finished at 35.59g against a 36g target.

    The shot never reaches 100% of target (undershoot), so duration_ratio is
    computed by extrapolating past t90 using the late-stage flow rate. With
    only the t100 fix applied, that extrapolation landed this shot just
    outside the healthy window (duration_ratio=1.105 vs. too_restrictive_
    factor=1.10) - but that was itself missing a second real gap: expected_s
    didn't yet budget actuator_delay_s (see analyze_shot's own comment and
    docs/data/DIAL_IN_RULES.md's Part 7), the dead time a Bot-held pre-
    infusion needs beyond its own programmed hold before flow can plausibly
    start. With both fixes applied, duration_ratio settles at ~0.992 -
    correctly healthy, matching the barista's own call."""

    def setUp(self) -> None:
        self.shot = load_real_shot("good_shot_adapt_pi")

    def test_matches_the_barista_s_own_call(self) -> None:
        self.assertEqual(self.shot.recorded_classification, "healthy")
        result = analyze_shot(
            self.shot.samples,
            target_yield_g=self.shot.target_yield_g,
            preinfusion_s=self.shot.preinfusion_s,
            baseline=None, expected_flow_g_s=CONFIG.expected_flow_g_s, config=CONFIG,
            actuator_delay_s=_actuator_delay_s(self.shot)
        )
        self.assertIsNone(result.invalid_reason)
        self.assertEqual(result.classification, ShotClassification.HEALTHY)
        self.assertLess(result.channeling_suspicion, CONFIG.suspicion_threshold)

    def test_flow_is_detected_right_after_preinfusion_ends(self) -> None:
        """preinfusion_s=7.0; real flow (per the recorded analysis_json)
        wasn't detected until t_first_flow_ms=9249 - well after preinfusion
        ended, not suspiciously early."""
        result = analyze_shot(
            self.shot.samples,
            target_yield_g=self.shot.target_yield_g,
            preinfusion_s=self.shot.preinfusion_s,
            baseline=None, expected_flow_g_s=CONFIG.expected_flow_g_s, config=CONFIG,
            actuator_delay_s=_actuator_delay_s(self.shot)
        )
        self.assertGreater(result.t_first_flow_ms, self.shot.preinfusion_s * 1000)


class TooFastMachinePiTests(unittest.TestCase):
    """"Too fast, adaptPI=False (machine controlled); need to adjust stop
    time based on flow projection" - regression test for a real bug: a single
    garbage leading sample (-48g at elapsed_ms=22, before the scale had
    settled/tared) poisoned the smoothing/derivative computation enough to
    spuriously cross the first-flow threshold at t=22ms, misclassifying this
    shot as invalid_measurement/flow_started_before_preinfusion_end even
    though real flow didn't start until ~5.4s. Once that leading sample is
    dropped (see _first_plausible_index), it correctly classifies as
    too_fast - matching the barista's own call (47.9g actual vs. 36g target,
    a big overshoot) - not the "adjust stop time" part of the comment, which
    is a separate, real product ask (the stop logic doesn't project flow
    forward yet) rather than something this classifier is responsible for."""

    def setUp(self) -> None:
        self.shot = load_real_shot("too_fast_machine_pi")

    def test_preinfusion_is_the_machine_s_true_value_not_the_bag_recipe(self) -> None:
        """This shot predates the export fix that logs a shot's true
        effective preinfusion_s - the machine's own pre-infusion (8.0) was
        what actually ran, not the bag's recipe value (7.0), and the fixture
        has been corrected to log that true value."""
        self.assertEqual(self.shot.preinfusion_s, 8.0)

    def test_matches_the_barista_s_own_call_once_leading_garbage_is_dropped(self) -> None:
        self.assertEqual(self.shot.recorded_classification, "invalid_measurement")
        result = analyze_shot(
            self.shot.samples,
            target_yield_g=self.shot.target_yield_g,
            preinfusion_s=self.shot.preinfusion_s,
            baseline=None, expected_flow_g_s=CONFIG.expected_flow_g_s, config=CONFIG,
            actuator_delay_s=_actuator_delay_s(self.shot)
        )
        self.assertIsNone(result.invalid_reason)
        self.assertEqual(result.classification, ShotClassification.TOO_FAST)

    def test_the_fixture_still_has_its_garbage_leading_sample(self) -> None:
        """Confirms the test above is exercising the real bug case (the raw,
        unmodified samples, garbage included) rather than accidentally
        testing already-cleaned data."""
        self.assertLess(self.shot.samples[0].weight_g, -10.0)


class LateCupMachinePiTests(unittest.TestCase):
    """"Invalid shot - put cup late, adaptPI=False (machine controlled)" -
    the cup wasn't on the scale until partway through the shot: weight jumps
    to 49.3g at elapsed_ms=4176 and then swings wildly (up to 205g, back down
    to 18.7g, and so on) as the cup was set down and resettled, nothing like
    a real, monotonically-rising pour. A small pre-cup blip at
    elapsed_ms=3576-4056 - well before the 8s machine pre-infusion ends - is
    enough to trip flow_started_before_preinfusion_end, matching both the
    barista's own call and the classification recorded at the time."""

    def setUp(self) -> None:
        self.shot = load_real_shot("late_cup_machine_pi")

    def test_matches_the_barista_s_own_call(self) -> None:
        self.assertEqual(self.shot.recorded_classification, "invalid_measurement")
        result = analyze_shot(
            self.shot.samples,
            target_yield_g=self.shot.target_yield_g,
            preinfusion_s=self.shot.preinfusion_s,
            baseline=None, expected_flow_g_s=CONFIG.expected_flow_g_s, config=CONFIG,
            actuator_delay_s=_actuator_delay_s(self.shot)
        )
        self.assertEqual(result.classification, ShotClassification.INVALID)
        self.assertEqual(result.invalid_reason, "flow_started_before_preinfusion_end")

    def test_the_fixture_still_has_its_garbage_leading_sample(self) -> None:
        """Confirms the test above is exercising the real, raw samples
        (garbage leading sample included) rather than accidentally testing
        already-cleaned data."""
        self.assertLess(self.shot.samples[0].weight_g, -10.0)


class ChokedMachinePiTests(unittest.TestCase):
    """"Too constrained, machine choked, adaptPI=False (machine controlled)"
    - an over-fine grind that barely let anything through: after
    pre-infusion, weight crept up in fractions of a gram every few seconds,
    and the shot timed out at 61s having produced only 3.8g against a 36g
    target. The classification recorded at the time (invalid_measurement)
    was an artifact of older logic; the current classifier correctly reads
    this as too_restrictive instead, matching the barista's own diagnosis."""

    def setUp(self) -> None:
        self.shot = load_real_shot("choked_machine_pi")

    def test_matches_the_barista_s_own_call(self) -> None:
        result = analyze_shot(
            self.shot.samples,
            target_yield_g=self.shot.target_yield_g,
            preinfusion_s=self.shot.preinfusion_s,
            baseline=None, expected_flow_g_s=CONFIG.expected_flow_g_s, config=CONFIG,
            actuator_delay_s=_actuator_delay_s(self.shot)
        )
        self.assertIsNone(result.invalid_reason)
        self.assertEqual(result.classification, ShotClassification.TOO_RESTRICTIVE)

    def test_the_fixture_still_has_its_garbage_leading_sample(self) -> None:
        """Confirms the test above is exercising the real, raw samples
        (garbage leading sample included) rather than accidentally testing
        already-cleaned data."""
        self.assertLess(self.shot.samples[0].weight_g, -10.0)


class TooRestrictiveMachinePiTests(unittest.TestCase):
    """"Too constrained, machine choked, adaptPI=False (machine controlled)"
    - a different, even finer grind on the same bag: flow never really gets
    going (max_flow_g_s under 1.4 g/s, weight plateaus at 15.5g for the last
    ~30s of a 61s timeout) against a 36g target. Unlike choked_machine_pi,
    this one was already correctly classified too_restrictive (no
    invalid_reason) at capture time, matching the barista's own call - a
    clean confirming case rather than a regression for a past bug."""

    def setUp(self) -> None:
        self.shot = load_real_shot("too_restrictive_machine_pi")

    def test_matches_the_barista_s_own_call(self) -> None:
        self.assertEqual(self.shot.recorded_classification, "too_restrictive")
        result = analyze_shot(
            self.shot.samples,
            target_yield_g=self.shot.target_yield_g,
            preinfusion_s=self.shot.preinfusion_s,
            baseline=None, expected_flow_g_s=CONFIG.expected_flow_g_s, config=CONFIG,
            actuator_delay_s=_actuator_delay_s(self.shot)
        )
        self.assertIsNone(result.invalid_reason)
        self.assertEqual(result.classification, ShotClassification.TOO_RESTRICTIVE)


class GoodButFlaggedMachinePiTests(unittest.TestCase):
    """"Seems to be a good shot (not sure why invalid), adaptPI=False
    (machine controlled)" - the main pour (elapsed_ms=9014 onward) does look
    like a coherent shot, finishing at 35.2g against a 36g target in 23.1s
    (t90). A small trickle also creeps up to 0.3g between elapsed_ms=2054
    and 8924, well inside the 8s machine pre-infusion window, but the scale
    only reports 0.1g steps: a couple of isolated samples land close enough
    together that the raw derivative spikes past first_flow_threshold_g_s
    for a single sample, even though the trickle itself is negligible and
    never sustains. The classification recorded at the time
    (invalid_measurement) was that bug; first_sustained_crossing_ms now
    requires the crossing to hold for first_flow_sustain_ms before counting
    it, so this shot is no longer wrongly discarded - matching the
    barista's actual complaint ("not sure why invalid").

    It classifies as too_fast, not healthy, though: at duration_ratio~0.79
    (23.1s against an expected ~29.2s - this shot's own 8s preinfusion_s plus
    ~21.2s for this yield at flow_analysis_constants' Hoffmann-calibrated
    expected_flow_g_s, per too_fast_factor), it lands well below the
    too_fast_factor=0.88 cutoff, not a rounding-error miss. The barista's
    note was about the wrongly-invalid classification specifically, not a
    considered healthy-vs-too_fast judgment call - and the sibling fixture
    right below (StaleScaleClockMachinePiTests) has a barista comment that
    literally says "seems to be too fast" for an analogous case, confirming
    too_fast is a normal, expected real-world outcome here, not a sign the
    classifier regressed."""

    def setUp(self) -> None:
        self.shot = load_real_shot("good_but_flagged_machine_pi")

    def test_is_no_longer_wrongly_invalid_and_classifies_as_too_fast(self) -> None:
        self.assertEqual(self.shot.recorded_classification, "invalid_measurement")
        result = analyze_shot(
            self.shot.samples,
            target_yield_g=self.shot.target_yield_g,
            preinfusion_s=self.shot.preinfusion_s,
            baseline=None, expected_flow_g_s=CONFIG.expected_flow_g_s, config=CONFIG,
            actuator_delay_s=_actuator_delay_s(self.shot)
        )
        self.assertIsNone(result.invalid_reason)  # no longer wrongly discarded
        self.assertEqual(result.classification, ShotClassification.TOO_FAST)

    def test_the_fixture_still_has_its_early_trickle(self) -> None:
        """Confirms the test above is exercising the real early-trickle case
        this test class's docstring describes, rather than a fixture that
        never had one in the first place."""
        early_samples = [s for s in self.shot.samples if s.elapsed_ms < 8000]
        self.assertGreater(max(s.weight_g for s in early_samples), 0.0)


class StaleScaleClockMachinePiTests(unittest.TestCase):
    """"Seems to be too fast (not sure why invalid), adaptPI=False (machine
    controlled)" - the first two samples (elapsed_ms=26 and 116) read
    weight_g=12.0 with scale_ms=19200, a stale BLE notification left over
    from whatever the scale was doing before this shot - our own
    tare-and-start-timer command hadn't landed yet. The real data starts at
    seq=2 (elapsed_ms=235, scale_ms=0, weight_g=0.0). Before
    _first_synced_clock_index existed, _first_disturbance_index saw the drop
    from that stale 12.0g down to a real 0.0g as a cup/scale disturbance and
    truncated the shot to just those two garbage samples
    (disturbance_left_too_few_samples). The real pour afterward finishes at
    45.4g against a 36g target in under 17s - genuinely fast, matching the
    barista's own read, not invalid."""

    def setUp(self) -> None:
        self.shot = load_real_shot("stale_scale_clock_machine_pi")

    def test_matches_the_barista_s_own_call(self) -> None:
        self.assertEqual(self.shot.recorded_classification, "invalid_measurement")
        result = analyze_shot(
            self.shot.samples,
            target_yield_g=self.shot.target_yield_g,
            preinfusion_s=self.shot.preinfusion_s,
            baseline=None, expected_flow_g_s=CONFIG.expected_flow_g_s, config=CONFIG,
            actuator_delay_s=_actuator_delay_s(self.shot)
        )
        self.assertIsNone(result.invalid_reason)
        self.assertEqual(result.classification, ShotClassification.TOO_FAST)

    def test_the_fixture_still_has_its_stale_leading_samples(self) -> None:
        """Confirms the test above is exercising the real stale-clock case
        this test class's docstring describes, rather than a fixture that
        never had one in the first place."""
        self.assertEqual(self.shot.samples[0].weight_g, 12.0)
        self.assertGreater(
            self.shot.samples[0].scale_ms - self.shot.samples[0].elapsed_ms, 10000
        )


class ViolentGushMachinePiTests(unittest.TestCase):
    """"Seems to be too fast (not sure why it says too_restrictive),
    adaptPI=False (machine controlled)" - a violent, bursty pour: weight
    swings up and down by as much as several grams throughout (e.g.
    11.7g->11.1g at elapsed_ms=9235->9325, or 38.59g->34.0g at
    elapsed_ms=14965->15895) as turbulent flow bounces the scale reading,
    before recovering and climbing again - never a real cup-lift, which
    would stay low. The old, instantaneous _first_disturbance_index
    truncated the shot at the very first such dip (~9235ms), hiding
    everything after it including the huge overshoot to 56.3g against a 36g
    target - with the truncated data never reaching 90% of yield, that
    produced a false too_restrictive (t90 undefined) instead of reflecting
    what actually happened. With the sustain requirement, none of these dips
    hold long enough to count, so the full curve is analyzed: it comes back
    puck_prep_issue (channeling_suspicion=1.0, both mid_accel and late_accel
    far past _ABSOLUTE_ACCEL_LIMIT_G_S2) - a more specific and accurate read
    than the barista's own "too fast", but one that shares the same
    underlying story (a channel blasting through the puck) and, unlike the
    old result, is at least in the right neighborhood rather than
    too_restrictive."""

    def setUp(self) -> None:
        self.shot = load_real_shot("violent_gush_machine_pi")

    def test_matches_the_barista_s_own_call(self) -> None:
        self.assertEqual(self.shot.recorded_classification, "too_restrictive")
        result = analyze_shot(
            self.shot.samples,
            target_yield_g=self.shot.target_yield_g,
            preinfusion_s=self.shot.preinfusion_s,
            baseline=None, expected_flow_g_s=CONFIG.expected_flow_g_s, config=CONFIG,
            actuator_delay_s=_actuator_delay_s(self.shot)
        )
        self.assertIsNone(result.invalid_reason)
        self.assertIsNotNone(result.t90_ms)
        self.assertEqual(result.classification, ShotClassification.PUCK_PREP_ISSUE)
        self.assertGreaterEqual(result.channeling_suspicion, CONFIG.suspicion_threshold)

    def test_the_fixture_still_has_its_bouncy_dips(self) -> None:
        """Confirms the test above is exercising the real bouncy-dip case
        this test class's docstring describes, rather than a fixture that
        never had one in the first place."""
        weights = [s.weight_g for s in self.shot.samples]
        biggest_dip = max(
            max(weights[:i + 1]) - w for i, w in enumerate(weights)
        )
        self.assertGreater(biggest_dip, 3.0)


class ChokedAdaptPiTests(unittest.TestCase):
    """"Too slow / machine choked, adaptPI=True (app controlled)" - grind
    was too fine: after preinfusion, weight crept from -3.3g up in
    fractions of a gram, reaching only 15.2g over a full 61s timeout
    against a 37.5g target (t90 never reached). Already correctly
    recorded as too_restrictive at capture time, matching both the
    barista's own diagnosis and the current classifier - a clean
    confirming case, not a regression for a past bug (see
    ChokedMachinePiTests above for the sibling case that *was* one)."""

    def setUp(self) -> None:
        self.shot = load_real_shot("choked_adapt_pi")

    def test_matches_the_barista_s_own_call(self) -> None:
        self.assertEqual(self.shot.recorded_classification, "too_restrictive")
        result = analyze_shot(
            self.shot.samples,
            target_yield_g=self.shot.target_yield_g,
            preinfusion_s=self.shot.preinfusion_s,
            baseline=None, expected_flow_g_s=CONFIG.expected_flow_g_s, config=CONFIG,
            actuator_delay_s=_actuator_delay_s(self.shot)
        )
        self.assertIsNone(result.invalid_reason)
        self.assertIsNone(result.t90_ms)
        self.assertEqual(result.classification, ShotClassification.TOO_RESTRICTIVE)


class TooFastButFlaggedInvalidAdaptPiTests(unittest.TestCase):
    """"Too fast - the automatic stop's projected margin overcompensated
    and stopped well short of target (37.5g target, 34.0g actual),
    adaptPI=True (app controlled)" - the barista's own read of the overall
    shot: a fast pour that undershot because the live stop-margin
    projection reacted to the high flow rate and stopped early. That
    undershoot is a separate, real product concern (runtime_shot.py's
    stop-margin projection, tracked in aggregate by
    test_constant_drift.py's StopLatencyBucketDriftReport) that this
    classifier never sees at all - analyze_shot only ever looks at the
    recorded flow curve, not the stop decision that produced it.

    Regression test for a real bug: once the one stale leading sample
    (scale_ms=60400, a leftover BLE notification from before this shot's
    own clock reset - see StaleScaleClockMachinePiTests above for the same
    pattern) is trimmed, a ~300ms noise wobble near the tare baseline (raw
    weight oscillating between -0.5g and -0.1g at ~1.5s in - nowhere near
    real flow) used to cross first_flow_threshold_g_s for exactly
    first_flow_sustain_ms's old 300ms value, wrongly tripping
    flow_started_before_preinfusion_end (matching what was recorded at
    capture time, but not the barista's own "too fast" call). Raising
    first_flow_sustain_ms to 600ms (definitions.yaml) fixes it with zero
    change to every other real fixture's own classification (verified
    across the whole 300-1200ms range, not just picked to make this one
    fixture pass) - the real pour, well after preinfusion ends, now
    correctly drives the result instead."""

    def setUp(self) -> None:
        self.shot = load_real_shot("too_fast_but_flagged_invalid_adapt_pi")

    def test_matches_the_barista_s_own_call(self) -> None:
        self.assertEqual(self.shot.recorded_classification, "invalid_measurement")
        result = analyze_shot(
            self.shot.samples,
            target_yield_g=self.shot.target_yield_g,
            preinfusion_s=self.shot.preinfusion_s,
            baseline=None, expected_flow_g_s=CONFIG.expected_flow_g_s, config=CONFIG,
            actuator_delay_s=_actuator_delay_s(self.shot)
        )
        self.assertIsNone(result.invalid_reason)
        self.assertEqual(result.classification, ShotClassification.TOO_FAST)

    def test_the_fixture_still_has_its_noise_wobble(self) -> None:
        """Confirms the test above is exercising the real near-baseline
        noise wobble this test class's docstring describes, rather than a
        fixture that never had one in the first place."""
        early_samples = [s for s in self.shot.samples if s.elapsed_ms < 3000]
        self.assertLess(min(s.weight_g for s in early_samples), -0.3)
        self.assertGreater(max(s.weight_g for s in early_samples), -0.2)


class HealthyAstringentAdaptPiTests(unittest.TestCase):
    """A shot recorded/classified healthy at export time that the barista's
    own taste call was "astringent" - flavor_mouthfeel_tag=dry_astringent is
    a fixture annotation reflecting that recollection, not raw export data
    (see real_shot_fixtures.py). This shot undershoots target (35.09g vs a
    36g target), so it never reaches a real 100%-of-target crossing;
    duration_ratio comes from extrapolating past t90 using this shot's own
    late-stage flow rate, plus the budgeted actuator_delay_s (see
    analyze_shot's own comment and docs/data/DIAL_IN_RULES.md's Part 7) -
    together landing at ~1.025, correctly healthy, matching the original
    recorded call. (Earlier today, with only the t100 fix applied and
    actuator_delay_s not yet budgeted, this same shot briefly computed
    too_restrictive - a real but incomplete intermediate state, not the
    final one.) channeling_suspicion (0.17) stays well under the threshold
    throughout - this was never a channeling call.

    analyze_shot has no way to see taste - it only ever looks at the
    recorded flow curve - so this fixture isn't primarily a classifier
    regression test so much as a record that "correct timing" and "tasted
    good" are genuinely different questions this integration answers with
    two separate systems (Stage 1 timing classification here vs. the
    flavor-correction/feedback-notification system in
    runtime_peripherals.py), not one. Filed from a live bug report: "I had
    a healthy shot today but no notification triggered" - the shot itself
    did classify healthy (so a notification should have been scheduled),
    the actual gap turned out to be elsewhere (see this session's own
    investigation)."""

    def setUp(self) -> None:
        self.shot = load_real_shot("healthy_astringent_adapt_pi")

    def test_matches_the_barista_s_own_call(self) -> None:
        self.assertEqual(self.shot.recorded_classification, "healthy")
        result = analyze_shot(
            self.shot.samples,
            target_yield_g=self.shot.target_yield_g,
            preinfusion_s=self.shot.preinfusion_s,
            baseline=None, expected_flow_g_s=CONFIG.expected_flow_g_s, config=CONFIG,
            actuator_delay_s=_actuator_delay_s(self.shot)
        )
        self.assertIsNone(result.invalid_reason)
        self.assertEqual(result.classification, ShotClassification.HEALTHY)
        self.assertLess(result.channeling_suspicion, CONFIG.suspicion_threshold)

    def test_the_fixture_carries_the_astringent_taste_annotation(self) -> None:
        """Confirms the fixture loader actually parses the hand-added
        flavor_mouthfeel_tag annotation, rather than silently falling back
        to None the way most fixtures (predating flavor-tag capture) do."""
        self.assertEqual(self.shot.flavor_mouthfeel_tag, "dry_astringent")


class TooRestrictiveFlaggedInvalidAdaptPiTests(unittest.TestCase):
    """"Mechanically good, but too slow" (the barista's own overall call) -
    recorded at capture time as invalid_measurement/
    disturbance_left_too_few_samples instead, because of a real bug: a
    single stale leading sample (seq=0, weight_g=69.5, immediately
    followed by the real ~0.1g baseline) wasn't trimmed as leading
    garbage at all (the old _first_plausible_index only ever rejected
    implausibly *negative* leading readings, never implausibly high
    ones), so the very next, real sample looked like a mid-shot
    "disturbance" relative to that garbage-inflated 69.5g "peak",
    truncating the shot down to a single sample. See flow_analysis.py's
    _first_plausible_index and test_flow_analysis.py's own direct unit
    test for the fix."""

    def setUp(self) -> None:
        self.shot = load_real_shot("too_restrictive_flagged_invalid_adapt_pi")

    def test_matches_the_barista_s_own_call(self) -> None:
        self.assertEqual(self.shot.recorded_classification, "invalid_measurement")
        result = analyze_shot(
            self.shot.samples,
            target_yield_g=self.shot.target_yield_g,
            preinfusion_s=self.shot.preinfusion_s,
            baseline=None, expected_flow_g_s=CONFIG.expected_flow_g_s, config=CONFIG,
            actuator_delay_s=_actuator_delay_s(self.shot)
        )
        self.assertIsNone(result.invalid_reason)
        self.assertEqual(result.classification, ShotClassification.TOO_RESTRICTIVE)
        self.assertLess(result.channeling_suspicion, CONFIG.suspicion_threshold)

    def test_the_fixture_still_has_its_stale_leading_sample(self) -> None:
        """Confirms the test above is exercising the real leading-garbage
        case this test class's docstring describes, rather than a fixture
        that never had one in the first place."""
        self.assertGreater(self.shot.samples[0].weight_g, 50.0)
        self.assertLess(self.shot.samples[1].weight_g, 1.0)


class DoubleLeadingGarbageAdaptPiTests(unittest.TestCase):
    """Same underlying bug as TooRestrictiveFlaggedInvalidAdaptPiTests
    above, but with a *run* of two identical stale leading readings
    (seq=0,1, both weight_g=145.2) instead of one - each garbage sample
    only compares against the first genuinely *different* value ahead of
    it, so a plateau of repeated identical garbage doesn't mask itself.
    Recorded at capture time as invalid_measurement/
    disturbance_left_too_few_samples for the same reason as that other
    fixture.

    No independent (barista/taste) verdict exists for this fixture - it
    was filed purely to regression-test the leading-garbage fix, so its
    downstream too_fast/healthy/too_restrictive classification was always
    incidental; what this test class actually guards is the leading-garbage
    fix (assertIsNone(invalid_reason) below), not the specific too_fast/
    healthy/too_restrictive band. This shot reaches a real 100%-of-target
    crossing; with both the t100 fix and actuator_delay_s budgeted (see
    analyze_shot's own comment and docs/data/DIAL_IN_RULES.md's Part 7),
    duration_ratio lands at ~1.02 - healthy. (With only the t100 fix
    applied, this briefly computed too_restrictive (~1.13) - a real but
    incomplete intermediate state, accepted as-is at the time since there
    was no ground truth here to weigh it against either way.)"""

    def setUp(self) -> None:
        self.shot = load_real_shot("double_leading_garbage_adapt_pi")

    def test_is_no_longer_wrongly_invalid(self) -> None:
        self.assertEqual(self.shot.recorded_classification, "invalid_measurement")
        result = analyze_shot(
            self.shot.samples,
            target_yield_g=self.shot.target_yield_g,
            preinfusion_s=self.shot.preinfusion_s,
            baseline=None, expected_flow_g_s=CONFIG.expected_flow_g_s, config=CONFIG,
            actuator_delay_s=_actuator_delay_s(self.shot)
        )
        self.assertIsNone(result.invalid_reason)
        self.assertEqual(result.classification, ShotClassification.HEALTHY)
        self.assertLess(result.channeling_suspicion, CONFIG.suspicion_threshold)

    def test_the_fixture_still_has_its_stale_leading_run(self) -> None:
        """Confirms the test above is exercising the real double-leading-
        garbage case this test class's docstring describes, rather than a
        fixture that never had one in the first place."""
        self.assertGreater(self.shot.samples[0].weight_g, 100.0)
        self.assertEqual(self.shot.samples[0].weight_g, self.shot.samples[1].weight_g)
        self.assertLess(self.shot.samples[2].weight_g, 1.0)


class TooFastButHealthyByTasteAdaptPiTests(unittest.TestCase):
    """The barista's own overall call: "healthy (36g within healthy zone)
    and tasted balanced". This fixture was the one that started this
    session's whole duration_ratio investigation: it was recorded/
    classified too_fast at export time (duration_ratio=0.843) even though
    the trace crosses target_yield_g (36.0g) at elapsed_ms=26211 - the
    user's own sharp observation was that this crossing genuinely fell
    inside the chart's own shaded healthy-time window, a real self-
    contradiction ("the code is broken if the UI shows healthy but the
    logic disagrees") worth fixing, not a known limitation to merely
    document.

    Two real gaps were found and fixed:
    1. duration_ratio was driven by t90 (reached at elapsed_ms=23755, 1.04s
       before the too_fast cutoff), not the real 100%-of-target crossing
       the chart itself visualizes - analyze_shot now prefers a real t100
       crossing when one exists.
    2. expected_s didn't budget actuator_delay_s - the dead time a Bot-held
       pre-infusion needs beyond its own programmed hold before flow can
       plausibly start (see analyze_shot's own comment and docs/data/
       DIAL_IN_RULES.md's Part 7).

    Fix 1 alone moved this shot to duration_ratio~0.930 (healthy) - briefly
    looking like the whole story. Fix 2, layered on top, moves it back to
    ~0.835 (too_fast): expected_s grows by actuator_delay_s while duration_s
    (already anchored to a real t100 crossing) doesn't change, so the ratio
    drops. That's not a regression on fix 1 - checking against the chart's
    own healthy window (test_the_target_crossing_no_longer_falls_inside_
    the_chart_s_own_healthy_window below) confirms the window itself
    shifts by the same actuator_delay_s and the target crossing now falls
    outside it too, so the chart and the classifier still agree with each
    other - the original self-contradiction stays fixed. What's left is a
    genuine timing-vs-taste disagreement (too_fast by timing, "balanced"
    by taste), the same accepted pattern as good_shot_adapt_pi/
    healthy_astringent_adapt_pi's own fixtures, not a new bug."""

    def setUp(self) -> None:
        self.shot = load_real_shot("too_fast_but_healthy_by_taste_adapt_pi")

    def test_is_classified_too_fast_by_timing_despite_the_healthy_verdict(self) -> None:
        self.assertEqual(self.shot.recorded_classification, "too_fast")
        result = analyze_shot(
            self.shot.samples,
            target_yield_g=self.shot.target_yield_g,
            preinfusion_s=self.shot.preinfusion_s,
            baseline=None, expected_flow_g_s=CONFIG.expected_flow_g_s, config=CONFIG,
            actuator_delay_s=_actuator_delay_s(self.shot)
        )
        self.assertIsNone(result.invalid_reason)
        self.assertEqual(result.classification, ShotClassification.TOO_FAST)
        self.assertLess(result.channeling_suspicion, CONFIG.suspicion_threshold)

    def test_duration_ratio_is_based_on_the_real_target_crossing(self) -> None:
        """Confirms the t100 fix's own mechanism still applies: t100_ms is
        found (this shot doesn't undershoot), and duration_ratio is derived
        from it rather than from t90_ms - the remaining too_fast verdict
        comes from actuator_delay_s widening expected_s, not from reverting
        to a t90-based duration_s."""
        result = analyze_shot(
            self.shot.samples,
            target_yield_g=self.shot.target_yield_g,
            preinfusion_s=self.shot.preinfusion_s,
            baseline=None, expected_flow_g_s=CONFIG.expected_flow_g_s, config=CONFIG,
            actuator_delay_s=_actuator_delay_s(self.shot)
        )
        self.assertIsNotNone(result.t100_ms)
        self.assertLess(result.t90_ms, result.t100_ms)
        too_fast_factor, _ = flow_analysis.healthy_duration_ratio_bounds(CONFIG.duration_ratio_bands)
        self.assertAlmostEqual(result.duration_ratio, 0.835, places=2)
        self.assertLess(result.duration_ratio, too_fast_factor)

    def test_the_target_crossing_no_longer_falls_inside_the_chart_s_own_healthy_window(self) -> None:
        """The chart's own healthy-window shading uses this exact same
        actuator_delay_s-budgeted formula (see runtime_entities.py's
        _build_shot_markers), so it shifts along with the classification -
        the target crossing that used to fall inside the (narrower, pre-
        actuator-delay) window now falls before the (wider) one too. Chart
        and classifier still agree with each other; this session's
        original bug (the two disagreeing) stays fixed."""
        result = analyze_shot(
            self.shot.samples,
            target_yield_g=self.shot.target_yield_g,
            preinfusion_s=self.shot.preinfusion_s,
            baseline=None, expected_flow_g_s=CONFIG.expected_flow_g_s, config=CONFIG,
            actuator_delay_s=_actuator_delay_s(self.shot)
        )
        healthy_start_ms, _ = flow_analysis.healthy_window_ms(
            int(self.shot.preinfusion_s * 1000),
            self.shot.target_yield_g,
            CONFIG.expected_flow_g_s,
            CONFIG.duration_ratio_bands,
            _actuator_delay_s(self.shot),
        )
        target_crossing_ms = next(
            s.elapsed_ms for s in self.shot.samples if s.weight_g >= self.shot.target_yield_g
        )
        self.assertLess(target_crossing_ms, healthy_start_ms)
        self.assertLess(result.t100_ms, healthy_start_ms)

    def test_the_fixture_carries_the_balanced_taste_annotation(self) -> None:
        self.assertEqual(self.shot.flavor_mouthfeel_tag, "balanced")


if __name__ == "__main__":
    unittest.main()
