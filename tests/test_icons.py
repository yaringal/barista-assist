"""Every mdi: icon name referenced by the integration must actually exist.

Guards against a real bug that shipped twice: `mdi:coffee-beans`, then the
"corrected" `mdi:coffee-bean`, were both invented names that don't exist in
Material Design Icons - Home Assistant just renders a blank icon for an
unknown name instead of erroring, so nothing caught it until a live user
reported the blank icon after each release. `fixtures/mdi_icon_names.txt` is
a snapshot of every real icon name (from Templarian/MaterialDesign's
meta.json); MDI practically never removes icons, so a stale snapshot can
only under-report new icons, never wrongly flag a real one as invalid.
"""

import json
from pathlib import Path
import re
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "custom_components" / "barista_assist"
ICON_NAME_RE = re.compile(r"mdi:[a-z0-9-]+")


def _known_icon_names() -> set[str]:
    text = (Path(__file__).parent / "fixtures" / "mdi_icon_names.txt").read_text(encoding="utf-8")
    return {line.strip() for line in text.splitlines() if line.strip()}


def _icon_refs_in(path: Path) -> set[str]:
    return set(ICON_NAME_RE.findall(path.read_text(encoding="utf-8")))


class IconNameTests(unittest.TestCase):
    def test_icons_json_uses_only_real_mdi_names(self):
        known = _known_icon_names()
        used = _icon_refs_in(PACKAGE / "icons.json")
        self.assertTrue(used)
        unknown = {icon for icon in used if icon.removeprefix("mdi:") not in known}
        self.assertEqual(unknown, set())

    def test_dashboard_yaml_uses_only_real_mdi_names(self):
        known = _known_icon_names()
        used = _icon_refs_in(PACKAGE / "frontend" / "dashboard.yaml")
        self.assertTrue(used)
        unknown = {icon for icon in used if icon.removeprefix("mdi:") not in known}
        self.assertEqual(unknown, set())

    def test_icons_json_is_well_formed(self):
        json.loads((PACKAGE / "icons.json").read_text(encoding="utf-8"))

    def test_dashboard_yaml_view_icons_are_declared(self):
        data = yaml.safe_load((PACKAGE / "frontend" / "dashboard.yaml").read_text(encoding="utf-8"))
        for view in data["views"]:
            self.assertIn("icon", view, f"view {view.get('path')!r} has no icon")


if __name__ == "__main__":
    unittest.main()
