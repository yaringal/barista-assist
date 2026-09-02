"""Barista Assist custom integration."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from . import services, websocket
from .const import DOMAIN, STATIC_URL_PATH
from .definitions import load_definitions
from .runtime import BaristaRuntime

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BUTTON,
    Platform.DATE,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TEXT,
]


async def async_setup(hass: HomeAssistant, config) -> bool:
    """Set up integration-level actions and dashboard endpoint."""
    # Both do blocking file I/O, so both must go through the executor rather
    # than running directly on the event loop (fail early if package YAML is
    # invalid; warm the dashboard cache outside the request path).
    await hass.async_add_executor_job(load_definitions)
    await hass.async_add_executor_job(websocket.dashboard_template)
    hass.data.setdefault(
        DOMAIN,
        {
            "runtime": None,
            "static_registered": False,
        },
    )
    await services.async_setup(hass)
    websocket.async_setup(hass)
    return True


async def _async_register_frontend(hass: HomeAssistant) -> None:
    """Serve this integration's bundled frontend assets (the custom Lovelace
    cards). Deliberately does NOT also call frontend.add_extra_js_url: that
    injects a <script> tag into the server-rendered frontend shell HTML,
    which is a different (and, in practice, unreliable - see the setup
    docs) loading path than a real Lovelace resource, which the
    already-running frontend fetches and injects itself. Registering a
    Lovelace resource programmatically from an integration isn't safe
    either - a still-open Home Assistant core bug means doing so before the
    frontend has loaded the resource collection can silently wipe out every
    *other* resource on the system too. So: this only serves the file: the
    one-time manual "add a Lovelace resource" step in the README's "Add the
    dashboard once" section is what actually makes it load, reliably."""
    data = hass.data[DOMAIN]
    if not data["static_registered"]:
        frontend_dir = Path(__file__).parent / "frontend"
        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL_PATH, str(frontend_dir), False)]
        )
        data["static_registered"] = True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


# Declarative entity keys that were renamed (definitions.yaml key -> old key).
# A definitions.yaml key rename alone doesn't touch an already-registered
# entity's unique_id ("{entry_id}_{key}", see entity.py) - left alone, the
# old unique_id becomes a permanently unavailable, greyed-out orphan while a
# brand new entity appears under the new key, silently losing whatever the
# user had it set to. Remapping the registry entry's unique_id in place
# instead carries its entity_id/history/any automations referencing it
# forward under the new key, exactly as if it had never been renamed.
_RENAMED_ENTITY_KEYS = {
    ("number", "stop_compensation"): "early_stop_margin_min",
}


def _migrate_renamed_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    registry = er.async_get(hass)
    for (domain, old_key), new_key in _RENAMED_ENTITY_KEYS.items():
        old_unique_id = f"{entry.entry_id}_{old_key}"
        old_entity_id = registry.async_get_entity_id(domain, DOMAIN, old_unique_id)
        if old_entity_id is None:
            continue
        new_unique_id = f"{entry.entry_id}_{new_key}"
        if registry.async_get_entity_id(domain, DOMAIN, new_unique_id) is not None:
            continue  # already migrated (or somehow both exist) - don't clobber
        registry.async_update_entity(old_entity_id, new_unique_id=new_unique_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # Ensure load_definitions()'s cache is warm (and any reparse it needs -
    # e.g. definitions.yaml changed since Home Assistant started - happens
    # here, in the executor) before BaristaRuntime's synchronous __init__
    # and the platform setups below call it directly on the event loop.
    await hass.async_add_executor_job(load_definitions)
    runtime = BaristaRuntime(hass, entry)
    entry.runtime_data = runtime
    hass.data[DOMAIN]["runtime"] = runtime
    try:
        await runtime.async_initialize()
        _migrate_renamed_entities(hass, entry)
        await _async_register_frontend(hass)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
        try:
            await websocket.async_write_dashboard_file(hass, runtime)
        except Exception as err:
            # A nice-to-have convenience file, not core functionality - a
            # failure here (e.g. an unwritable config directory) shouldn't
            # take down the whole integration.
            _LOGGER.warning("Could not write the Barista Assist dashboard file: %s", err)
    except Exception:
        hass.data[DOMAIN]["runtime"] = None
        await runtime.async_close()
        raise
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False
    runtime = hass.data[DOMAIN].get("runtime")
    hass.data[DOMAIN]["runtime"] = None
    if runtime:
        await runtime.async_close()
    return True
