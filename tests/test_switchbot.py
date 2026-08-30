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
    def __init__(self, fail_start_notify_times: int = 0) -> None:
        self.notify_callback = None
        self.written: list[bytes] = []
        self._fail_start_notify_times = fail_start_notify_times

    async def start_notify(self, _uuid, callback) -> None:
        if self._fail_start_notify_times > 0:
            self._fail_start_notify_times -= 1
            raise sys.modules["bleak.exc"].BleakError(
                "[org.bluez.Error.NotConnected] Not Connected"
            )
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


class RetryOnTransientDisconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_notify_failure_retries_the_whole_connection(self) -> None:
        """Regression test: a BLE peripheral is free to drop the link right
        after connecting, before the first GATT operation lands - observed
        live as `BleakDBusError: [org.bluez.Error.NotConnected]` from
        start_notify immediately after establish_connection had already
        succeeded. async_set_long_press_duration must reconnect and retry
        the GATT sequence exactly once rather than fail outright on one
        transient disconnect."""
        attempted_clients: list[FakeBleClient] = []

        async def fake_establish_connection(*_args, **_kwargs):
            client = FakeBleClient(fail_start_notify_times=1 if not attempted_clients else 0)
            attempted_clients.append(client)
            return client

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
        ):
            task = asyncio.ensure_future(configurator.async_set_long_press_duration(0))

            async def _wait_for_notify_registration() -> None:
                while not attempted_clients or attempted_clients[-1].notify_callback is None:
                    await asyncio.sleep(0)

            await asyncio.wait_for(_wait_for_notify_registration(), timeout=5)
            response = bytes([switchbot_protocol.STATUS_OK])
            attempted_clients[-1].notify_callback(None, bytearray(response))
            await asyncio.wait_for(task, timeout=5)

        self.assertEqual(len(attempted_clients), 2)  # first attempt failed, second succeeded

    async def test_a_hard_connect_failure_is_retried_exactly_once(self) -> None:
        """Regression test with two contradictory failure modes to balance:

        (1) A real install got stuck for a long time with the brew Bot
        completely unresponsive after establish_connection() failed outright
        (BleakOutOfConnectionSlotsError). establish_connection() already
        retries internally and its own attempt count can reach the high
        single digits on real hardware; retrying the whole connect step
        3 more times on top of that (0.2.10's behavior) multiplies an
        already-slow, often unrecoverable failure several times over.

        (2) But retrying the connect step zero times (this method's very
        next fix) turned out to regress a *different*, real, working case:
        a connection that's genuinely just transient/marginal rather than
        truly unavailable - 0.2.10 would sometimes succeed on a second or
        third attempt where connecting only once failed outright.

        One retry (two total attempts) is the balance: enough to recover the
        transient case, bounded enough not to reproduce the multi-minute
        stall."""
        connect_attempts = 0

        async def failing_establish_connection(*_args, **_kwargs):
            nonlocal connect_attempts
            connect_attempts += 1
            raise sys.modules["bleak.exc"].BleakError("out of connection slots")

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
            patch.object(switchbot, "establish_connection", failing_establish_connection),
        ):
            with self.assertRaises(sys.modules["bleak.exc"].BleakError):
                await asyncio.wait_for(
                    configurator.async_set_long_press_duration(0), timeout=5
                )

        self.assertEqual(connect_attempts, 2)  # retried once, then stopped - not 1, not 3+

    async def test_a_connect_failure_followed_by_success_recovers(self) -> None:
        """The actual scenario this balance is meant to recover: the first
        connect attempt fails (transient/marginal contention), the second
        succeeds - matching what 0.2.10 apparently relied on."""
        attempted_clients: list[FakeBleClient] = []
        connect_attempts = 0

        async def flaky_establish_connection(*_args, **_kwargs):
            nonlocal connect_attempts
            connect_attempts += 1
            if connect_attempts == 1:
                raise sys.modules["bleak.exc"].BleakError("out of connection slots")
            client = FakeBleClient()
            attempted_clients.append(client)
            return client

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
            patch.object(switchbot, "establish_connection", flaky_establish_connection),
        ):
            task = asyncio.ensure_future(configurator.async_set_long_press_duration(0))

            async def _wait_for_notify_registration() -> None:
                while not attempted_clients or attempted_clients[-1].notify_callback is None:
                    await asyncio.sleep(0)

            await asyncio.wait_for(_wait_for_notify_registration(), timeout=5)
            response = bytes([switchbot_protocol.STATUS_OK])
            attempted_clients[-1].notify_callback(None, bytearray(response))
            await asyncio.wait_for(task, timeout=5)

        self.assertEqual(connect_attempts, 2)


if __name__ == "__main__":
    unittest.main()
