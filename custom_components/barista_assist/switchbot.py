"""Minimal direct BLE client for configuring a SwitchBot Bot."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .switchbot_protocol import (
    STATUS_OK,
    SwitchBotProtocolError,
    build_set_long_press_command,
    describe_status,
    response_status,
)

_LOGGER = logging.getLogger(__name__)

BOT_SERVICE_UUID = "cba20d00-224d-11e6-9fb8-0002a5d5c51b"
BOT_WRITE_UUID = "cba20002-224d-11e6-9fb8-0002a5d5c51b"
BOT_NOTIFY_UUID = "cba20003-224d-11e6-9fb8-0002a5d5c51b"


def resolve_bluetooth_address(hass: HomeAssistant, entity_id: str) -> str | None:
    """Resolve the BLE MAC address behind a selected HA entity."""
    entity_registry = er.async_get(hass)
    entity = entity_registry.async_get(entity_id)
    if entity is None or entity.device_id is None:
        return None
    device_registry = dr.async_get(hass)
    device = device_registry.async_get(entity.device_id)
    if device is None:
        return None
    for connection_type, connection_id in device.connections:
        if connection_type == dr.CONNECTION_BLUETOOTH:
            return connection_id
    return None


class SwitchBotBotConfigurator:
    """Open a short BLE connection solely to program Bot long-press duration."""

    def __init__(self, hass: HomeAssistant, address: str) -> None:
        self.hass = hass
        self.address = address

    async def async_set_long_press_duration(self, seconds: int) -> None:
        """Set the stored Bot long-press duration and require device confirmation."""
        device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if device is None:
            raise ConnectionError(
                "The brew SwitchBot is not currently reachable through Home Assistant Bluetooth"
            )

        client = await establish_connection(
            BleakClientWithServiceCache,
            device,
            device.name or "SwitchBot Bot",
            max_attempts=3,
        )
        response_event = asyncio.Event()
        response_box: dict[str, bytes] = {}

        def _notification(_sender: Any, data: bytearray) -> None:
            response_box["data"] = bytes(data)
            response_event.set()

        try:
            await client.start_notify(BOT_NOTIFY_UUID, _notification)
            await client.write_gatt_char(
                BOT_WRITE_UUID,
                build_set_long_press_command(seconds),
                response=True,
            )
            try:
                await asyncio.wait_for(response_event.wait(), timeout=5.0)
            except TimeoutError as err:
                raise ConnectionError(
                    "The brew SwitchBot did not confirm its long-press setting"
                ) from err
            status = response_status(response_box["data"])
            if status != STATUS_OK:
                raise SwitchBotProtocolError(describe_status(status))
        finally:
            try:
                await client.stop_notify(BOT_NOTIFY_UUID)
            except Exception:
                pass
            try:
                await client.disconnect()
            except Exception as err:
                _LOGGER.debug("SwitchBot disconnect error: %s", err)

