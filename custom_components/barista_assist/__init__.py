"""Barista Assist custom integration."""

from __future__ import annotations

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
    load_definitions()  # Fail early if package YAML is invalid.
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
    runtime = BaristaRuntime(hass, entry)
    entry.runtime_data = runtime
    hass.data[DOMAIN]["runtime"] = runtime
    try:
        await runtime.async_initialize()
        await _async_register_frontend(hass)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
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
