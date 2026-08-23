from pathlib import Path
import importlib.util
import sys
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ROOT / "custom_components" / "barista_assist" / "services.yaml"
DEFINITIONS = ROOT / "custom_components" / "barista_assist" / "definitions.py"
spec = importlib.util.spec_from_file_location("barista_definitions_services", DEFINITIONS)
definitions = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules["barista_definitions_services"] = definitions
spec.loader.exec_module(definitions)


class ServicesTests(unittest.TestCase):
    def test_select_slot_options_match_definitions_slots(self):
        """services.yaml's selector list is hand-maintained UI metadata, not
        validated by voluptuous at runtime (services.py validates against
        definitions.yaml's slots directly) - so it can silently drift from
        the declared source of truth. Catch that here instead."""
        data = yaml.safe_load(SERVICES.read_text(encoding="utf-8"))
        declared_options = data["select_slot"]["fields"]["slot"]["selector"]["select"]["options"]
        self.assertEqual(list(declared_options), list(definitions.load_definitions().slots))


if __name__ == "__main__":
    unittest.main()
