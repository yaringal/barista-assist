"""Barista Assist custom integration."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from . import services, websocket
from .const import DASHBOARD_RESOURCE, DOMAIN, STATIC_URL_PATH
from .definitions import load_definitions
from .runtime import BaristaRuntime

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BUTTON,
    Platform.DATE,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
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
            "dashboard_resource_registered": False,
        },
    )
    await services.async_setup(hass)
    websocket.async_setup(hass)
    return True


async def _async_register_frontend(hass: HomeAssistant) -> None:
    data = hass.data[DOMAIN]
    if not data["static_registered"]:
        frontend_dir = Path(__file__).parent / "frontend"
        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL_PATH, str(frontend_dir), True)]
        )
        data["static_registered"] = True
    if not data["dashboard_resource_registered"]:
        frontend.add_extra_js_url(hass, DASHBOARD_RESOURCE)
        data["dashboard_resource_registered"] = True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


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
    if hass.data[DOMAIN]["dashboard_resource_registered"]:
        frontend.remove_extra_js_url(hass, DASHBOARD_RESOURCE)
        hass.data[DOMAIN]["dashboard_resource_registered"] = False
    return True
