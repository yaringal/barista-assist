import unittest

import yaml

from ha_stubs import import_barista_module

websocket = import_barista_module("websocket")


class RenderDashboardYamlTests(unittest.TestCase):
    def setUp(self):
        self.template = {
            "title": "Barista Assist",
            "views": [
                {
                    "path": "brew",
                    "cards": [{"type": "tile", "entity": "__STATUS__", "name": "Status"}],
                }
            ],
        }
        self.entity_map = {"__STATUS__": "sensor.barista_assist_status"}

    def test_substitutes_entity_tokens(self):
        text = websocket.render_dashboard_yaml(self.template, self.entity_map)
        data = yaml.safe_load(text)
        self.assertEqual(data["views"][0]["cards"][0]["entity"], "sensor.barista_assist_status")

    def test_output_is_views_only(self):
        text = websocket.render_dashboard_yaml(self.template, self.entity_map)
        data = yaml.safe_load(text)
        self.assertEqual(set(data), {"views"})
        self.assertNotIn("title:", text)

    def test_leaves_non_token_strings_untouched(self):
        text = websocket.render_dashboard_yaml(self.template, self.entity_map)
        data = yaml.safe_load(text)
        self.assertEqual(data["views"][0]["cards"][0]["name"], "Status")


class AutoPiHidesPreinfusionTileTests(unittest.TestCase):
    def setUp(self):
        self.template = {
            "title": "Barista Assist",
            "views": [
                {
                    "path": "brew",
                    "cards": [
                        {"type": "tile", "entity": "__STATUS__", "name": "Status"},
                        {"type": "tile", "entity": "__PREINFUSION__", "name": "Pre-infusion"},
                    ],
                },
                {
                    "path": "bags",
                    "cards": [
                        {
                            "type": "entities",
                            "entities": ["__DOSE__", "__PREINFUSION__"],
                        }
                    ],
                },
            ],
        }
        self.entity_map = {
            "__STATUS__": "sensor.barista_assist_status",
            "__PREINFUSION__": "number.barista_assist_preinfusion",
            "__DOSE__": "number.barista_assist_dose",
        }

    def test_auto_pi_drops_the_tile_and_list_entry(self):
        text = websocket.render_dashboard_yaml(self.template, self.entity_map, auto_pi=True)
        data = yaml.safe_load(text)
        brew_cards = data["views"][0]["cards"]
        self.assertEqual([c["entity"] for c in brew_cards], ["sensor.barista_assist_status"])
        bags_entities = data["views"][1]["cards"][0]["entities"]
        self.assertEqual(bags_entities, ["number.barista_assist_dose"])

    def test_default_keeps_the_tile_and_list_entry(self):
        text = websocket.render_dashboard_yaml(self.template, self.entity_map)
        data = yaml.safe_load(text)
        brew_cards = data["views"][0]["cards"]
        self.assertEqual(
            [c["entity"] for c in brew_cards],
            ["sensor.barista_assist_status", "number.barista_assist_preinfusion"],
        )
        bags_entities = data["views"][1]["cards"][0]["entities"]
        self.assertEqual(
            bags_entities, ["number.barista_assist_dose", "number.barista_assist_preinfusion"]
        )

    def test_auto_pi_does_not_mutate_the_shared_template(self):
        """render_dashboard_yaml is called against dashboard_template()'s
        process-wide cache - stripping must never mutate that cached object,
        or a later non-Auto-PI render would incorrectly stay stripped too."""
        websocket.render_dashboard_yaml(self.template, self.entity_map, auto_pi=True)
        self.assertEqual(
            [c["entity"] for c in self.template["views"][0]["cards"]],
            ["__STATUS__", "__PREINFUSION__"],
        )


if __name__ == "__main__":
    unittest.main()
