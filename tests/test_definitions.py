from pathlib import Path
import importlib.util
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


if __name__ == "__main__":
    unittest.main()
