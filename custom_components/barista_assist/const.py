"""Small set of non-declarative constants for Barista Assist."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Final

DOMAIN: Final = "barista_assist"
NAME: Final = "Barista Assist"

CONF_SCALE_ADDRESS: Final = "scale_address"
CONF_BREW_ENTITY: Final = "brew_entity"
CONF_MACHINE_MAX_SHOT_SECONDS: Final = "machine_max_shot_seconds"
CONF_SAFETY_MARGIN_SECONDS: Final = "safety_margin_seconds"
CONF_MACHINE_LIMIT_CONFIRMED: Final = "machine_limit_confirmed"
CONF_AUTO_PI: Final = "auto_pi"

STATIC_URL_PATH: Final = "/barista_assist_static"

BOOKOO_SERVICE_UUID: Final = "00000ffe-0000-1000-8000-00805f9b34fb"
BOOKOO_COMMAND_UUID: Final = "0000ff12-0000-1000-8000-00805f9b34fb"
BOOKOO_WEIGHT_UUID: Final = "0000ff11-0000-1000-8000-00805f9b34fb"

SIGNAL_UPDATE: Final = f"{DOMAIN}_update"


@lru_cache(maxsize=1)
def integration_version() -> str:
    """Read the installed package version from manifest.json."""
    manifest = Path(__file__).with_name("manifest.json")
    return str(json.loads(manifest.read_text(encoding="utf-8"))["version"])


DASHBOARD_RESOURCE: Final = (
    f"{STATIC_URL_PATH}/barista-assist-dashboard.js?v={integration_version()}"
)

# Written into the Home Assistant config directory as a YAML-mode Lovelace
# dashboard file (see PUBLISHING.md/README.md for the one-time
# configuration.yaml block that references it) - regenerated on every
# startup/reload so it still auto-updates with each release, without
# depending on the browser-side dashboard-strategy registration mechanism.
DASHBOARD_FILENAME: Final = "barista_assist_dashboard.yaml"
