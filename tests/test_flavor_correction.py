"""Tests for flavor_correction.py's Phase 5 recommend_flavor_correction()."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ha_stubs  # noqa: E402

flavor_correction = ha_stubs.import_barista_module("flavor_correction")

recommend_flavor_correction = flavor_correction.recommend_flavor_correction

# Mirrors the shape (not necessarily the exact values) of
# expert_rules.flavor_correction in definitions.yaml.
CONFIG = {
    "require_persistent_pattern_shots": 2,
    "minimum_meaningful_step": {
        "temperature_offset_c": 1,
        "dose_g": 0.5,
        "target_yield_g": 2.5,
    },
    "tags": {
        "sour_sharp": {"lever": "yield", "direction": "increase", "delta_g": 7.5},
        "bitter_harsh": {"lever": "yield", "direction": "decrease"},
        "thin_weak": {"lever": "dose", "direction": "increase", "delta_g": 0.5},
        "dry_astringent": {"lever": "yield", "direction": "decrease"},
        "balanced": {},
    },
}


class RecommendFlavorCorrectionTests(unittest.TestCase):
    def test_none_tag_is_not_a_candidate(self):
        self.assertIsNone(recommend_flavor_correction(None, [], CONFIG))

    def test_balanced_tag_is_not_a_candidate(self):
        self.assertIsNone(
            recommend_flavor_correction("balanced", ["balanced", "balanced"], CONFIG)
        )

    def test_unknown_tag_is_not_a_candidate(self):
        self.assertIsNone(recommend_flavor_correction("nonsense", ["nonsense"] * 5, CONFIG))

    def test_a_single_report_is_not_persistent(self):
        """require_persistent_pattern_shots=2: one answered shot alone can't
        trigger a recommendation, matching Stage 3's own reasoning (a single
        shot's taste tag is noise, not evidence)."""
        self.assertIsNone(recommend_flavor_correction("sour_sharp", ["sour_sharp"], CONFIG))

    def test_a_broken_streak_is_not_persistent(self):
        """Most recent 2 answered shots must both match - an older matching
        shot further back doesn't count if the streak broke since."""
        recent = ["sour_sharp", "balanced", "sour_sharp"]
        self.assertIsNone(recommend_flavor_correction("sour_sharp", recent, CONFIG))

    def test_two_persistent_reports_recommend_the_tag_s_base_lever(self):
        recent = ["sour_sharp", "sour_sharp"]
        result = recommend_flavor_correction("sour_sharp", recent, CONFIG)
        self.assertEqual(result["lever"], "yield")
        self.assertEqual(result["field"], "target_yield_g")
        self.assertEqual(result["direction"], "increase")
        self.assertEqual(result["delta"], 7.5)

    def test_a_single_delta_g_number_is_used_as_is(self):
        result = recommend_flavor_correction("thin_weak", ["thin_weak"] * 2, CONFIG)
        self.assertEqual(result["lever"], "dose")
        self.assertEqual(result["field"], "dose_g")
        self.assertEqual(result["delta"], 0.5)

    def test_a_tag_with_no_delta_g_falls_back_to_minimum_meaningful_step(self):
        """bitter_harsh/dry_astringent have no sourced magnitude for their
        direction (see their own comments in definitions.yaml) - falls back
        to this block's own minimum_meaningful_step for that lever's field."""
        result = recommend_flavor_correction("bitter_harsh", ["bitter_harsh"] * 2, CONFIG)
        self.assertEqual(result["field"], "target_yield_g")
        self.assertEqual(result["delta"], 2.5)
        self.assertEqual(result["direction"], "decrease")

    def test_dry_astringent_also_falls_back_to_minimum_meaningful_step(self):
        result = recommend_flavor_correction("dry_astringent", ["dry_astringent"] * 3, CONFIG)
        self.assertEqual(result["delta"], 2.5)

    def test_more_than_the_required_streak_still_recommends(self):
        """3 matching answered shots in a row is still persistent, not just
        exactly N."""
        result = recommend_flavor_correction("thin_weak", ["thin_weak"] * 3, CONFIG)
        self.assertIsNotNone(result)


if __name__ == "__main__":
    unittest.main()
