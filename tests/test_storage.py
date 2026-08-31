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

    def new_bag(self, name: str = "Test Coffee"):
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
        last = self.db.last_shot()
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
        last = self.db.last_shot()
        self.assertEqual(last["classification"], "healthy")
        self.assertAlmostEqual(last["channeling_suspicion"], 0.1)
        self.assertEqual(json.loads(last["analysis_json"]), {"late_accel": 0.05, "t90_ms": 20000})

    def test_recent_healthy_features_is_none_with_no_history(self) -> None:
        bag = self.new_bag()
        self.assertIsNone(self.db.recent_healthy_features(bag.id))

    def test_recent_healthy_features_ignores_non_healthy_shots(self) -> None:
        bag = self.new_bag()
        self.finalize_with_analysis(bag, classification="too_fast", late_accel=0.9, t90_ms=8000)
        self.assertIsNone(self.db.recent_healthy_features(bag.id))

    def test_recent_healthy_features_medians_recent_healthy_shots(self) -> None:
        bag = self.new_bag()
        # target_yield_g=36, t90_ms=20000 -> flow rate 1.8 g/s
        self.finalize_with_analysis(bag, classification="healthy", late_accel=0.0, t90_ms=20000)
        # target_yield_g=36, t90_ms=30000 -> flow rate 1.2 g/s
        self.finalize_with_analysis(bag, classification="healthy", late_accel=0.2, t90_ms=30000)
        self.finalize_with_analysis(bag, classification="puck_prep_issue", late_accel=5.0, t90_ms=9000)

        features = self.db.recent_healthy_features(bag.id)
        self.assertEqual(features["shot_count"], 2)
        self.assertAlmostEqual(features["median_late_accel"], 0.1)
        self.assertAlmostEqual(features["median_flow_g_s"], 1.5)

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
        self.assertIsNone(self.db.last_shot())
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

    def test_v1_database_migrates_preinfusion_and_legacy_slot(self) -> None:
        legacy_path = Path(self.tmp.name) / "legacy.sqlite3"
        legacy_db = storage.BaristaDatabase(legacy_path)
        with sqlite3.connect(legacy_path) as db:
            db.executescript(
                (legacy_db.migrations_dir / "001_initial.sql").read_text(encoding="utf-8")
            )
            db.execute("PRAGMA user_version=1")
            db.execute("INSERT INTO settings(key,value) VALUES('selected_slot','decaf')")
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
        self.assertEqual(legacy_db.legacy_selected_slot(), "decaf")
        self.assertEqual(legacy_db.active_bags()["decaf"].preinfusion_s, 9)


if __name__ == "__main__":
    unittest.main()
