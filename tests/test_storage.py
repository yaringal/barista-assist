from __future__ import annotations

import importlib.util
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
