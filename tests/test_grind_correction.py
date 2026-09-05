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
# expert_rules.grind_correction in definitions.yaml.
CONFIG = {
    "applies_to_classification": ["too_fast", "too_restrictive"],
    "excludes_classification": ["puck_prep_issue", "invalid_measurement"],
    "bands": [
        {"name": "grossly_fast", "duration_ratio_max": 0.6, "grind_delta": -2.0},
        {"name": "moderately_fast", "duration_ratio_max": 0.75, "grind_delta": -1.0},
        {"name": "slightly_fast", "duration_ratio_max": 0.88, "grind_delta": -0.5},
        {"name": "healthy", "duration_ratio_max": 1.10, "grind_delta": 0.0},
        {"name": "slightly_restrictive", "duration_ratio_max": 1.30, "grind_delta": 0.5},
        {"name": "moderately_restrictive", "duration_ratio_max": 1.60, "grind_delta": 1.0},
        {"name": "grossly_restrictive", "duration_ratio_max": None, "grind_delta": 2.0},
    ],
}


class RecommendGrindDeltaTests(unittest.TestCase):
    def test_grossly_fast_shot_recommends_the_largest_negative_delta(self):
        delta = recommend_grind_delta(ShotClassification.TOO_FAST, 0.5, CONFIG)
        self.assertEqual(delta, -2.0)

    def test_slightly_fast_shot_recommends_a_small_negative_delta(self):
        delta = recommend_grind_delta(ShotClassification.TOO_FAST, 0.85, CONFIG)
        self.assertEqual(delta, -0.5)

    def test_grossly_restrictive_shot_recommends_the_largest_positive_delta(self):
        """The last band has no duration_ratio_max, so an extreme ratio
        still matches it rather than falling through with no answer."""
        delta = recommend_grind_delta(ShotClassification.TOO_RESTRICTIVE, 5.0, CONFIG)
        self.assertEqual(delta, 2.0)

    def test_slightly_restrictive_shot_recommends_a_small_positive_delta(self):
        delta = recommend_grind_delta(ShotClassification.TOO_RESTRICTIVE, 1.2, CONFIG)
        self.assertEqual(delta, 0.5)

    def test_band_boundaries_are_inclusive_on_the_max_side(self):
        """A duration_ratio exactly on a band's own duration_ratio_max picks
        that band, not the next (looser) one - <=, not <."""
        delta = recommend_grind_delta(ShotClassification.TOO_FAST, 0.88, CONFIG)
        self.assertEqual(delta, -0.5)

    def test_healthy_classification_is_not_a_candidate(self):
        """Not in applies_to_classification, even though a duration_ratio of
        1.0 would otherwise land in the "healthy" band."""
        delta = recommend_grind_delta(ShotClassification.HEALTHY, 1.0, CONFIG)
        self.assertIsNone(delta)

    def test_puck_prep_issue_is_excluded_even_with_a_fast_duration_ratio(self):
        """docs/DESIGN.md section 13: mechanical validity is judged before
        the fast/slow hydraulic correction - a channeling-suspicious shot
        never gets a grind recommendation, no matter how its duration_ratio
        looks."""
        delta = recommend_grind_delta(ShotClassification.PUCK_PREP_ISSUE, 0.5, CONFIG)
        self.assertIsNone(delta)

    def test_invalid_measurement_is_excluded(self):
        delta = recommend_grind_delta(ShotClassification.INVALID, 0.5, CONFIG)
        self.assertIsNone(delta)

    def test_none_duration_ratio_is_not_a_candidate(self):
        """A shot flow_analysis couldn't compute duration_ratio for (t90
        never reached and no samples to fall back on) can't be banded."""
        delta = recommend_grind_delta(ShotClassification.TOO_RESTRICTIVE, None, CONFIG)
        self.assertIsNone(delta)

    def test_accepts_a_plain_string_classification_too(self):
        """classification doesn't have to be the enum - a plain string
        matching its .value works the same, for callers that only have the
        stored/serialized form (e.g. a string read back from the database)."""
        delta = recommend_grind_delta("too_fast", 0.5, CONFIG)
        self.assertEqual(delta, -2.0)


if __name__ == "__main__":
    unittest.main()
