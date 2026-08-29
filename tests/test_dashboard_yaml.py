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


if __name__ == "__main__":
    unittest.main()
