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


class TokenSubstitutionInNestedKeysTests(unittest.TestCase):
    def test_substitutes_tokens_inside_visibility_conditions(self):
        """visibility conditions (e.g. the Pre-infusion tile's Adapt PI check)
        reference entities by token just like `entity:` does - _replace_tokens
        must substitute them too, since it walks arbitrary nested keys."""
        template = {
            "views": [
                {
                    "cards": [
                        {
                            "type": "tile",
                            "entity": "__PREINFUSION__",
                            "visibility": [
                                {"condition": "state", "entity": "__ADAPT_PI__", "state": "off"}
                            ],
                        }
                    ]
                }
            ]
        }
        entity_map = {
            "__PREINFUSION__": "number.barista_assist_preinfusion",
            "__ADAPT_PI__": "switch.barista_assist_adapt_pi",
        }
        text = websocket.render_dashboard_yaml(template, entity_map)
        data = yaml.safe_load(text)
        condition = data["views"][0]["cards"][0]["visibility"][0]
        self.assertEqual(condition["entity"], "switch.barista_assist_adapt_pi")


if __name__ == "__main__":
    unittest.main()
