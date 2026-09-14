from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "barista_assist" / "storage.py"
spec = importlib.util.spec_from_file_location("barista_storage", MODULE)
storage = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules["barista_storage"] = storage
spec.loader.exec_module(storage)


class StorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "barista.sqlite3"
        self.db = storage.BaristaDatabase(self.path)
        self.db.initialize()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def new_bag(self, name: str = "Test Coffee", *, roast_level: str | None = None):
        return self.db.new_bag(
            slot="normal",
            coffee_name=name,
            roaster="Test Roaster",
            roast_date="2026-08-10",
            starting_mass_g=250.0,
            dose_g=18.0,
            grind=15.0,
            target_yield_g=36.0,
            temperature_offset_c=1,
            preinfusion_s=7,
            roast_level=roast_level,
        )

    def test_new_bag_replaces_active_slot_only(self) -> None:
        first = self.new_bag("First")
        second = self.new_bag("Second")
        bags = self.db.active_bags()
        self.assertEqual(bags["normal"].id, second.id)
        self.assertNotEqual(first.id, second.id)

    def test_partial_recipe_update(self) -> None:
        bag = self.new_bag()
        self.db.update_recipe_field(bag.id, "grind", 14.5)
        updated = self.db.active_bags()["normal"]
        self.assertEqual(updated.grind, 14.5)
        self.assertEqual(updated.target_yield_g, 36.0)
        self.assertEqual(updated.preinfusion_s, 7)

    def test_create_shot_persists_expected_flow_g_s(self) -> None:
        """Fixed once at brew time (see runtime.py's async_brew) - feeds the
        Live Shot/Shot History charts' idealized-curve overlay."""
        bag = self.new_bag()
        shot_id = self.db.create_shot(
            bag=bag,
            started_at="2026-08-16T17:00:00+00:00",
            stop_compensation_g=1.5,
            preinfusion_s=7.0,
            adapt_pi=False,
            expected_flow_g_s=1.35,
        )
        self.assertAlmostEqual(self.db.latest_shot_bag(bag.id)["expected_flow_g_s"], 1.35)
        self.assertEqual(shot_id, self.db.latest_shot_bag(bag.id)["id"])

    def test_create_shot_expected_flow_g_s_defaults_to_none(self) -> None:
        bag = self.new_bag()
        self.db.create_shot(
            bag=bag,
            started_at="2026-08-16T17:00:00+00:00",
            stop_compensation_g=1.5,
            preinfusion_s=7.0,
            adapt_pi=False,
        )
        self.assertIsNone(self.db.latest_shot_bag(bag.id)["expected_flow_g_s"])

    def test_completed_shot_reduces_estimated_remaining(self) -> None:
        bag = self.new_bag()
        shot_id = self.db.create_shot(
            bag=bag,
            started_at="2026-08-16T17:00:00+00:00",
            stop_compensation_g=1.5,
            preinfusion_s=7.0,
            adapt_pi=False,
        )
        samples = [
            storage.ShotSample(0, 0, 0, 0.0, 0.0, 90),
            storage.ShotSample(1, 30000, 30000, 36.2, 1.8, 90),
        ]
        self.db.finalize_shot(
            shot_id,
            ended_at="2026-08-16T17:00:33+00:00",
            actual_yield_g=36.2,
            status="complete",
            stop_command_elapsed_ms=29000,
            samples=samples,
        )
        self.assertAlmostEqual(self.db.bag_remaining_g(bag.id), 232.0)
        last = self.db.latest_shot_bag(bag.id)
        self.assertEqual(last["sample_count"], 2)
        self.assertAlmostEqual(last["actual_yield_g"], 36.2)


    def test_export_marks_post_stop_samples(self) -> None:
        bag = self.new_bag('Export Coffee\tWith Newline')
        shot_id = self.db.create_shot(
            bag=bag,
            started_at='2026-08-16T17:00:00+00:00',
            stop_compensation_g=1.5,
            preinfusion_s=7.0,
            adapt_pi=False,
        )
        samples = [
            storage.ShotSample(0, 0, 0, 0.0, 0.0, 90),
            storage.ShotSample(1, 29000, 29000, 34.0, 1.5, 90),
            storage.ShotSample(2, 30000, 30000, 36.2, 1.2, 90),
        ]
        self.db.finalize_shot(
            shot_id,
            ended_at='2026-08-16T17:00:33+00:00',
            actual_yield_g=36.2,
            status='complete',
            stop_command_elapsed_ms=29000,
            samples=samples,
        )
        text = self.db.export_shots_text()
        self.assertIn('[SHOT]', text)
        self.assertIn('coffee_name=Export Coffee With Newline', text)
        self.assertIn('2\t30000\t30000\t36.200\t1.2000\t90\t1', text)
        self.assertIn('1\t29000\t29000\t34.000\t1.5000\t90\t1', text)
        self.assertIn('0\t0\t0\t0.000\t0.0000\t90\t0', text)

    def test_export_includes_adapt_pi_flag(self) -> None:
        bag = self.new_bag()
        shot_id = self.db.create_shot(
            bag=bag,
            started_at="2026-08-16T17:00:00+00:00",
            stop_compensation_g=1.5,
            preinfusion_s=8.0,
            adapt_pi=True,
        )
        self.db.finalize_shot(
            shot_id,
            ended_at="2026-08-16T17:00:33+00:00",
            actual_yield_g=36.2,
            status="complete",
            stop_command_elapsed_ms=29000,
            samples=[storage.ShotSample(0, 0, 0, 0.0, 0.0, 90)],
        )
        text = self.db.export_shots_text()
        self.assertIn("adapt_pi=True", text)

    def test_export_shots_text_can_filter_to_one_shot(self) -> None:
        """The shot-history card's per-row export button - shot_id restricts
        the export to just that shot's metadata and samples, leaving every
        other stored shot out entirely."""
        bag = self.new_bag()
        first_id = self.db.create_shot(
            bag=bag,
            started_at="2026-08-16T17:00:00+00:00",
            stop_compensation_g=1.5,
            preinfusion_s=7.0,
            adapt_pi=False,
        )
        self.db.finalize_shot(
            first_id,
            ended_at="2026-08-16T17:00:33+00:00",
            actual_yield_g=36.0,
            status="complete",
            stop_command_elapsed_ms=29000,
            samples=[storage.ShotSample(0, 0, 0, 0.0, 0.0, 90)],
        )
        second_id = self.db.create_shot(
            bag=bag,
            started_at="2026-08-16T18:00:00+00:00",
            stop_compensation_g=1.5,
            preinfusion_s=7.0,
            adapt_pi=False,
        )
        self.db.finalize_shot(
            second_id,
            ended_at="2026-08-16T18:00:33+00:00",
            actual_yield_g=36.0,
            status="complete",
            stop_command_elapsed_ms=29000,
            samples=[storage.ShotSample(0, 0, 0, 0.0, 0.0, 90)],
        )

        text = self.db.export_shots_text(shot_id=first_id)
        self.assertIn(f"shot_id={first_id}", text)
        self.assertNotIn(f"shot_id={second_id}", text)
        self.assertEqual(text.count("[SHOT]"), 1)

    def test_export_includes_flow_analysis_fields(self) -> None:
        bag = self.new_bag()
        self.finalize_with_analysis(bag, classification="puck_prep_issue", late_accel=1.2, t90_ms=15000)
        text = self.db.export_shots_text()
        self.assertIn('classification=puck_prep_issue', text)
        self.assertIn('channeling_suspicion=0.1', text)
        self.assertIn('analysis_json={"late_accel": 1.2, "t90_ms": 15000}', text)

    def finalize_with_analysis(
        self, bag, *, classification: str, late_accel: float, t90_ms: int, target_yield_g: float = 36.0
    ) -> None:
        """Finalize a shot carrying just enough analysis_json for
        recent_healthy_features to compute a flow rate and late_accel from."""
        shot_id = self.db.create_shot(
            bag=bag,
            started_at="2026-08-16T17:00:00+00:00",
            stop_compensation_g=1.5,
            preinfusion_s=7.0,
            adapt_pi=False,
        )
        samples = [storage.ShotSample(0, 0, 0, 0.0, 0.0, 90)]
        self.db.finalize_shot(
            shot_id,
            ended_at="2026-08-16T17:00:33+00:00",
            actual_yield_g=target_yield_g,
            status="complete",
            stop_command_elapsed_ms=None,
            samples=samples,
            classification=classification,
            channeling_suspicion=0.1,
            analysis_json=json.dumps({"late_accel": late_accel, "t90_ms": t90_ms}),
        )

    def test_finalize_shot_persists_analysis_fields(self) -> None:
        bag = self.new_bag()
        self.finalize_with_analysis(bag, classification="healthy", late_accel=0.05, t90_ms=20000)
        last = self.db.latest_shot_bag(bag.id)
        self.assertEqual(last["classification"], "healthy")
        self.assertAlmostEqual(last["channeling_suspicion"], 0.1)
        self.assertEqual(json.loads(last["analysis_json"]), {"late_accel": 0.05, "t90_ms": 20000})

    def test_finalize_shot_persists_effective_stop_margin(self) -> None:
        """effective_stop_margin_g records the live-projected margin actually
        used at this shot's own stop decision (see ActiveShot.
        effective_stop_margin_g) - distinct from stop_compensation_g, the
        floor the user configured, which a fast-flowing shot can exceed."""
        bag = self.new_bag()
        shot_id = self.db.create_shot(
            bag=bag,
            started_at="2026-08-16T17:00:00+00:00",
            stop_compensation_g=1.5,
            preinfusion_s=7.0,
            adapt_pi=False,
        )
        self.db.finalize_shot(
            shot_id,
            ended_at="2026-08-16T17:00:33+00:00",
            actual_yield_g=30.0,
            status="complete",
            stop_command_elapsed_ms=25000,
            samples=[],
            effective_stop_margin_g=6.8,
        )
        self.assertAlmostEqual(self.db.latest_shot_bag(bag.id)["effective_stop_margin_g"], 6.8)

    def test_finalize_shot_effective_stop_margin_defaults_to_none(self) -> None:
        """A manually aborted/timed-out shot never had a weight-triggered
        margin computed for it - must persist as None, not 0.0 or missing."""
        bag = self.new_bag()
        shot_id = self.db.create_shot(
            bag=bag,
            started_at="2026-08-16T17:00:00+00:00",
            stop_compensation_g=1.5,
            preinfusion_s=7.0,
            adapt_pi=False,
        )
        self.db.finalize_shot(
            shot_id,
            ended_at="2026-08-16T17:00:33+00:00",
            actual_yield_g=20.0,
            status="aborted",
            stop_command_elapsed_ms=12000,
            samples=[],
        )
        self.assertIsNone(self.db.latest_shot_bag(bag.id)["effective_stop_margin_g"])

    def test_finalize_shot_persists_recommended_grind_delta(self) -> None:
        """Phase 4's (docs/DESIGN.md section 28) expert-system grind
        recommendation, computed by grind_correction.recommend_grind_delta
        in runtime.py's _async_finalize."""
        bag = self.new_bag()
        shot_id = self.db.create_shot(
            bag=bag,
            started_at="2026-08-16T17:00:00+00:00",
            stop_compensation_g=1.5,
            preinfusion_s=7.0,
            adapt_pi=False,
        )
        self.db.finalize_shot(
            shot_id,
            ended_at="2026-08-16T17:00:33+00:00",
            actual_yield_g=30.0,
            status="complete",
            stop_command_elapsed_ms=25000,
            samples=[],
            recommended_grind_delta=-1.0,
        )
        self.assertAlmostEqual(self.db.latest_shot_bag(bag.id)["recommended_grind_delta"], -1.0)

    def test_finalize_shot_recommended_grind_delta_defaults_to_none(self) -> None:
        """A healthy shot (or one excluded as puck_prep_issue/
        invalid_measurement) isn't a grind-correction candidate at all -
        must persist as None, not 0.0, so it's distinguishable from an
        actual "no change" recommendation."""
        bag = self.new_bag()
        shot_id = self.db.create_shot(
            bag=bag,
            started_at="2026-08-16T17:00:00+00:00",
            stop_compensation_g=1.5,
            preinfusion_s=7.0,
            adapt_pi=False,
        )
        self.db.finalize_shot(
            shot_id,
            ended_at="2026-08-16T17:00:33+00:00",
            actual_yield_g=30.0,
            status="complete",
            stop_command_elapsed_ms=25000,
            samples=[],
        )
        self.assertIsNone(self.db.latest_shot_bag(bag.id)["recommended_grind_delta"])

    def test_new_bag_persists_roast_level(self) -> None:
        bag = self.new_bag(roast_level="medium")
        self.assertEqual(bag.roast_level, "medium")
        self.assertEqual(self.db.active_bags()["normal"].roast_level, "medium")

    def test_new_bag_roast_level_defaults_to_none(self) -> None:
        bag = self.new_bag()
        self.assertIsNone(bag.roast_level)

    def test_record_flavor_tag_persists_per_axis(self) -> None:
        bag = self.new_bag()
        shot_id = self.db.create_shot(
            bag=bag,
            started_at="2026-08-16T17:00:00+00:00",
            stop_compensation_g=1.5,
            preinfusion_s=7.0,
            adapt_pi=False,
        )
        self.db.record_flavor_tag(shot_id, "extraction", "sour_sharp")
        self.db.record_flavor_tag(shot_id, "mouthfeel", "dry_astringent")
        shot = self.db.latest_shot_bag(bag.id)
        self.assertEqual(shot["flavor_extraction_tag"], "sour_sharp")
        self.assertEqual(shot["flavor_mouthfeel_tag"], "dry_astringent")

    def test_record_flavor_tag_on_one_axis_does_not_touch_the_other(self) -> None:
        """A shot can be answered on only one axis (e.g. the mouthfeel
        notification was never tapped) - recording extraction must not
        overwrite mouthfeel with anything, and vice versa."""
        bag = self.new_bag()
        shot_id = self.db.create_shot(
            bag=bag,
            started_at="2026-08-16T17:00:00+00:00",
            stop_compensation_g=1.5,
            preinfusion_s=7.0,
            adapt_pi=False,
        )
        self.db.record_flavor_tag(shot_id, "extraction", "bitter_harsh")
        self.assertIsNone(self.db.latest_shot_bag(bag.id)["flavor_mouthfeel_tag"])

    def test_record_flavor_tag_returns_whether_a_shot_was_found(self) -> None:
        """Mirrors delete_shot's own return convention - lets a caller (see
        runtime._handle_flavor_notification_action) log a stale notification
        action instead of it silently doing nothing."""
        bag = self.new_bag()
        shot_id = self.db.create_shot(
            bag=bag,
            started_at="2026-08-16T17:00:00+00:00",
            stop_compensation_g=1.5,
            preinfusion_s=7.0,
            adapt_pi=False,
        )
        self.assertTrue(self.db.record_flavor_tag(shot_id, "extraction", "sour_sharp"))
        self.assertFalse(self.db.record_flavor_tag("no-such-shot", "extraction", "sour_sharp"))

    def test_recent_flavor_tags_is_most_recent_first_and_answered_only(self) -> None:
        bag = self.new_bag()
        for started_at, tag in [
            ("2026-08-16T17:00:00+00:00", "sour_sharp"),
            ("2026-08-16T18:00:00+00:00", None),  # never answered
            ("2026-08-16T19:00:00+00:00", "balanced"),
        ]:
            shot_id = self.db.create_shot(
                bag=bag,
                started_at=started_at,
                stop_compensation_g=1.5,
                preinfusion_s=7.0,
                adapt_pi=False,
            )
            if tag is not None:
                self.db.record_flavor_tag(shot_id, "extraction", tag)
        self.assertEqual(
            self.db.recent_flavor_tags(bag.id, "extraction"), ["balanced", "sour_sharp"]
        )

    def test_recent_flavor_tags_is_empty_with_no_history(self) -> None:
        bag = self.new_bag()
        self.assertEqual(self.db.recent_flavor_tags(bag.id, "mouthfeel"), [])

    def test_recent_shots_and_latest_shot_bag_include_the_bag_s_roaster(self) -> None:
        bag = self.new_bag()  # new_bag() sets roaster="Test Roaster"
        self.db.create_shot(
            bag=bag,
            started_at="2026-08-16T17:00:00+00:00",
            stop_compensation_g=1.5,
            preinfusion_s=7.0,
            adapt_pi=False,
        )
        self.assertEqual(self.db.latest_shot_bag(bag.id)["roaster"], "Test Roaster")
        self.assertEqual(self.db.recent_shots(limit=None)[0]["roaster"], "Test Roaster")

    def _finalize_classified_shot(
        self, bag, *, classification: str, started_at: str, recommended_grind_delta: float | None = None
    ) -> None:
        """Create+finalize a shot at bag's own current recipe snapshot,
        carrying just a classification (and optionally
        recommended_grind_delta) - the shared helper behind
        latest_shot/consecutive_puck_prep_issue_count tests below,
        which only care about classification/recipe fields, not full flow
        analysis."""
        shot_id = self.db.create_shot(
            bag=bag,
            started_at=started_at,
            stop_compensation_g=1.5,
            preinfusion_s=bag.preinfusion_s,
            adapt_pi=False,
        )
        self.db.finalize_shot(
            shot_id,
            ended_at=started_at,
            actual_yield_g=bag.target_yield_g,
            status="complete",
            stop_command_elapsed_ms=None,
            samples=[],
            classification=classification,
            recommended_grind_delta=recommended_grind_delta,
        )

    def test_latest_shot_is_none_with_no_classified_shot(self) -> None:
        bag = self.new_bag()
        self.assertIsNone(self.db.latest_shot(bag.id))

    def test_latest_shot_returns_the_most_recent_classified_shot(self) -> None:
        bag = self.new_bag()
        self._finalize_classified_shot(
            bag, classification="too_fast", started_at="2026-08-16T17:00:00+00:00",
            recommended_grind_delta=-1.0,
        )
        self._finalize_classified_shot(
            bag, classification="healthy", started_at="2026-08-16T17:05:00+00:00",
        )
        shot = self.db.latest_shot(bag.id)
        self.assertEqual(shot["classification"], "healthy")
        self.assertIsNone(shot["recommended_grind_delta"])

    def test_latest_shot_is_scoped_to_the_bag(self) -> None:
        """A bag swap mints a fresh id (new_bag), so a different bag's
        shots never leak into this one's latest-shot lookup."""
        bag_a = self.new_bag()
        self._finalize_classified_shot(
            bag_a, classification="too_fast", started_at="2026-08-16T17:00:00+00:00"
        )
        bag_b = self.new_bag()
        self.assertIsNone(self.db.latest_shot(bag_b.id))
        self.assertEqual(self.db.latest_shot(bag_a.id)["classification"], "too_fast")

    def test_latest_shot_bag_is_scoped_to_the_bag(self) -> None:
        """Same scoping as latest_shot above, for latest_shot_bag - the
        broader (every column, unclassified shots included) query behind
        BaristaRuntime.last_shot and its last_yield/shot_classification/
        shot_channeling_suspicion/recommended_grind sensors. A different
        bag's own most recent shot must never leak into this one's."""
        bag_a = self.new_bag()
        self.db.create_shot(
            bag=bag_a,
            started_at="2026-08-16T17:00:00+00:00",
            stop_compensation_g=1.5,
            preinfusion_s=7.0,
            adapt_pi=False,
        )
        bag_b = self.new_bag()
        self.assertIsNone(self.db.latest_shot_bag(bag_b.id))
        self.assertIsNotNone(self.db.latest_shot_bag(bag_a.id))

    def test_latest_shot_bag_returns_the_most_recent_shot_on_that_bag(self) -> None:
        bag = self.new_bag()
        self.db.create_shot(
            bag=bag,
            started_at="2026-08-16T17:00:00+00:00",
            stop_compensation_g=1.5,
            preinfusion_s=7.0,
            adapt_pi=False,
        )
        newer_shot_id = self.db.create_shot(
            bag=bag,
            started_at="2026-08-16T17:05:00+00:00",
            stop_compensation_g=1.5,
            preinfusion_s=7.0,
            adapt_pi=False,
        )
        self.assertEqual(self.db.latest_shot_bag(bag.id)["id"], newer_shot_id)

    def test_consecutive_puck_prep_issue_count_is_zero_with_no_history(self) -> None:
        bag = self.new_bag()
        current_recipe = {
            "dose_g": bag.dose_g,
            "target_yield_g": bag.target_yield_g,
            "temperature_offset_c": bag.temperature_offset_c,
            "preinfusion_s": bag.preinfusion_s,
            "grind": bag.grind,
        }
        self.assertEqual(self.db.consecutive_puck_prep_issue_count(bag.id, current_recipe), 0)

    def test_consecutive_puck_prep_issue_count_is_zero_when_most_recent_is_not_puck_prep_issue(
        self,
    ) -> None:
        bag = self.new_bag()
        self._finalize_classified_shot(
            bag, classification="puck_prep_issue", started_at="2026-08-16T17:00:00+00:00"
        )
        self._finalize_classified_shot(
            bag, classification="healthy", started_at="2026-08-16T17:05:00+00:00"
        )
        current_recipe = {
            "dose_g": bag.dose_g,
            "target_yield_g": bag.target_yield_g,
            "temperature_offset_c": bag.temperature_offset_c,
            "preinfusion_s": bag.preinfusion_s,
            "grind": bag.grind,
        }
        self.assertEqual(self.db.consecutive_puck_prep_issue_count(bag.id, current_recipe), 0)

    def test_consecutive_puck_prep_issue_count_counts_a_real_streak(self) -> None:
        bag = self.new_bag()
        for i in range(3):
            self._finalize_classified_shot(
                bag, classification="puck_prep_issue", started_at=f"2026-08-16T17:0{i}:00+00:00"
            )
        current_recipe = {
            "dose_g": bag.dose_g,
            "target_yield_g": bag.target_yield_g,
            "temperature_offset_c": bag.temperature_offset_c,
            "preinfusion_s": bag.preinfusion_s,
            "grind": bag.grind,
        }
        self.assertEqual(self.db.consecutive_puck_prep_issue_count(bag.id, current_recipe), 3)

    def test_consecutive_puck_prep_issue_count_stops_at_a_recipe_change(self) -> None:
        """Grind included in the match (not just grind_correction's own
        hold_constant) - a self-tried grind change is a new attempt, not a
        continuation of the same unresolved streak."""
        bag = self.new_bag()
        self._finalize_classified_shot(
            bag, classification="puck_prep_issue", started_at="2026-08-16T17:00:00+00:00"
        )
        self.db.update_recipe_field(bag.id, "grind", bag.grind + 1.0)
        bag = self.db.active_bags()["normal"]
        self._finalize_classified_shot(
            bag, classification="puck_prep_issue", started_at="2026-08-16T17:05:00+00:00"
        )
        current_recipe = {
            "dose_g": bag.dose_g,
            "target_yield_g": bag.target_yield_g,
            "temperature_offset_c": bag.temperature_offset_c,
            "preinfusion_s": bag.preinfusion_s,
            "grind": bag.grind,
        }
        self.assertEqual(self.db.consecutive_puck_prep_issue_count(bag.id, current_recipe), 1)

    def test_consecutive_puck_prep_issue_count_stops_at_a_non_matching_classification(
        self,
    ) -> None:
        bag = self.new_bag()
        self._finalize_classified_shot(
            bag, classification="healthy", started_at="2026-08-16T17:00:00+00:00"
        )
        self._finalize_classified_shot(
            bag, classification="puck_prep_issue", started_at="2026-08-16T17:05:00+00:00"
        )
        current_recipe = {
            "dose_g": bag.dose_g,
            "target_yield_g": bag.target_yield_g,
            "temperature_offset_c": bag.temperature_offset_c,
            "preinfusion_s": bag.preinfusion_s,
            "grind": bag.grind,
        }
        self.assertEqual(self.db.consecutive_puck_prep_issue_count(bag.id, current_recipe), 1)

    def test_recent_healthy_features_is_none_with_no_history(self) -> None:
        bag = self.new_bag()
        self.assertIsNone(self.db.recent_healthy_features(bag.id))

    def test_recent_healthy_features_ignores_non_healthy_shots(self) -> None:
        bag = self.new_bag()
        self.finalize_with_analysis(bag, classification="too_fast", late_accel=0.9, t90_ms=8000)
        self.assertIsNone(self.db.recent_healthy_features(bag.id))

    def test_recent_healthy_features_medians_recent_healthy_shots(self) -> None:
        bag = self.new_bag()
        self.finalize_with_analysis(bag, classification="healthy", late_accel=0.0, t90_ms=20000)
        self.finalize_with_analysis(bag, classification="healthy", late_accel=0.2, t90_ms=30000)
        self.finalize_with_analysis(bag, classification="puck_prep_issue", late_accel=5.0, t90_ms=9000)

        features = self.db.recent_healthy_features(bag.id)
        self.assertEqual(features["shot_count"], 2)
        self.assertAlmostEqual(features["median_late_accel"], 0.1)

    def test_roast_level_baseline_is_none_with_no_roast_level(self) -> None:
        self.assertIsNone(self.db.roast_level_baseline(None))

    def test_roast_level_baseline_is_none_with_no_matching_shots(self) -> None:
        self.assertIsNone(self.db.roast_level_baseline("medium"))

    def test_roast_level_baseline_excludes_the_given_bag(self) -> None:
        bag = self.new_bag(roast_level="medium")
        self.finalize_with_analysis(bag, classification="healthy", late_accel=0.0, t90_ms=20000)
        self.assertIsNone(self.db.roast_level_baseline("medium", exclude_bag_id=bag.id))
        self.assertIsNotNone(self.db.roast_level_baseline("medium"))

    def test_roast_level_baseline_includes_too_fast_and_too_restrictive_but_not_puck_prep_issue(
        self,
    ) -> None:
        bag = self.new_bag(roast_level="medium")
        self.finalize_with_analysis(bag, classification="too_fast", late_accel=0.0, t90_ms=10000)
        self.finalize_with_analysis(bag, classification="too_restrictive", late_accel=0.0, t90_ms=40000)
        self.finalize_with_analysis(bag, classification="puck_prep_issue", late_accel=5.0, t90_ms=9000)

        self.assertEqual(self.db.roast_level_baseline("medium")["shot_count"], 2)

    def test_roast_level_baseline_medians_across_bags_sharing_roast_level(self) -> None:
        # finalize_with_analysis's own shots all carry preinfusion_s=7.0 -
        # median_flow_g_s is extraction-only (t90 minus that preinfusion_s),
        # matching flow_analysis.py's own expected_s formula, not the raw
        # target_yield_g/t90 button-to-90%-yield rate.
        bag_a = self.new_bag(roast_level="medium")
        # target_yield_g=36, dose_g=18, t90=20s -7s PI=13s -> flow rate
        # 36/13 g/s, ratio 2.0
        self.finalize_with_analysis(bag_a, classification="healthy", late_accel=0.0, t90_ms=20000)
        # Replaces bag_a in the slot, but bag_a's own row/shots stay queryable.
        bag_b = self.new_bag(roast_level="medium")
        # target_yield_g=36, dose_g=18, t90=30s -7s PI=23s -> flow rate
        # 36/23 g/s, ratio 2.0
        self.finalize_with_analysis(bag_b, classification="healthy", late_accel=0.0, t90_ms=30000)
        bag_c = self.new_bag(roast_level="dark")
        self.finalize_with_analysis(bag_c, classification="healthy", late_accel=0.0, t90_ms=12000)

        baseline = self.db.roast_level_baseline("medium", exclude_bag_id=bag_b.id)
        self.assertEqual(baseline["shot_count"], 1)
        self.assertAlmostEqual(baseline["median_flow_g_s"], 36.0 / 13.0)
        self.assertAlmostEqual(baseline["median_ratio"], 2.0)
        self.assertAlmostEqual(baseline["median_dose_g"], 18.0)

    def test_recent_shots_with_no_limit_returns_every_shot(self) -> None:
        bag = self.new_bag()
        for _ in range(3):
            self.db.create_shot(
                bag=bag,
                started_at="2026-08-16T17:00:00+00:00",
                stop_compensation_g=1.5,
                preinfusion_s=7.0,
                adapt_pi=False,
            )
        self.assertEqual(len(self.db.recent_shots(limit=2)), 2)
        self.assertEqual(len(self.db.recent_shots(limit=None)), 3)

    def test_shot_samples_returns_the_raw_time_series_in_order(self) -> None:
        bag = self.new_bag()
        shot_id = self.db.create_shot(
            bag=bag,
            started_at="2026-08-16T17:00:00+00:00",
            stop_compensation_g=1.5,
            preinfusion_s=7.0,
            adapt_pi=False,
        )
        samples = [
            storage.ShotSample(0, 0, 0, 0.0, 0.0, 90),
            storage.ShotSample(1, 1000, 1000, 1.5, 1.5, 90),
        ]
        self.db.finalize_shot(
            shot_id,
            ended_at="2026-08-16T17:00:33+00:00",
            actual_yield_g=1.5,
            status="complete",
            stop_command_elapsed_ms=None,
            samples=samples,
        )
        result = self.db.shot_samples(shot_id)
        self.assertEqual([row["seq"] for row in result], [0, 1])
        self.assertAlmostEqual(result[1]["weight_g"], 1.5)

    def test_delete_shot_removes_it_and_cascades_to_its_samples(self) -> None:
        bag = self.new_bag()
        shot_id = self.db.create_shot(
            bag=bag,
            started_at="2026-08-16T17:00:00+00:00",
            stop_compensation_g=1.5,
            preinfusion_s=7.0,
            adapt_pi=False,
        )
        self.db.finalize_shot(
            shot_id,
            ended_at="2026-08-16T17:00:33+00:00",
            actual_yield_g=36.2,
            status="complete",
            stop_command_elapsed_ms=None,
            samples=[storage.ShotSample(0, 0, 0, 0.0, 0.0, 90)],
        )
        self.assertTrue(self.db.delete_shot(shot_id))
        self.assertIsNone(self.db.latest_shot_bag(bag.id))
        self.assertEqual(self.db.shot_samples(shot_id), [])

    def test_delete_shot_updates_the_bag_remaining_estimate(self) -> None:
        bag = self.new_bag()
        shot_id = self.db.create_shot(
            bag=bag,
            started_at="2026-08-16T17:00:00+00:00",
            stop_compensation_g=1.5,
            preinfusion_s=7.0,
            adapt_pi=False,
        )
        self.db.finalize_shot(
            shot_id,
            ended_at="2026-08-16T17:00:33+00:00",
            actual_yield_g=36.2,
            status="complete",
            stop_command_elapsed_ms=None,
            samples=[storage.ShotSample(0, 0, 0, 0.0, 0.0, 90)],
        )
        self.assertAlmostEqual(self.db.bag_remaining_g(bag.id), 232.0)
        self.db.delete_shot(shot_id)
        self.assertAlmostEqual(self.db.bag_remaining_g(bag.id), 250.0)

    def test_delete_shot_returns_false_for_an_unknown_id(self) -> None:
        self.assertFalse(self.db.delete_shot("does-not-exist"))

    def test_v1_database_migrates_preinfusion(self) -> None:
        legacy_path = Path(self.tmp.name) / "legacy.sqlite3"
        legacy_db = storage.BaristaDatabase(legacy_path)
        with sqlite3.connect(legacy_path) as db:
            db.executescript(
                (legacy_db.migrations_dir / "001_initial.sql").read_text(encoding="utf-8")
            )
            db.execute("PRAGMA user_version=1")
            db.execute(
                """
                INSERT INTO bags(
                    id,slot,coffee_name,roaster,roast_date,opened_at,starting_mass_g,
                    dose_g,grind,target_yield_g,temperature_offset_c,active
                ) VALUES('bag1','decaf','Legacy',NULL,NULL,'2026-08-01',250,18,15,38,0,1)
                """
            )
        previous = legacy_db.initialize(legacy_preinfusion_s=9)
        self.assertEqual(previous, 1)
        self.assertEqual(legacy_db.active_bags()["decaf"].preinfusion_s, 9)


if __name__ == "__main__":
    unittest.main()
