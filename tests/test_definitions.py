import dataclasses
from pathlib import Path
import importlib.util
import os
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "barista_assist" / "definitions.py"
spec = importlib.util.spec_from_file_location("barista_definitions", MODULE)
definitions = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules["barista_definitions"] = definitions
spec.loader.exec_module(definitions)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ha_stubs  # noqa: E402

flow_analysis = ha_stubs.import_barista_module("flow_analysis")


class DefinitionTests(unittest.TestCase):
    def setUp(self) -> None:
        definitions.load_definitions.cache_clear()
        self.defs = definitions.load_definitions()

    def test_flat_white_starting_recipe(self):
        recipe = self.defs.defaults["recipe"]
        self.assertEqual(recipe["dose_g"], 18.0)
        self.assertEqual(recipe["target_yield_g"], 36.0)
        self.assertEqual(recipe["preinfusion_s"], 7)
        self.assertEqual(self.defs.defaults["controller"]["safety_margin_s"], 3)

    def test_df54_is_discrete(self):
        grind = self.defs.entity("number", "grind")
        self.assertEqual(grind.step, 0.5)

    def test_flow_analysis_constants_are_loaded(self):
        """flow_analysis.py's classification thresholds (and every other
        tunable constant it uses) come from here - FlowAnalysisConfig itself
        has no default values at all (consts live in yaml, code is for
        logic) - see runtime.py's _async_finalize and flow_analysis_constants'
        own comment in definitions.yaml."""
        constants = self.defs.flow_analysis_constants
        self.assertEqual(constants["expected_flow_g_s"], 1.3)
        self.assertEqual(constants["too_fast_factor"], 0.88)
        self.assertEqual(constants["too_restrictive_factor"], 1.10)
        # Spot-check a couple of the signal-processing ones too, not just
        # the three with a real derivation behind them.
        self.assertEqual(constants["min_samples"], 5)
        self.assertEqual(constants["smoothing_window_ms"], 500)

    def test_flow_analysis_constants_matches_flow_analysis_config_fields(self):
        """Every key here must be a real FlowAnalysisConfig field name (and
        vice versa) - runtime.py builds the config via
        FlowAnalysisConfig(**flow_analysis_constants), which raises at
        runtime on any mismatch rather than silently ignoring a typo'd or
        renamed key."""
        field_names = {f.name for f in dataclasses.fields(flow_analysis.FlowAnalysisConfig)}
        self.assertEqual(set(self.defs.flow_analysis_constants.keys()), field_names)

    def test_expert_rules_grind_correction_bands_cover_every_classification(self):
        grind_correction = self.defs.expert_rules["grind_correction"]
        self.assertEqual(
            set(grind_correction["applies_to_classification"]), {"too_fast", "too_restrictive"}
        )
        self.assertEqual(
            set(grind_correction["excludes_classification"]),
            {"puck_prep_issue", "invalid_measurement"},
        )
        self.assertTrue(grind_correction["bands"])
        # Ascending, per grind_correction.py's own assumption that the first
        # matching band (lowest duration_ratio_max) wins.
        maximums = [band["duration_ratio_max"] for band in grind_correction["bands"][:-1]]
        self.assertEqual(maximums, sorted(maximums))
        self.assertIsNone(grind_correction["bands"][-1]["duration_ratio_max"])

    def test_dashboard_tokens_are_unique(self):
        tokens = self.defs.dashboard_tokens
        self.assertEqual(len(tokens), sum(1 for p in self.defs.entities.values() for e in p if e.token))

    def test_load_definitions_reparses_once_the_file_changes_on_disk(self):
        """Regression test: definitions.yaml (and frontend/dashboard.yaml,
        cached the same way in websocket.py) used to be parsed once and
        cached for the life of the process via lru_cache, so a HACS update -
        or, during development, an edit - silently had no effect until a
        full Home Assistant restart, contradicting the documented "takes
        effect after the integration/Home Assistant reloads" behavior."""
        path = Path(definitions.__file__).with_name("definitions.yaml")
        original_mtime = path.stat().st_mtime
        self.addCleanup(os.utime, path, (original_mtime, original_mtime))

        first = definitions.load_definitions()
        self.assertIs(definitions.load_definitions(), first)  # unchanged file: cached, no reparse

        os.utime(path, (original_mtime + 5, original_mtime + 5))
        second = definitions.load_definitions()
        self.assertIsNot(second, first)  # mtime changed: detected and reparsed


if __name__ == "__main__":
    unittest.main()
