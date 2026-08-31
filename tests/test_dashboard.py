from pathlib import Path
import importlib.util
import re
import sys
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "custom_components" / "barista_assist" / "frontend" / "dashboard.yaml"
DEFINITIONS = ROOT / "custom_components" / "barista_assist" / "definitions.py"
spec = importlib.util.spec_from_file_location("barista_definitions_dashboard", DEFINITIONS)
definitions = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules["barista_definitions_dashboard"] = definitions
spec.loader.exec_module(definitions)


class DashboardTests(unittest.TestCase):
    def test_dashboard_yaml_is_valid_and_has_expected_views(self):
        data = yaml.safe_load(DASHBOARD.read_text(encoding="utf-8"))
        self.assertEqual(data["title"], "Barista Assist")
        self.assertEqual(
            [view["path"] for view in data["views"]], ["brew", "bags", "system", "shots"]
        )

    def test_every_placeholder_is_declared(self):
        text = DASHBOARD.read_text(encoding="utf-8")
        used = set(re.findall(r"__[A-Z0-9_]+__", text))
        declared = set(definitions.load_definitions().dashboard_tokens)
        self.assertTrue(used)
        self.assertEqual(used, declared)

    def test_dashboard_has_shot_export_card(self):
        text = DASHBOARD.read_text(encoding='utf-8')
        self.assertIn('custom:barista-assist-export-card', text)
        self.assertIn('Copy all shot data', text)

    def test_dashboard_has_shot_history_card(self):
        text = DASHBOARD.read_text(encoding='utf-8')
        self.assertIn('custom:barista-assist-shot-history-card', text)

    def test_dashboard_documents_new_default_yield(self):
        text = DASHBOARD.read_text(encoding="utf-8")
        self.assertIn("36.0 g yield", text)
        self.assertNotIn("38 g / 0", text)

    def test_every_tile_card_has_an_explicit_short_name(self):
        """Without an explicit `name:`, a tile card's header falls back to the
        entity's has_entity_name friendly name, which is prefixed with the
        device name ("Barista Assist") and overflows the tile."""
        data = yaml.safe_load(DASHBOARD.read_text(encoding="utf-8"))
        for view in data["views"]:
            for section in view.get("sections", []):
                for card in section.get("cards", []):
                    if card.get("type") == "tile":
                        self.assertIn(
                            "name", card, f"tile for {card.get('entity')!r} has no name"
                        )


if __name__ == "__main__":
    unittest.main()
