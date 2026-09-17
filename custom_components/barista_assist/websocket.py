"""The shot-export websocket endpoint, plus generating the packaged
dashboard's YAML-mode file (see const.DASHBOARD_FILENAME)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import voluptuous as vol
import yaml
from homeassistant.components import websocket_api
from homeassistant.components.websocket_api import ActiveConnection
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .const import DASHBOARD_FILENAME, DOMAIN
from .definitions import load_definitions

_dashboard_cache: dict[str, Any] = {"mtime": None, "data": None}


def dashboard_template() -> Any:
    """Parsed frontend/dashboard.yaml, re-read whenever the file changes on
    disk rather than only once per process - so a HACS update (or, during
    development, an edit) takes effect on the next regeneration instead of
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
        # A whole-string match (the common case: entity: __GRIND__) is just
        # the single-occurrence case of this same replace - but a card
        # condition's own value_template (a Jinja string) needs a token
        # substituted *inside* a larger string, e.g.
        # "{{ state_attr('__GRIND__', 'recommended') ... }}", so every
        # occurrence of every token is replaced, not just an exact match.
        for token, entity_id in mapping.items():
            if token in value:
                value = value.replace(token, entity_id)
        return value
    if isinstance(value, list):
        return [_replace_tokens(item, mapping) for item in value]
    if isinstance(value, dict):
        return {key: _replace_tokens(item, mapping) for key, item in value.items()}
    return value


def render_dashboard_yaml(template: Any, entity_map: dict[str, str]) -> str:
    """Token-substituted `views:`-only YAML for a YAML-mode Lovelace
    dashboard file. Only `views` is kept - the file's own title/icon come
    from the user's configuration.yaml entry, per Home Assistant's YAML
    dashboard format, not from this package's dashboard.yaml."""
    substituted = _replace_tokens(template, entity_map)
    return yaml.safe_dump({"views": substituted["views"]}, sort_keys=False)


async def async_write_dashboard_file(hass: HomeAssistant, runtime) -> None:
    """(Re)generate the dashboard file a YAML-mode Lovelace dashboard reads
    from, so the packaged dashboard keeps auto-updating with every release
    without depending on browser-side dashboard-strategy registration."""
    # Both do blocking file I/O on a cache miss (e.g. dashboard.yaml or
    # definitions.yaml changed since the last read), so both are forced
    # through the executor rather than ever risking that on the event loop.
    template = await hass.async_add_executor_job(dashboard_template)
    await hass.async_add_executor_job(load_definitions)
    # _dashboard_entity_map's own load_definitions() call is now a cache
    # hit; its entity-registry lookups must still run on the event loop.
    yaml_text = render_dashboard_yaml(template, _dashboard_entity_map(hass, runtime))
    path = Path(hass.config.path(DASHBOARD_FILENAME))
    await hass.async_add_executor_job(path.write_text, yaml_text, "utf-8")


@callback
def async_setup(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_export_shots_text)
    websocket_api.async_register_command(hass, ws_list_shots)
    websocket_api.async_register_command(hass, ws_shot_samples)
    websocket_api.async_register_command(hass, ws_delete_shot)


def _get_runtime(hass: HomeAssistant, connection: ActiveConnection, msg_id: int):
    runtime = hass.data.get(DOMAIN, {}).get("runtime")
    if runtime is None:
        connection.send_error(msg_id, "not_loaded", "Barista Assist is not loaded")
    return runtime


@websocket_api.websocket_command(
    {
        vol.Required("type"): "barista_assist/export_shots_text",
        vol.Optional("shot_id"): str,
    }
)
@websocket_api.async_response
async def ws_export_shots_text(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return persisted shot data as plain text for diagnosis/pasting - every
    shot, or just one when shot_id is given (the shot-history card's per-row
    export button)."""
    runtime = _get_runtime(hass, connection, msg["id"])
    if runtime is None:
        return
    try:
        text = await runtime.async_export_shots_text(msg.get("shot_id"))
    except Exception as err:
        connection.send_error(msg["id"], "export_failed", str(err))
        return
    connection.send_result(msg["id"], {"text": text})


@websocket_api.websocket_command({vol.Required("type"): "barista_assist/list_shots"})
@websocket_api.async_response
async def ws_list_shots(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return every stored shot, most recent first, for the shot-history view."""
    runtime = _get_runtime(hass, connection, msg["id"])
    if runtime is None:
        return
    try:
        shots = await runtime.async_list_shots()
    except Exception as err:
        connection.send_error(msg["id"], "list_failed", str(err))
        return
    connection.send_result(msg["id"], {"shots": shots})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "barista_assist/shot_samples",
        vol.Required("shot_id"): str,
        vol.Optional("stop_command_elapsed_ms"): int,
    }
)
@websocket_api.async_response
async def ws_shot_samples(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return one shot's raw scale time series, for the shot-history graph -
    plus its own predicted_stop_elapsed_ms (see runtime_shot.py's
    async_shot_samples) when the caller passes stop_command_elapsed_ms
    (already on the shot row from list_shots)."""
    runtime = _get_runtime(hass, connection, msg["id"])
    if runtime is None:
        return
    try:
        result = await runtime.async_shot_samples(
            msg["shot_id"], stop_command_elapsed_ms=msg.get("stop_command_elapsed_ms")
        )
    except Exception as err:
        connection.send_error(msg["id"], "samples_failed", str(err))
        return
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {vol.Required("type"): "barista_assist/delete_shot", vol.Required("shot_id"): str}
)
@websocket_api.async_response
async def ws_delete_shot(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Delete one stored shot and its raw samples."""
    runtime = _get_runtime(hass, connection, msg["id"])
    if runtime is None:
        return
    try:
        deleted = await runtime.async_delete_shot(msg["shot_id"])
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "delete_refused", str(err))
        return
    except Exception as err:
        connection.send_error(msg["id"], "delete_failed", str(err))
        return
    if not deleted:
        connection.send_error(msg["id"], "not_found", "Shot not found")
        return
    connection.send_result(msg["id"], {"deleted": True})
