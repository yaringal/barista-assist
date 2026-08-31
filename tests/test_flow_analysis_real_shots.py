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


if __name__ == "__main__":
    unittest.main()
