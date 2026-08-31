"""Tests for the pure Stage-1 shot-flow classifier in flow_analysis.py.

Shots are synthesized rather than recorded: each test builds a mass/flow
curve shaped like the scenario it targets (steady, fast, stalled, a late
acceleration meant to resemble channeling) and checks the resulting
classification, not internal intermediate numbers, since the smoothing/
threshold constants are expected to change once real shot data is available
(see docs/DESIGN.md's data-driven-thresholds follow-up phase).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ha_stubs  # noqa: E402

flow_analysis = ha_stubs.import_barista_module("flow_analysis")
storage = ha_stubs.import_barista_module("storage")

BaselineFeatures = flow_analysis.BaselineFeatures
ShotClassification = flow_analysis.ShotClassification
InvalidReason = flow_analysis.InvalidReason
analyze_shot = flow_analysis.analyze_shot
ShotSample = storage.ShotSample

TARGET_YIELD_G = 36.0
# median_flow_g_s matches the module's own prior everywhere except the
# dedicated blending test, so it's a no-op for tests not about that.
HEALTHY_BASELINE = BaselineFeatures(
    shot_count=3, median_late_accel=0.0, median_flow_g_s=flow_analysis._EXPECTED_FLOW_G_S
)


def _simulate(flow_fn, duration_s: float, hz: float = 10.0) -> list[ShotSample]:
    """Build samples by Euler-integrating a flow(t)-in-g/s callable."""
    dt = 1.0 / hz
    steps = int(duration_s * hz) + 1
    samples = []
    weight = 0.0
    for i in range(steps):
        t_s = i * dt
        if i > 0:
            weight += flow_fn(t_s - dt) * dt
        elapsed_ms = int(round(t_s * 1000))
        samples.append(
            ShotSample(
                seq=i,
                elapsed_ms=elapsed_ms,
                scale_ms=elapsed_ms,
                weight_g=weight,
                flow_g_s=flow_fn(t_s),
                battery_percent=90,
            )
        )
    return samples


def _steady_flow_samples(duration_s: float, target_yield_g: float = TARGET_YIELD_G):
    rate = target_yield_g / duration_s
    return _simulate(lambda _t: rate, duration_s)


class FlowAnalysisTests(unittest.TestCase):
    """Most tests pass preinfusion_s=0.0: they exercise duration/suspicion logic,
    not the first-flow-plausibility check, and none of these synthetic curves
    model a real pre-infusion soak phase."""

    def test_too_few_samples_is_invalid(self) -> None:
        samples = _steady_flow_samples(18.0)[:3]
        result = analyze_shot(
            samples, target_yield_g=TARGET_YIELD_G, preinfusion_s=0.0, baseline=HEALTHY_BASELINE
        )
        self.assertEqual(result.classification, ShotClassification.INVALID)
        self.assertFalse(result.baseline_eligible)
        self.assertEqual(result.invalid_reason, InvalidReason.TOO_FEW_SAMPLES)

    def test_near_zero_final_weight_is_invalid(self) -> None:
        """A scale fault or empty cup: plenty of samples, but almost no beverage mass."""
        samples = _simulate(lambda _t: 0.02, 18.0)
        result = analyze_shot(
            samples, target_yield_g=TARGET_YIELD_G, preinfusion_s=0.0, baseline=HEALTHY_BASELINE
        )
        self.assertEqual(result.classification, ShotClassification.INVALID)
        self.assertEqual(result.invalid_reason, InvalidReason.NEAR_ZERO_FINAL_WEIGHT)

    def test_flow_starting_well_before_preinfusion_ends_is_invalid(self) -> None:
        """docs/DESIGN.md section 13: "was time to first flow plausible?"

        Pre-infusion is meant to be a low/no-flow soak; flow detected almost
        immediately with a 7s configured pre-infusion means either the press
        didn't actually hold, or the trace can't be trusted.
        """
        samples = _steady_flow_samples(duration_s=24.0)
        result = analyze_shot(
            samples, target_yield_g=TARGET_YIELD_G, preinfusion_s=7.0, baseline=HEALTHY_BASELINE
        )
        self.assertEqual(result.classification, ShotClassification.INVALID)
        self.assertEqual(result.invalid_reason, InvalidReason.FLOW_STARTED_BEFORE_PREINFUSION_END)

    def test_flow_that_never_crosses_the_first_flow_threshold_is_invalid(self) -> None:
        """A final weight over the near-zero floor, but flow never registers as real
        flow at any point: an implausible combination, not a genuinely slow pour."""
        samples = _simulate(lambda _t: 0.05, 30.0)
        result = analyze_shot(
            samples, target_yield_g=TARGET_YIELD_G, preinfusion_s=0.0, baseline=HEALTHY_BASELINE
        )
        self.assertIsNone(result.t_first_flow_ms)
        self.assertEqual(result.classification, ShotClassification.INVALID)
        self.assertEqual(result.invalid_reason, InvalidReason.NO_DETECTED_FLOW)

    def test_steady_flow_at_the_expected_rate_is_healthy(self) -> None:
        expected_s = TARGET_YIELD_G / flow_analysis._EXPECTED_FLOW_G_S
        samples = _steady_flow_samples(duration_s=expected_s)
        result = analyze_shot(
            samples, target_yield_g=TARGET_YIELD_G, preinfusion_s=0.0, baseline=HEALTHY_BASELINE
        )
        self.assertEqual(result.classification, ShotClassification.HEALTHY)
        self.assertIsNotNone(result.t50_ms)
        self.assertAlmostEqual(result.t50_ms, 0.5 * expected_s * 1000, delta=200)

    def test_cup_lifted_after_the_pour_does_not_contaminate_the_classification(self) -> None:
        """The user can remove the cup/scale whenever they like: weight can
        only rise during a real pour, so a drop is unambiguous interference,
        not flow - and everything after it must be discarded, not analyzed."""
        expected_s = TARGET_YIELD_G / flow_analysis._EXPECTED_FLOW_G_S
        clean = _steady_flow_samples(duration_s=expected_s)
        clean_result = analyze_shot(
            clean, target_yield_g=TARGET_YIELD_G, preinfusion_s=0.0, baseline=None
        )
        self.assertEqual(clean_result.classification, ShotClassification.HEALTHY)

        last = clean[-1]
        disturbed_tail = [
            ShotSample(
                seq=last.seq + 1,
                elapsed_ms=last.elapsed_ms + 100,
                scale_ms=last.elapsed_ms + 100,
                weight_g=last.weight_g - 20.0,  # cup yanked off the scale
                flow_g_s=-200.0,
                battery_percent=90,
            ),
            ShotSample(
                seq=last.seq + 2,
                elapsed_ms=last.elapsed_ms + 200,
                scale_ms=last.elapsed_ms + 200,
                weight_g=0.5,
                flow_g_s=0.0,
                battery_percent=90,
            ),
        ]
        result = analyze_shot(
            clean + disturbed_tail,
            target_yield_g=TARGET_YIELD_G,
            preinfusion_s=0.0,
            baseline=None,
        )
        self.assertEqual(result.classification, ShotClassification.HEALTHY)
        self.assertEqual(result.t90_ms, clean_result.t90_ms)

    def test_pre_infusion_settling_noise_is_not_a_disturbance(self) -> None:
        """Regression test: a real ~53g water shot was misclassified
        invalid_measurement/near_zero_final_weight even though it clearly
        poured. The scale's own settling noise during pre-infusion dipped
        from a ~0.4g peak down to -0.2g (a 0.6g drop, over
        _MAX_PLAUSIBLE_WEIGHT_DROP_G) before any real coffee mass had
        accumulated, and the old, unconditional disturbance check truncated
        everything after that noise blip - discarding the entire real pour
        that followed. Disturbance detection must not arm before the running
        peak clears _DISTURBANCE_DETECTION_FLOOR_G."""
        noisy_preinfusion = [
            ShotSample(seq=0, elapsed_ms=0, scale_ms=0, weight_g=0.0, flow_g_s=0.0, battery_percent=90),
            ShotSample(seq=1, elapsed_ms=100, scale_ms=100, weight_g=0.4, flow_g_s=0.5, battery_percent=90),
            ShotSample(seq=2, elapsed_ms=200, scale_ms=200, weight_g=-0.2, flow_g_s=-0.6, battery_percent=90),
            ShotSample(seq=3, elapsed_ms=300, scale_ms=300, weight_g=0.0, flow_g_s=0.2, battery_percent=90),
        ]
        expected_s = TARGET_YIELD_G / flow_analysis._EXPECTED_FLOW_G_S
        real_pour = _steady_flow_samples(duration_s=expected_s)
        shifted_pour = [
            ShotSample(
                seq=len(noisy_preinfusion) + s.seq,
                elapsed_ms=s.elapsed_ms + 300,
                scale_ms=s.elapsed_ms + 300,
                weight_g=s.weight_g,
                flow_g_s=s.flow_g_s,
                battery_percent=90,
            )
            for s in real_pour
        ]
        result = analyze_shot(
            noisy_preinfusion + shifted_pour,
            target_yield_g=TARGET_YIELD_G,
            preinfusion_s=0.0,
            baseline=HEALTHY_BASELINE,
        )
        self.assertEqual(result.classification, ShotClassification.HEALTHY)

    def test_disturbance_below_the_floor_is_ignored_but_above_it_is_still_caught(self) -> None:
        """Direct unit test for the floor added to _first_disturbance_index:
        a drop is ignored while the running peak is still under
        _DISTURBANCE_DETECTION_FLOOR_G (real pre-infusion settling noise),
        but the identical-sized drop is still flagged once the running peak
        has cleared that floor, exactly as before this fix."""
        times_ms = [0, 100, 200, 300]
        below_floor = [0.0, 0.4, -0.2, 0.0]  # peak 0.4g, well under the floor
        self.assertIsNone(flow_analysis._first_disturbance_index(times_ms, below_floor))

        above_floor = [0.0, 1.0, 3.0, 2.0]  # peak 3.0g; the 1.0g drop exceeds the threshold
        self.assertEqual(flow_analysis._first_disturbance_index(times_ms, above_floor), 3)

    def test_disturbance_leaving_too_few_samples_is_invalid_with_a_distinct_reason(self) -> None:
        """A disturbance early enough to leave under MIN_SAMPLES of trustworthy
        data must be distinguishable in logs from a shot that was simply
        always too sparse (e.g. a near-instant abort)."""
        # 4 clean samples (weight already past _DISTURBANCE_DETECTION_FLOOR_G,
        # so the drop below counts as a real disturbance, not settling noise)
        # + 1 disturbed = 5 total, clearing the "too few samples at all"
        # check below, but the disturbed one gets truncated away, leaving
        # only 4 trustworthy samples - still under MIN_SAMPLES.
        clean_prefix = [
            ShotSample(seq=0, elapsed_ms=0, scale_ms=0, weight_g=0.0, flow_g_s=0.0, battery_percent=90),
            ShotSample(seq=1, elapsed_ms=100, scale_ms=100, weight_g=1.0, flow_g_s=10.0, battery_percent=90),
            ShotSample(seq=2, elapsed_ms=200, scale_ms=200, weight_g=2.0, flow_g_s=10.0, battery_percent=90),
            ShotSample(seq=3, elapsed_ms=300, scale_ms=300, weight_g=3.0, flow_g_s=10.0, battery_percent=90),
        ]
        last = clean_prefix[-1]
        disturbed = clean_prefix + [
            ShotSample(
                seq=last.seq + 1,
                elapsed_ms=last.elapsed_ms + 100,
                scale_ms=last.elapsed_ms + 100,
                weight_g=0.1,
                flow_g_s=-50.0,
                battery_percent=90,
            )
        ]
        result = analyze_shot(
            disturbed, target_yield_g=TARGET_YIELD_G, preinfusion_s=0.0, baseline=None
        )
        self.assertEqual(result.classification, ShotClassification.INVALID)
        self.assertEqual(result.invalid_reason, InvalidReason.DISTURBANCE_LEFT_TOO_FEW_SAMPLES)

    def test_a_garbage_leading_sample_does_not_derail_classification(self) -> None:
        """Regression test: a real shot's very first sample (before the
        scale had connected/settled) read -48g, one reading before returning
        to a normal ~0g baseline. Left in, that single outlier poisoned the
        smoothing/derivative computation enough to spuriously cross the
        first-flow threshold at t=22ms - long before pre-infusion could have
        plausibly ended - misclassifying an otherwise ordinary too-fast shot
        as invalid_measurement/flow_started_before_preinfusion_end."""
        garbage_first_sample = ShotSample(
            seq=0, elapsed_ms=22, scale_ms=0, weight_g=-48.0, flow_g_s=0.06, battery_percent=90
        )
        expected_s = TARGET_YIELD_G / flow_analysis._EXPECTED_FLOW_G_S
        real_pour = _steady_flow_samples(duration_s=expected_s * 0.3)
        shifted_pour = [
            ShotSample(
                seq=s.seq + 1,
                elapsed_ms=s.elapsed_ms + 100,
                scale_ms=s.elapsed_ms + 100,
                weight_g=s.weight_g,
                flow_g_s=s.flow_g_s,
                battery_percent=90,
            )
            for s in real_pour
        ]
        result = analyze_shot(
            [garbage_first_sample] + shifted_pour,
            target_yield_g=TARGET_YIELD_G,
            preinfusion_s=0.0,
            baseline=HEALTHY_BASELINE,
        )
        self.assertIsNone(result.invalid_reason)
        self.assertEqual(result.classification, ShotClassification.TOO_FAST)

    def test_first_plausible_index_skips_only_implausibly_negative_leading_readings(self) -> None:
        """Direct unit test for _first_plausible_index: ordinary near-zero
        noise (including small negative dips) is never skipped, and neither
        is a legitimately high leading *positive* reading (e.g. real samples
        that only start once a pour is already underway) - only readings
        clearly beyond real scale noise on the negative side are."""
        self.assertEqual(flow_analysis._first_plausible_index([0.0, 0.1, -0.1, 0.2]), 0)
        self.assertEqual(flow_analysis._first_plausible_index([-48.0, 0.0, 0.1]), 1)
        self.assertEqual(flow_analysis._first_plausible_index([-48.0, 30.0, 0.0]), 1)
        self.assertEqual(flow_analysis._first_plausible_index([30.0, 99.0]), 0)
        self.assertEqual(flow_analysis._first_plausible_index([-48.0, -30.0]), 2)  # all implausible

    def test_reaching_target_far_faster_than_expected_is_too_fast(self) -> None:
        expected_s = TARGET_YIELD_G / flow_analysis._EXPECTED_FLOW_G_S
        samples = _steady_flow_samples(duration_s=expected_s * 0.3)
        result = analyze_shot(
            samples, target_yield_g=TARGET_YIELD_G, preinfusion_s=0.0, baseline=HEALTHY_BASELINE
        )
        self.assertEqual(result.classification, ShotClassification.TOO_FAST)

    def test_reaching_target_far_slower_than_expected_is_too_restrictive(self) -> None:
        expected_s = TARGET_YIELD_G / flow_analysis._EXPECTED_FLOW_G_S
        samples = _steady_flow_samples(duration_s=expected_s * 2.2)
        result = analyze_shot(
            samples, target_yield_g=TARGET_YIELD_G, preinfusion_s=0.0, baseline=HEALTHY_BASELINE
        )
        self.assertEqual(result.classification, ShotClassification.TOO_RESTRICTIVE)

    def test_flow_that_stalls_before_90_percent_is_too_restrictive(self) -> None:
        """Puck effectively clogs: flow drops to near zero partway through."""

        def flow_fn(t: float) -> float:
            return 2.0 if t < 8.0 else 0.01

        samples = _simulate(flow_fn, duration_s=20.0)
        result = analyze_shot(
            samples, target_yield_g=TARGET_YIELD_G, preinfusion_s=0.0, baseline=HEALTHY_BASELINE
        )
        self.assertIsNone(result.t90_ms)
        self.assertEqual(result.classification, ShotClassification.TOO_RESTRICTIVE)

    def test_design_doc_example_a_is_too_fast(self) -> None:
        """docs/DESIGN.md section 25, Example A: 18g -> 38g, PI 7s, 24s total,
        smooth/globally-high-flow curve, explicitly "clearly fast" (needs a
        grind correction, per Stage 2) rather than a mechanical/puck problem."""
        target_yield_g = 38.0
        preinfusion_s = 7.0

        def flow_fn(t: float) -> float:
            return 0.0 if t < preinfusion_s else target_yield_g / (24.0 - preinfusion_s)

        samples = _simulate(flow_fn, duration_s=24.0)
        result = analyze_shot(
            samples, target_yield_g=target_yield_g, preinfusion_s=preinfusion_s, baseline=None
        )
        self.assertEqual(result.classification, ShotClassification.TOO_FAST)

    def test_steady_shot_is_healthy_with_no_baseline_at_all(self) -> None:
        """No per-bag history yet: fall back entirely on the fixed mechanical prior."""
        expected_s = TARGET_YIELD_G / flow_analysis._EXPECTED_FLOW_G_S
        samples = _steady_flow_samples(duration_s=expected_s)
        sparse_baseline = BaselineFeatures(
            shot_count=1, median_late_accel=0.0, median_flow_g_s=flow_analysis._EXPECTED_FLOW_G_S
        )
        for baseline in (None, sparse_baseline):
            result = analyze_shot(
                samples, target_yield_g=TARGET_YIELD_G, preinfusion_s=0.0, baseline=baseline
            )
            self.assertEqual(result.classification, ShotClassification.HEALTHY)
            self.assertTrue(result.baseline_eligible)

    def test_a_bags_own_characteristic_pace_stops_being_flagged_as_too_fast(self) -> None:
        """The expected flow rate is a reference point, not a safety boundary
        (see module docstring): a bag that genuinely runs faster than the
        generic prior should stop being called "too fast" once enough of its
        own healthy history says that pace is normal for it - unlike
        mechanical suspicion, this one is allowed to fully self-normalize."""
        bag_rate = 1.8  # well above the global prior of 1.25 g/s
        samples = _steady_flow_samples(duration_s=TARGET_YIELD_G / bag_rate)

        no_history = analyze_shot(
            samples, target_yield_g=TARGET_YIELD_G, preinfusion_s=0.0, baseline=None
        )
        self.assertEqual(no_history.classification, ShotClassification.TOO_FAST)

        strong_history = BaselineFeatures(
            shot_count=50, median_late_accel=0.0, median_flow_g_s=bag_rate
        )
        result = analyze_shot(
            samples, target_yield_g=TARGET_YIELD_G, preinfusion_s=0.0, baseline=strong_history
        )
        self.assertEqual(result.classification, ShotClassification.HEALTHY)

    def test_blended_flow_rate_shifts_toward_bag_history_as_shot_count_grows(self) -> None:
        bag_rate = 1.8
        prior_only = flow_analysis._blended_expected_flow_g_s(None)
        weak = flow_analysis._blended_expected_flow_g_s(
            BaselineFeatures(shot_count=1, median_late_accel=0.0, median_flow_g_s=bag_rate)
        )
        strong = flow_analysis._blended_expected_flow_g_s(
            BaselineFeatures(shot_count=50, median_late_accel=0.0, median_flow_g_s=bag_rate)
        )
        self.assertEqual(prior_only, flow_analysis._EXPECTED_FLOW_G_S)
        self.assertLess(prior_only, weak)
        self.assertLess(weak, strong)
        self.assertLess(strong, bag_rate)  # even 50 shots don't fully erase the prior

    def test_late_flow_runaway_is_flagged_as_puck_prep_issue_even_with_no_baseline(self) -> None:
        """Normal start, then flow accelerates hard in the final third: channeling-like.

        The fixed mechanical prior alone must catch this since it must work
        from a bag's very first shot, before any baseline exists.
        """
        switch_s = 12.0

        def flow_fn(t: float) -> float:
            if t < switch_s:
                return 2.0
            return 2.0 + 1.0 * (t - switch_s)

        samples = _simulate(flow_fn, duration_s=20.0)
        for baseline in (None, HEALTHY_BASELINE):
            result = analyze_shot(
                samples, target_yield_g=TARGET_YIELD_G, preinfusion_s=0.0, baseline=baseline
            )
            self.assertNotIn(
                result.classification,
                (ShotClassification.TOO_FAST, ShotClassification.TOO_RESTRICTIVE),
            )
            self.assertEqual(result.classification, ShotClassification.PUCK_PREP_ISSUE)
            self.assertGreaterEqual(result.channeling_suspicion, 0.6)

    def test_late_flow_runaway_is_flagged_even_against_a_matching_bad_baseline(self) -> None:
        """A baseline can only ever raise suspicion, never launder a bad shot as normal.

        Regression test: comparing only against a bag's own baseline (the old
        behavior) let a channeling-like shot pass as healthy once a few similarly
        bad shots had already been accepted into that baseline - exactly the
        contamination docs/DESIGN.md section 12 says must not happen.
        """
        switch_s = 12.0

        def flow_fn(t: float) -> float:
            if t < switch_s:
                return 2.0
            return 2.0 + 1.0 * (t - switch_s)

        samples = _simulate(flow_fn, duration_s=20.0)
        matching_baseline = BaselineFeatures(
            shot_count=5, median_late_accel=1.0, median_flow_g_s=flow_analysis._EXPECTED_FLOW_G_S
        )
        result = analyze_shot(
            samples,
            target_yield_g=TARGET_YIELD_G,
            preinfusion_s=0.0,
            baseline=matching_baseline,
        )
        self.assertEqual(result.classification, ShotClassification.PUCK_PREP_ISSUE)

    def test_baseline_deviation_can_escalate_a_shot_the_absolute_check_would_miss(self) -> None:
        """A rise too small to trip the fixed prior alone is still flagged for a bag
        whose own history has been unusually flat."""
        switch_s = 20.0
        base_rate = (2 / 3 * TARGET_YIELD_G) / switch_s  # reach the late-third boundary right at the switch

        def flow_fn(t: float) -> float:
            if t < switch_s:
                return base_rate
            return base_rate + 0.3 * (t - switch_s)

        samples = _simulate(flow_fn, duration_s=32.0)

        unflagged = analyze_shot(
            samples, target_yield_g=TARGET_YIELD_G, preinfusion_s=0.0, baseline=None
        )
        self.assertEqual(unflagged.classification, ShotClassification.HEALTHY)

        tight_baseline = BaselineFeatures(
            shot_count=5, median_late_accel=0.0, median_flow_g_s=flow_analysis._EXPECTED_FLOW_G_S
        )
        escalated = analyze_shot(
            samples, target_yield_g=TARGET_YIELD_G, preinfusion_s=0.0, baseline=tight_baseline
        )
        self.assertEqual(escalated.classification, ShotClassification.PUCK_PREP_ISSUE)
        self.assertGreater(escalated.channeling_suspicion, unflagged.channeling_suspicion)


class FirstSustainedCrossingTests(unittest.TestCase):
    """Direct tests for _first_sustained_crossing_ms, the helper that keeps a
    single noisy/quantization-driven derivative spike from being mistaken for
    the true start of flow (see test_flow_analysis_real_shots.py's
    GoodButFlaggedMachinePiTests for the real-shot regression this fixed).
    Tested in isolation from the smoothing pipeline since the crossing rule
    itself is what's being verified here, not curve-shaping."""

    def test_a_single_sample_spike_does_not_count(self) -> None:
        times_ms = [0, 100, 200, 300, 400]
        values = [0.0, 0.0, 1.0, 0.0, 0.0]
        result = flow_analysis._first_sustained_crossing_ms(times_ms, values, 0.3, 300)
        self.assertIsNone(result)

    def test_a_sustained_run_counts_from_its_start(self) -> None:
        times_ms = [0, 100, 200, 300, 400, 500]
        values = [0.0, 0.0, 1.0, 1.0, 1.0, 1.0]
        result = flow_analysis._first_sustained_crossing_ms(times_ms, values, 0.3, 300)
        self.assertEqual(result, 200)

    def test_a_run_still_rising_at_the_last_sample_counts_even_if_short(self) -> None:
        """No later data to prove a still-rising run wouldn't have held -
        treat it as real rather than discarding a shot that simply ended
        while genuinely mid-pour."""
        times_ms = [0, 100, 200]
        values = [0.0, 0.0, 1.0]
        result = flow_analysis._first_sustained_crossing_ms(times_ms, values, 0.3, 300)
        self.assertEqual(result, 200)

    def test_an_earlier_spike_is_skipped_in_favor_of_the_real_sustained_run(self) -> None:
        times_ms = [0, 100, 200, 300, 400, 500, 600]
        values = [0.0, 1.0, 0.0, 0.0, 1.0, 1.0, 1.0]
        result = flow_analysis._first_sustained_crossing_ms(times_ms, values, 0.3, 300)
        self.assertEqual(result, 400)


class FirstSyncedClockIndexTests(unittest.TestCase):
    """Direct tests for _first_synced_clock_index, the helper that drops
    leading samples whose scale_ms shows they're stale BLE notifications from
    before the scale's clock was reset for this shot (see
    test_flow_analysis_real_shots.py's StaleScaleClockMachinePiTests for the
    real-shot regression this fixed)."""

    def test_no_stale_samples_returns_zero(self) -> None:
        times_ms = [0, 100, 200, 300]
        scale_ms = [0, 100, 200, 300]
        self.assertEqual(flow_analysis._first_synced_clock_index(times_ms, scale_ms), 0)

    def test_leading_stale_samples_are_counted(self) -> None:
        times_ms = [26, 116, 235, 330]
        scale_ms = [19200, 19200, 0, 0]
        self.assertEqual(flow_analysis._first_synced_clock_index(times_ms, scale_ms), 2)

    def test_every_sample_stale_returns_the_full_length(self) -> None:
        times_ms = [26, 116]
        scale_ms = [19200, 19300]
        self.assertEqual(flow_analysis._first_synced_clock_index(times_ms, scale_ms), 2)


class FirstDisturbanceIndexTests(unittest.TestCase):
    """Direct tests for _first_disturbance_index, which now requires a drop
    below the running peak to hold for _DISTURBANCE_SUSTAIN_MS before
    counting as a genuine cup/scale disturbance, rather than flagging on a
    single instantaneous dip (see test_flow_analysis_real_shots.py's
    ViolentGushMachinePiTests for the real-shot regression this fixed)."""

    def test_no_drop_returns_none(self) -> None:
        times_ms = [0, 100, 200, 300]
        weights = [0.0, 1.0, 2.0, 3.0]
        self.assertIsNone(flow_analysis._first_disturbance_index(times_ms, weights))

    def test_a_quickly_recovering_dip_does_not_count(self) -> None:
        times_ms = [0, 100, 300, 500, 700]
        weights = [0.0, 3.0, 2.4, 3.5, 4.0]
        self.assertIsNone(flow_analysis._first_disturbance_index(times_ms, weights))

    def test_a_drop_that_never_recovers_counts(self) -> None:
        times_ms = [0, 100, 200, 1300, 1400, 1500, 1600]
        weights = [0.0, 3.0, 1.0, 1.0, 1.0, 1.0, 1.0]
        self.assertEqual(flow_analysis._first_disturbance_index(times_ms, weights), 2)

    def test_a_drop_sustained_long_enough_counts_even_if_it_later_recovers(self) -> None:
        times_ms = [0, 100, 200, 1250, 1300]
        weights = [0.0, 3.0, 1.0, 1.0, 3.5]
        self.assertEqual(flow_analysis._first_disturbance_index(times_ms, weights), 2)

    def test_a_drop_below_the_detection_floor_does_not_count(self) -> None:
        """Below _DISTURBANCE_DETECTION_FLOOR_G, settling noise alone can
        exceed _MAX_PLAUSIBLE_WEIGHT_DROP_G with no real disturbance."""
        times_ms = [0, 100, 200, 2000]
        weights = [0.0, 1.0, 0.3, 0.3]
        self.assertIsNone(flow_analysis._first_disturbance_index(times_ms, weights))


if __name__ == "__main__":
    unittest.main()
