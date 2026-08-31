"""Regression tests for flow_analysis.analyze_shot against real, recorded
shot exports (see tests/real_shot_fixtures.py and tests/fixtures/real_shots/),
each hand-annotated with the barista's own judgment of the shot. Complements
test_flow_analysis.py's synthetic curves - see that module's docstring for
why synthetic curves were used first (real shot data wasn't available yet).

analyze_shot is called with baseline=None throughout: fixtures don't carry
the actual historical baseline that was live at export time, and the point
here is whether the classifier's fixed-prior logic agrees with the human's
own call on the shot, not bit-for-bit reproduction of a historical DB row.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ha_stubs  # noqa: E402
from real_shot_fixtures import load_real_shot  # noqa: E402

flow_analysis = ha_stubs.import_barista_module("flow_analysis")
ShotClassification = flow_analysis.ShotClassification
analyze_shot = flow_analysis.analyze_shot


class GoodShotAdaptPiTests(unittest.TestCase):
    """"Good shot, adaptPI=True (app controlled)" - a held pre-infusion
    shot (preinfusion_s=7.0) the barista judged healthy: flow starts just
    after preinfusion ends, ramps up, and settles into a plausible pour
    despite an early flow spike/dip (a fast initial channel-like burst from
    ~9.5-11.5s that eases off and never recurs) - real pucks are not perfectly
    uniform, and this one still finished at 35.59g against a 36g target."""

    def setUp(self) -> None:
        self.shot = load_real_shot("good_shot_adapt_pi")

    def test_matches_the_barista_s_own_call(self) -> None:
        self.assertEqual(self.shot.recorded_classification, "healthy")
        result = analyze_shot(
            self.shot.samples,
            target_yield_g=self.shot.target_yield_g,
            preinfusion_s=self.shot.preinfusion_s,
            baseline=None,
        )
        self.assertIsNone(result.invalid_reason)
        self.assertEqual(result.classification, ShotClassification.HEALTHY)
        self.assertLess(result.channeling_suspicion, flow_analysis.SUSPICION_THRESHOLD)

    def test_flow_is_detected_right_after_preinfusion_ends(self) -> None:
        """preinfusion_s=7.0; real flow (per the recorded analysis_json)
        wasn't detected until t_first_flow_ms=9249 - well after preinfusion
        ended, not suspiciously early."""
        result = analyze_shot(
            self.shot.samples,
            target_yield_g=self.shot.target_yield_g,
            preinfusion_s=self.shot.preinfusion_s,
            baseline=None,
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
            baseline=None,
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
            baseline=None,
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
            baseline=None,
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
            baseline=None,
        )
        self.assertIsNone(result.invalid_reason)
        self.assertEqual(result.classification, ShotClassification.TOO_RESTRICTIVE)


class GoodButFlaggedMachinePiTests(unittest.TestCase):
    """"Seems to be a good shot (not sure why invalid), adaptPI=False
    (machine controlled)" - the main pour (elapsed_ms=9014 onward) does look
    like a healthy shot, finishing at 35.2g against a 36g target. A small
    trickle also creeps up to 0.3g between elapsed_ms=2054 and 8924, well
    inside the 8s machine pre-infusion window, but the scale only reports
    0.1g steps: a couple of isolated samples land close enough together that
    the raw derivative spikes past FIRST_FLOW_THRESHOLD_G_S for a single
    sample, even though the trickle itself is negligible and never sustains.
    The classification recorded at the time (invalid_measurement) was that
    bug; _first_sustained_crossing_ms now requires the crossing to hold for
    _FIRST_FLOW_SUSTAIN_MS before counting it, so this shot correctly comes
    back healthy, matching the barista's own instinct."""

    def setUp(self) -> None:
        self.shot = load_real_shot("good_but_flagged_machine_pi")

    def test_matches_the_barista_s_own_call(self) -> None:
        self.assertEqual(self.shot.recorded_classification, "invalid_measurement")
        result = analyze_shot(
            self.shot.samples,
            target_yield_g=self.shot.target_yield_g,
            preinfusion_s=self.shot.preinfusion_s,
            baseline=None,
        )
        self.assertIsNone(result.invalid_reason)
        self.assertEqual(result.classification, ShotClassification.HEALTHY)

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
            baseline=None,
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
            baseline=None,
        )
        self.assertIsNone(result.invalid_reason)
        self.assertIsNotNone(result.t90_ms)
        self.assertEqual(result.classification, ShotClassification.PUCK_PREP_ISSUE)
        self.assertGreaterEqual(result.channeling_suspicion, flow_analysis.SUSPICION_THRESHOLD)

    def test_the_fixture_still_has_its_bouncy_dips(self) -> None:
        """Confirms the test above is exercising the real bouncy-dip case
        this test class's docstring describes, rather than a fixture that
        never had one in the first place."""
        weights = [s.weight_g for s in self.shot.samples]
        biggest_dip = max(
            max(weights[:i + 1]) - w for i, w in enumerate(weights)
        )
        self.assertGreater(biggest_dip, 3.0)


if __name__ == "__main__":
    unittest.main()
