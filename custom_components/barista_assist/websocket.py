"""Single WebSocket endpoint used by the package-owned dashboard strategy."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import voluptuous as vol
import yaml
from homeassistant.components import websocket_api
from homeassistant.components.websocket_api import ActiveConnection
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .definitions import load_definitions

_dashboard_cache: dict[str, Any] = {"mtime": None, "data": None}


def dashboard_template() -> Any:
    """Parsed frontend/dashboard.yaml, re-read whenever the file changes on
    disk rather than only once per process - so a HACS update (or, during
    development, an edit) takes effect on the next dashboard view instead of
    needing a full Home Assistant restart."""
    path = Path(__file__).parent / "frontend" / "dashboard.yaml"
    mtime = path.stat().st_mtime
    if _dashboard_cache["mtime"] != mtime:
        _dashboard_cache["data"] = yaml.safe_load(path.read_text(encoding="utf-8"))
        _dashboard_cache["mtime"] = mtime
    return _dashboard_cache["data"]


def _dashboard_entity_map(hass: HomeAssistant, runtime) -> dict[str, str]:
    registry = er.async_get(hass)
    result: dict[str, str] = {}
    for token, (platform, key) in load_definitions().dashboard_tokens.items():
        unique_id = f"{runtime.entry.entry_id}_{key}"
        entity_id = registry.async_get_entity_id(platform, DOMAIN, unique_id)
        if entity_id is None:
            raise RuntimeError(f"Dashboard entity {platform}.{key} is not registered")
        result[token] = entity_id
    return result


def _replace_tokens(value: Any, mapping: dict[str, str]) -> Any:
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, list):
        return [_replace_tokens(item, mapping) for item in value]
    if isinstance(value, dict):
        return {key: _replace_tokens(item, mapping) for key, item in value.items()}
    return value


@websocket_api.websocket_command({vol.Required("type"): "barista_assist/get_dashboard"})
@websocket_api.async_response
async def ws_get_dashboard(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    runtime = hass.data.get(DOMAIN, {}).get("runtime")
    if runtime is None:
        connection.send_error(msg["id"], "not_loaded", "Barista Assist is not loaded")
        return
    try:
        dashboard = _replace_tokens(
            dashboard_template(), _dashboard_entity_map(hass, runtime)
        )
    except Exception as err:
        connection.send_error(msg["id"], "dashboard_failed", str(err))
        return
    connection.send_result(msg["id"], dashboard)


@callback
def async_setup(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_get_dashboard)
    websocket_api.async_register_command(hass, ws_export_shots_text)


@websocket_api.websocket_command({vol.Required("type"): "barista_assist/export_shots_text"})
@websocket_api.async_response
async def ws_export_shots_text(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return all persisted shot data as plain text for diagnosis/pasting."""
    runtime = hass.data.get(DOMAIN, {}).get("runtime")
    if runtime is None:
        connection.send_error(msg["id"], "not_loaded", "Barista Assist is not loaded")
        return
    try:
        text = await runtime.async_export_shots_text()
    except Exception as err:
        connection.send_error(msg["id"], "export_failed", str(err))
        return
    connection.send_result(msg["id"], {"text": text})
