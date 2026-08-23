"""Stable Home Assistant action API (barista_assist.brew, etc.)."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError

from .const import DOMAIN
from .definitions import load_definitions


def _runtime(hass: HomeAssistant):
    runtime = hass.data.get(DOMAIN, {}).get("runtime")
    if runtime is None:
        raise ServiceValidationError("Barista Assist is not configured or loaded")
    return runtime


async def async_setup(hass: HomeAssistant) -> None:
    async def brew(_call: ServiceCall) -> None:
        await _runtime(hass).async_run_action("brew")

    async def abort(_call: ServiceCall) -> None:
        await _runtime(hass).async_run_action("abort")

    async def tare(_call: ServiceCall) -> None:
        await _runtime(hass).async_run_action("tare")

    hass.services.async_register(DOMAIN, "brew", brew, schema=vol.Schema({}))
    hass.services.async_register(DOMAIN, "abort", abort, schema=vol.Schema({}))
    hass.services.async_register(DOMAIN, "tare", tare, schema=vol.Schema({}))

    async def select_slot(call: ServiceCall) -> None:
        await _runtime(hass).async_select_slot(call.data["slot"])

    hass.services.async_register(
        DOMAIN,
        "select_slot",
        select_slot,
        schema=vol.Schema({vol.Required("slot"): vol.In(load_definitions().slots)}),
    )
