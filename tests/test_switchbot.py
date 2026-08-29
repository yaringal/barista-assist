"""Regression test: switchbot.py's Bot-response notification callback was
registered directly as bleak's raw callback and touched a plain
asyncio.Event/dict from whatever thread bleak calls it from - the same class
of bug fixed in bookoo.py's _notification/_disconnected (see test_bookoo.py).
asyncio.Event.set() is not thread-safe, so this exercises the callback firing
from a background thread exactly like a real BLE backend might.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ha_stubs  # noqa: E402

switchbot = ha_stubs.import_barista_module("switchbot")
switchbot_protocol = ha_stubs.import_barista_module("switchbot_protocol")


class FakeBleClient:
    def __init__(self) -> None:
        self.notify_callback = None
        self.written: list[bytes] = []

    async def start_notify(self, _uuid, callback) -> None:
        self.notify_callback = callback

    async def write_gatt_char(self, _uuid, data, response=True) -> None:
        self.written.append(data)

    async def stop_notify(self, _uuid) -> None:
        pass

    async def disconnect(self) -> None:
        pass


class SwitchBotThreadSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_notification_from_another_thread_is_marshaled_onto_the_loop(self) -> None:
        fake_client = FakeBleClient()

        async def fake_establish_connection(*_args, **_kwargs):
            return fake_client

        calling_threads: list[threading.Thread] = []
        original_set = asyncio.Event.set

        def spy_set(self) -> None:
            calling_threads.append(threading.current_thread())
            original_set(self)

        configurator = switchbot.SwitchBotBotConfigurator(
            ha_stubs.sys.modules["homeassistant.core"].HomeAssistant(),
            "AA:BB:CC:DD:EE:FF",
        )

        with (
            patch.object(
                switchbot.bluetooth,
                "async_ble_device_from_address",
                return_value=SimpleNamespace(name="Test Bot"),
            ),
            patch.object(switchbot, "establish_connection", fake_establish_connection),
            patch.object(asyncio.Event, "set", spy_set),
        ):
            task = asyncio.ensure_future(configurator.async_set_long_press_duration(5))

            async def _wait_for_notify_registration() -> None:
                while fake_client.notify_callback is None:
                    await asyncio.sleep(0)

            await asyncio.wait_for(_wait_for_notify_registration(), timeout=5)

            response = bytes([switchbot_protocol.STATUS_OK])
            with ThreadPoolExecutor(max_workers=1) as pool:
                await asyncio.get_running_loop().run_in_executor(
                    pool, fake_client.notify_callback, None, bytearray(response)
                )
            await asyncio.wait_for(task, timeout=5)

        self.assertEqual(calling_threads, [threading.main_thread()])


if __name__ == "__main__":
    unittest.main()
