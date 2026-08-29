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
