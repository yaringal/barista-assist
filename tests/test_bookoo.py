"""Regression test: reported against a real install - Home Assistant logged
'Detected that custom integration barista_assist calls async_write_ha_state
from a thread other than the event loop' hundreds of times per shot.

BookooUltraClient registers _notification/_disconnected directly as bleak's
raw callbacks, which are not guaranteed to run on the event loop (depends on
the platform's BLE backend). Everything downstream - runtime.py's dispatcher
signal, then each entity's async_write_ha_state - assumed it was already on
the loop. This exercises the exact failure mode: invoking the callback from
a different thread, like a BLE backend legitimately might.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ha_stubs  # noqa: E402

bookoo = ha_stubs.import_barista_module("bookoo")
protocol = ha_stubs.import_barista_module("protocol")


def _make_weight_packet() -> bytes:
    # 12,345 ms; +38.42 g; +2.16 g/s; 87% battery; 10.0 min standby.
    payload = bytearray(
        [
            0x03, 0x0B,
            0x00, 0x30, 0x39,
            0x01,
            0x2B,  # weight sign: ASCII '+'
            0x00, 0x0F, 0x02,
            0x2B,  # flow sign: ASCII '+'
            0x00, 0xD8,
            87,
            0x00, 0x64,
            2,
            0,
            0,
        ]
    )
    payload.append(protocol.xor_checksum(payload))
    return bytes(payload)


class FakeHass:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop


class BookooThreadSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_notification_from_another_thread_is_marshaled_onto_the_loop(self) -> None:
        loop = asyncio.get_running_loop()
        readings: list = []
        calling_threads: list[threading.Thread] = []

        def on_reading(reading) -> None:
            calling_threads.append(threading.current_thread())
            readings.append(reading)

        client = bookoo.BookooUltraClient(
            FakeHass(loop), "AA:BB:CC:DD:EE:FF", on_reading, lambda _connected: None
        )

        with ThreadPoolExecutor(max_workers=1) as pool:
            # Simulate a BLE backend invoking the notify callback off-thread.
            await loop.run_in_executor(
                pool, client._notification, None, _make_weight_packet()
            )
            await asyncio.sleep(0)  # let the scheduled call_soon_threadsafe run

        self.assertEqual(len(readings), 1)
        self.assertAlmostEqual(readings[0].weight_g, 38.42)
        self.assertEqual(calling_threads, [threading.main_thread()])
        self.assertIs(client.last_reading, readings[0])

    async def test_disconnected_from_another_thread_is_marshaled_onto_the_loop(self) -> None:
        loop = asyncio.get_running_loop()
        connection_states: list[bool] = []
        calling_threads: list[threading.Thread] = []

        def on_connection(connected: bool) -> None:
            calling_threads.append(threading.current_thread())
            connection_states.append(connected)

        client = bookoo.BookooUltraClient(
            FakeHass(loop), "AA:BB:CC:DD:EE:FF", lambda _reading: None, on_connection
        )
        sentinel_client = object()
        client._client = sentinel_client

        with ThreadPoolExecutor(max_workers=1) as pool:
            await loop.run_in_executor(pool, client._disconnected, sentinel_client)
            await asyncio.sleep(0)

        self.assertEqual(connection_states, [False])
        self.assertEqual(calling_threads, [threading.main_thread()])
        self.assertIsNone(client._client)


if __name__ == "__main__":
    unittest.main()
