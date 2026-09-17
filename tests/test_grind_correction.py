"""Tests for grind_correction.py's Phase 4 recommend_grind_delta()."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ha_stubs  # noqa: E402

flow_analysis = ha_stubs.import_barista_module("flow_analysis")
grind_correction = ha_stubs.import_barista_module("grind_correction")

ShotClassification = flow_analysis.ShotClassification
recommend_grind_delta = grind_correction.recommend_grind_delta

# Mirrors the shape (not necessarily the exact values) of
# flow_analysis_constants.duration_ratio_bands in definitions.yaml - the
# shared, stage-agnostic ladder (name + duration_ratio_max only). Passed as
# recommend_grind_delta's own duration_ratio_bands argument, separate from
# CONFIG below.
DURATION_RATIO_BANDS = [
    {"name": "grossly_fast", "duration_ratio_max": 0.6},
    {"name": "moderately_fast", "duration_ratio_max": 0.75},
    {"name": "slightly_fast", "duration_ratio_max": 0.88},
    {"name": "healthy", "duration_ratio_max": 1.10},
    {"name": "slightly_restrictive", "duration_ratio_max": 1.30},
    {"name": "moderately_restrictive", "duration_ratio_max": 1.60},
    {"name": "grossly_restrictive", "duration_ratio_max": None},
]

# Mirrors the shape (not necessarily the exact values) of
# expert_rules.grind_correction in definitions.yaml - grind-correction's own
# policy only; the band boundaries above are shared with flow_analysis.py,
# not redefined here.
CONFIG = {
    "applies_to_classification": ["too_fast", "too_restrictive"],
    "excludes_classification": ["puck_prep_issue", "invalid_measurement"],
    "hold_constant": ["dose_g", "target_yield_g", "temperature_offset_c", "preinfusion_s"],
    "grind_deltas": {
        "grossly_fast": -2.0,
        "moderately_fast": -1.0,
        "slightly_fast": -0.5,
        "healthy": 0.0,
        "slightly_restrictive": 0.5,
        "moderately_restrictive": 1.0,
        "grossly_restrictive": 2.0,
    },
}


class RecommendGrindDeltaTests(unittest.TestCase):
    def test_grossly_fast_shot_recommends_the_largest_negative_delta(self):
        delta = recommend_grind_delta(ShotClassification.TOO_FAST, 0.5, CONFIG, DURATION_RATIO_BANDS)
        self.assertEqual(delta, -2.0)

    def test_slightly_fast_shot_recommends_a_small_negative_delta(self):
        delta = recommend_grind_delta(ShotClassification.TOO_FAST, 0.85, CONFIG, DURATION_RATIO_BANDS)
        self.assertEqual(delta, -0.5)

    def test_grossly_restrictive_shot_recommends_the_largest_positive_delta(self):
        """The last band has no duration_ratio_max, so an extreme ratio
        still matches it rather than falling through with no answer."""
        delta = recommend_grind_delta(ShotClassification.TOO_RESTRICTIVE, 5.0, CONFIG, DURATION_RATIO_BANDS)
        self.assertEqual(delta, 2.0)

    def test_slightly_restrictive_shot_recommends_a_small_positive_delta(self):
        delta = recommend_grind_delta(ShotClassification.TOO_RESTRICTIVE, 1.2, CONFIG, DURATION_RATIO_BANDS)
        self.assertEqual(delta, 0.5)

    def test_band_boundaries_are_inclusive_on_the_max_side(self):
        """A duration_ratio exactly on a band's own duration_ratio_max picks
        that band, not the next (looser) one - <=, not <."""
        delta = recommend_grind_delta(ShotClassification.TOO_FAST, 0.88, CONFIG, DURATION_RATIO_BANDS)
        self.assertEqual(delta, -0.5)

    def test_healthy_classification_is_not_a_candidate(self):
        """Not in applies_to_classification, even though a duration_ratio of
        1.0 would otherwise land in the "healthy" band."""
        delta = recommend_grind_delta(ShotClassification.HEALTHY, 1.0, CONFIG, DURATION_RATIO_BANDS)
        self.assertIsNone(delta)

    def test_puck_prep_issue_is_excluded_even_with_a_fast_duration_ratio(self):
        """docs/DESIGN.md section 13: mechanical validity is judged before
        the fast/slow hydraulic correction - a channeling-suspicious shot
        never gets a grind recommendation, no matter how its duration_ratio
        looks."""
        delta = recommend_grind_delta(ShotClassification.PUCK_PREP_ISSUE, 0.5, CONFIG, DURATION_RATIO_BANDS)
        self.assertIsNone(delta)

    def test_invalid_measurement_is_excluded(self):
        delta = recommend_grind_delta(ShotClassification.INVALID, 0.5, CONFIG, DURATION_RATIO_BANDS)
        self.assertIsNone(delta)

    def test_none_duration_ratio_is_not_a_candidate(self):
        """A shot flow_analysis couldn't compute duration_ratio for (t90
        never reached and no samples to fall back on) can't be banded."""
        delta = recommend_grind_delta(ShotClassification.TOO_RESTRICTIVE, None, CONFIG, DURATION_RATIO_BANDS)
        self.assertIsNone(delta)

    def test_accepts_a_plain_string_classification_too(self):
        """classification doesn't have to be the enum - a plain string
        matching its .value works the same, for callers that only have the
        stored/serialized form (e.g. a string read back from the database)."""
        delta = recommend_grind_delta("too_fast", 0.5, CONFIG, DURATION_RATIO_BANDS)
        self.assertEqual(delta, -2.0)


# Same recipe on every field grind_correction.hold_constant lists.
CURRENT_RECIPE = {
    "dose_g": 18.0,
    "target_yield_g": 36.0,
    "temperature_offset_c": 0,
    "preinfusion_s": 5.0,
}


def _previous_shot(*, classification, recommended_grind_delta, recipe_overrides=None):
    row = dict(CURRENT_RECIPE)
    row.update(recipe_overrides or {})
    row["classification"] = classification
    row["recommended_grind_delta"] = recommended_grind_delta
    return row


class OvershootDampingTests(unittest.TestCase):
    """docs/todo/GRIND_CORRECTION_PLAN.md §4 - damp the recommendation one
    band-tier back toward healthy when the previous shot's own recommended
    correction, on this same recipe, overshot past healthy into the
    opposite classification."""

    def test_no_previous_shot_is_undamped(self):
        delta = recommend_grind_delta(
            ShotClassification.TOO_FAST, 0.5, CONFIG, DURATION_RATIO_BANDS,
            current_recipe=CURRENT_RECIPE, previous_shot=None
        )
        self.assertEqual(delta, -2.0)

    def test_overshoot_into_slightly_restrictive_hits_the_floor(self):
        """Previous shot was moderately_fast (-1.0, applied), this shot came
        back too_restrictive matching slightly_restrictive (+0.5 undamped,
        index 4) - one tier toward healthy (index 3) would land exactly on
        healthy, so the floor keeps the undamped +0.5 instead (same
        mechanism as test_floor_never_collapses_to_healthy below, from the
        opposite direction)."""
        previous = _previous_shot(classification="too_fast", recommended_grind_delta=-1.0)
        delta = recommend_grind_delta(
            ShotClassification.TOO_RESTRICTIVE,
            1.2,
            CONFIG,
            DURATION_RATIO_BANDS,
            current_recipe=CURRENT_RECIPE,
            previous_shot=previous,
        )
        self.assertEqual(delta, 0.5)

    def test_overshoot_into_moderately_restrictive_damps_to_slightly_restrictive(self):
        """This shot matches moderately_restrictive (+1.0 undamped, index 5);
        stepping one tier toward healthy (index 3) lands on
        slightly_restrictive (+0.5, index 4), not on healthy itself."""
        previous = _previous_shot(classification="too_fast", recommended_grind_delta=-1.0)
        delta = recommend_grind_delta(
            ShotClassification.TOO_RESTRICTIVE,
            1.45,
            CONFIG,
            DURATION_RATIO_BANDS,
            current_recipe=CURRENT_RECIPE,
            previous_shot=previous,
        )
        self.assertEqual(delta, 0.5)

    def test_overshoot_is_symmetric_too_restrictive_to_too_fast(self):
        """Previous shot was moderately_restrictive (+1.0, applied), this
        shot came back grossly_fast (-2.0 undamped, index 0); one tier
        toward healthy (index 3) lands on moderately_fast (-1.0, index 1)."""
        previous = _previous_shot(classification="too_restrictive", recommended_grind_delta=1.0)
        delta = recommend_grind_delta(
            ShotClassification.TOO_FAST,
            0.5,
            CONFIG,
            DURATION_RATIO_BANDS,
            current_recipe=CURRENT_RECIPE,
            previous_shot=previous,
        )
        self.assertEqual(delta, -1.0)

    def test_no_damping_when_hold_constant_field_differs(self):
        """dose_g changed since the previous shot - the too_restrictive
        swing isn't attributable to grind alone, so no damping even though
        the classifications look like an overshoot pattern."""
        previous = _previous_shot(
            classification="too_fast",
            recommended_grind_delta=-1.0,
            recipe_overrides={"dose_g": 17.0},
        )
        delta = recommend_grind_delta(
            ShotClassification.TOO_RESTRICTIVE,
            1.45,
            CONFIG,
            DURATION_RATIO_BANDS,
            current_recipe=CURRENT_RECIPE,
            previous_shot=previous,
        )
        self.assertEqual(delta, 1.0)

    def test_no_damping_when_previous_shot_was_not_a_candidate(self):
        """Previous shot was healthy (never a grind-correction candidate, no
        recommended_grind_delta) - nothing to have overshot from."""
        previous = _previous_shot(classification="healthy", recommended_grind_delta=None)
        delta = recommend_grind_delta(
            ShotClassification.TOO_RESTRICTIVE,
            1.45,
            CONFIG,
            DURATION_RATIO_BANDS,
            current_recipe=CURRENT_RECIPE,
            previous_shot=previous,
        )
        self.assertEqual(delta, 1.0)

    def test_no_damping_when_classification_did_not_flip_sides(self):
        """Previous shot was moderately_fast and this shot is still on the
        too_fast side (slightly_fast) - improved, but not an overshoot past
        healthy, so the full undamped correction still applies."""
        previous = _previous_shot(classification="too_fast", recommended_grind_delta=-1.0)
        delta = recommend_grind_delta(
            ShotClassification.TOO_FAST,
            0.85,
            CONFIG,
            DURATION_RATIO_BANDS,
            current_recipe=CURRENT_RECIPE,
            previous_shot=previous,
        )
        self.assertEqual(delta, -0.5)

    def test_floor_never_collapses_to_healthy(self):
        """This shot matches slightly_fast (-0.5, index 2); one tier toward
        healthy (index 3) would land exactly on healthy (0.0) - the floor
        keeps the undamped -0.5 instead, since the shot is still off and
        needs some correction."""
        previous = _previous_shot(classification="too_restrictive", recommended_grind_delta=0.5)
        delta = recommend_grind_delta(
            ShotClassification.TOO_FAST,
            0.85,
            CONFIG,
            DURATION_RATIO_BANDS,
            current_recipe=CURRENT_RECIPE,
            previous_shot=previous,
        )
        self.assertEqual(delta, -0.5)


if __name__ == "__main__":
    unittest.main()
