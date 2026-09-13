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
        """Structural sanity, not specific numbers - every one of these is
        free to retune in definitions.yaml without breaking this test."""
        recipe = self.defs.defaults["recipe"]
        controller = self.defs.defaults["controller"]
        self.assertGreater(recipe["dose_g"], 0)
        self.assertGreater(recipe["target_yield_g"], recipe["dose_g"])
        self.assertGreater(recipe["preinfusion_s"], 0)
        self.assertGreater(controller["safety_margin_s"], 0)
        self.assertLess(controller["safety_margin_s"], controller["machine_max_shot_s"])

    def test_df54_is_discrete(self):
        """A real step size (not 0/None), whatever its current tuned
        value - grind is a stepped grinder dial, not continuous."""
        grind = self.defs.entity("number", "grind")
        self.assertIsNotNone(grind.step)
        self.assertGreater(grind.step, 0)

    def test_flow_analysis_constants_are_loaded(self):
        """flow_analysis.py's classification thresholds (and every other
        tunable constant it uses) come from here - FlowAnalysisConfig itself
        has no default values at all (consts live in yaml, code is for
        logic) - see runtime.py's _async_finalize and flow_analysis_constants'
        own comment in definitions.yaml. Checks the real structural
        invariants these values must hold, not today's specific tuned
        numbers - every one of them is free to retune in definitions.yaml
        without breaking this test."""
        constants = self.defs.flow_analysis_constants
        self.assertGreater(constants["expected_flow_g_s"], 0)
        # too_fast_factor/too_restrictive_factor bound "healthy" from below/
        # above 1.0 (duration_ratio == expected/actual) - the ordering is a
        # real requirement, not just today's chosen magnitudes.
        self.assertLess(constants["too_fast_factor"], 1.0)
        self.assertGreater(constants["too_restrictive_factor"], 1.0)
        self.assertGreater(constants["min_samples"], 0)
        self.assertGreater(constants["smoothing_window_ms"], 0)

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

    def test_flavor_feedback_delay_is_loaded(self):
        """A one-off UX knob a human retunes directly in YAML (see its own
        comment in definitions.yaml) - not a value to pin exactly, just
        confirm it parses as a real, positive delay."""
        delay = self.defs.defaults["controller"]["flavor_feedback_delay_s"]
        self.assertIsInstance(delay, (int, float))
        self.assertGreater(delay, 0)

    def test_flavor_correction_tags_match_flavor_correction_py_s_lever_map(self):
        """Every non-balanced tag's primary lever, and its escalation
        lever if one is defined, must be one flavor_correction.py's
        _LEVER_TO_FIELD map actually knows how to translate to a recipe
        field - a typo'd/renamed lever here would otherwise only surface as
        a KeyError deep inside resolve_flavor_state at runtime."""
        flavor_correction = ha_stubs.import_barista_module("flavor_correction")
        levers = set(flavor_correction._LEVER_TO_FIELD)
        tags = self.defs.expert_rules["flavor_correction"]["tags"]
        for tag, config in tags.items():
            if tag == "balanced":
                continue
            self.assertIn(config["lever"], levers, f"tag {tag!r} has an unmapped lever")
            escalation = config.get("escalation")
            if escalation is not None:
                self.assertIn(
                    escalation["lever"], levers, f"tag {tag!r}'s escalation has an unmapped lever"
                )

    def test_roast_level_ratio_prior_covers_every_roast_level_select_option(self):
        """new_bag_roast_level's options (besides "not specified") are the
        exact set roast_level_ratio_prior must have a fallback ratio for -
        runtime.py's async_new_bag only ever looks up whatever the user
        picked in that dropdown."""
        roast_levels = {
            value
            for _label, value in self.defs.entity("select", "new_bag_roast_level").options
            if value
        }
        self.assertEqual(roast_levels, set(self.defs.expert_rules["roast_level_ratio_prior"]))

    def test_roast_level_temperature_prior_covers_every_roast_level_select_option(self):
        roast_levels = {
            value
            for _label, value in self.defs.entity("select", "new_bag_roast_level").options
            if value
        }
        self.assertEqual(
            roast_levels, set(self.defs.expert_rules["roast_level_temperature_prior"])
        )

    def test_roast_level_temperature_prior_values_are_valid_offset_options(self):
        """Every value must be one of temperature_offset's own select
        options - runtime._validate_recipe_field rejects anything else."""
        valid_offsets = {
            value for _label, value in self.defs.entity("select", "temperature_offset").options
        }
        prior_values = set(self.defs.expert_rules["roast_level_temperature_prior"].values())
        self.assertTrue(prior_values.issubset(valid_offsets))

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
