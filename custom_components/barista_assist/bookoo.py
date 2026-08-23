"""Direct BLE client for the BOOKOO Themis Ultra."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
from typing import Any

from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

from .const import BOOKOO_COMMAND_UUID, BOOKOO_WEIGHT_UUID
from .protocol import BookooProtocolError, BookooReading, build_command, parse_weight_packet

_LOGGER = logging.getLogger(__name__)

ReadingCallback = Callable[[BookooReading], None]
ConnectionCallback = Callable[[bool], None]


class BookooUltraClient:
    """Own the active BOOKOO BLE connection and notifications."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        on_reading: ReadingCallback,
        on_connection: ConnectionCallback,
    ) -> None:
        self.hass = hass
        self.address = address
        self._on_reading = on_reading
        self._on_connection = on_connection
        self._client: BleakClientWithServiceCache | None = None
        self._connect_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._reading_event = asyncio.Event()
        self._runner: asyncio.Task[None] | None = None
        self.last_reading: BookooReading | None = None

    @property
    def connected(self) -> bool:
        return bool(self._client and self._client.is_connected)

    async def async_start(self) -> None:
        """Start opportunistic background reconnection."""
        if self._runner is None:
            self._runner = self.hass.async_create_background_task(
                self._reconnect_loop(), "barista_assist_bookoo_reconnect"
            )

    async def async_stop(self) -> None:
        """Stop reconnecting and disconnect."""
        self._stop_event.set()
        if self._runner:
            self._runner.cancel()
            try:
                await self._runner
            except asyncio.CancelledError:
                pass
            self._runner = None
        await self.async_disconnect()

    async def _reconnect_loop(self) -> None:
        while not self._stop_event.is_set():
            if not self.connected:
                device = bluetooth.async_ble_device_from_address(
                    self.hass, self.address, connectable=True
                )
                if device is not None:
                    try:
                        await self.async_ensure_connected()
                    except Exception as err:  # BLE failures are expected/retriable.
                        _LOGGER.debug("BOOKOO reconnect failed: %s", err)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=5.0)
            except TimeoutError:
                continue

    async def async_ensure_connected(self) -> None:
        """Connect and subscribe if needed."""
        if self.connected:
            return
        async with self._connect_lock:
            if self.connected:
                return
            device = bluetooth.async_ble_device_from_address(
                self.hass, self.address, connectable=True
            )
            if device is None:
                raise ConnectionError(
                    "The BOOKOO scale is not currently reachable by a connectable Bluetooth adapter"
                )
            client = await establish_connection(
                BleakClientWithServiceCache,
                device,
                device.name or "BOOKOO Themis Ultra",
                disconnected_callback=self._disconnected,
                max_attempts=3,
            )
            try:
                await client.start_notify(BOOKOO_WEIGHT_UUID, self._notification)
            except Exception:
                await client.disconnect()
                raise
            self._client = client
            self._on_connection(True)
            _LOGGER.debug("Connected to BOOKOO scale %s", self.address)

    async def async_disconnect(self) -> None:
        client, self._client = self._client, None
        if client and client.is_connected:
            try:
                await client.disconnect()
            except Exception as err:
                _LOGGER.debug("BOOKOO disconnect error: %s", err)
        self._on_connection(False)

    def _disconnected(self, client: Any) -> None:
        """Handle an unsolicited BLE disconnect."""
        if self._client is client:
            self._client = None
        self._on_connection(False)

    def _notification(self, _sender: Any, data: bytearray) -> None:
        try:
            reading = parse_weight_packet(bytes(data))
        except BookooProtocolError as err:
            _LOGGER.debug("Ignoring invalid BOOKOO packet: %s", err)
            return
        self.last_reading = reading
        self._reading_event.set()
        self._on_reading(reading)

    async def async_wait_for_fresh_reading(self, timeout: float = 5.0) -> BookooReading:
        """Wait for a notification received after this method is called."""
        self._reading_event.clear()
        await asyncio.wait_for(self._reading_event.wait(), timeout=timeout)
        assert self.last_reading is not None
        return self.last_reading

    async def async_wait_for_reading(self, timeout: float = 5.0) -> BookooReading:
        """Wait until at least one valid scale notification is available."""
        if self.last_reading is not None:
            return self.last_reading
        self._reading_event.clear()
        await asyncio.wait_for(self._reading_event.wait(), timeout=timeout)
        assert self.last_reading is not None
        return self.last_reading

    async def _write_command(self, command: int, data2: int = 0, data3: int = 0) -> None:
        await self.async_ensure_connected()
        assert self._client is not None
        await self._client.write_gatt_char(
            BOOKOO_COMMAND_UUID, build_command(command, data2, data3)
        )

    async def async_tare(self) -> None:
        await self._write_command(0x01)

    async def async_tare_and_start_timer(self) -> None:
        # BOOKOO recommends command 0x07 for tare + timer start.
        await self._write_command(0x07)

    async def async_set_flow_smoothing(self, enabled: bool) -> None:
        await self._write_command(0x08, 0x01 if enabled else 0x00, 0x00)
