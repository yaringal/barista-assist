"""Fakes for driving BaristaRuntime in tests without real HA/BLE/Bluetooth.

BaristaRuntime constructs its own Store and BookooUltraClient, and calls out
to SwitchBotBotConfigurator/resolve_bluetooth_address from inside runtime.py.
Tests patch those names on the imported runtime module (see build_runtime())
so BaristaRuntime's real code runs unmodified against these fakes.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path


class FakeState:
    def __init__(self, attributes: dict | None = None) -> None:
        self.attributes = attributes or {}


class FakeStates:
    def __init__(self) -> None:
        self._states: dict[str, FakeState] = {}

    def set(self, entity_id: str, attributes: dict | None = None) -> None:
        self._states[entity_id] = FakeState(attributes)

    def get(self, entity_id: str) -> FakeState | None:
        return self._states.get(entity_id)


class FakeServices:
    """Records switch.turn_on calls; can be told to fail the next N calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.fail_next = 0
        self._fail_exc: Exception | None = None

    def fail_next_call(self, exc: Exception | None = None) -> None:
        self.fail_next += 1
        self._fail_exc = exc

    async def async_call(self, domain, service, data, blocking=True) -> None:
        self.calls.append((domain, service, dict(data)))
        if self.fail_next:
            self.fail_next -= 1
            raise (self._fail_exc or RuntimeError("simulated switch.turn_on failure"))


class FakeConfig:
    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir

    def path(self, *parts: str) -> str:
        return str(self._base_dir.joinpath(*parts))


class FakeHass:
    """Runs executor jobs and background tasks immediately/eagerly on the
    real asyncio event loop, so tests can await real concurrency/races.
    """

    def __init__(self, base_dir: Path) -> None:
        self.config = FakeConfig(base_dir)
        self.states = FakeStates()
        self.services = FakeServices()
        self.data: dict = {}
        # Every task handed to us, in creation order, so tests can await the
        # exact task a call just scheduled instead of guessing with sleep(0).
        self.tasks: list[asyncio.Task] = []

    async def async_add_executor_job(self, func, *args):
        return func(*args)

    def async_create_task(self, coro, name: str | None = None) -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        self.tasks.append(task)
        return task

    def async_create_background_task(self, coro, name: str | None = None) -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        self.tasks.append(task)
        return task


class FakeConfigEntry:
    def __init__(self, entry_id: str, data: dict, options: dict) -> None:
        self.entry_id = entry_id
        self.data = data
        self.options = options


class FakeScale:
    """Stands in for BookooUltraClient: no BLE, just records calls and lets
    tests push readings straight through the runtime's own callback.
    """

    def __init__(self, hass, address, on_reading, on_connection) -> None:
        self.hass = hass
        self.address = address
        self._on_reading = on_reading
        self._on_connection = on_connection
        self.last_reading = None
        self.started = False
        self.tare_and_start_timer_calls = 0
        self.set_flow_smoothing_calls: list[bool] = []

    async def async_start(self) -> None:
        self.started = True

    async def async_stop(self) -> None:
        self.started = False

    async def async_ensure_connected(self) -> None:
        pass

    async def async_wait_for_fresh_reading(self, timeout: float = 5.0):
        return self.last_reading

    async def async_set_flow_smoothing(self, enabled: bool) -> None:
        self.set_flow_smoothing_calls.append(enabled)

    async def async_tare_and_start_timer(self) -> None:
        self.tare_and_start_timer_calls += 1

    async def async_tare(self) -> None:
        pass

    def push_reading(self, reading) -> None:
        """Simulate a BLE notification arriving, exactly like the real client."""
        self.last_reading = reading
        self._on_reading(reading)


class FakeBotConfigurator:
    """Stands in for SwitchBotBotConfigurator: no BLE, just records the
    requested hold duration and can simulate a slow or failing reprogram.
    """

    calls: list[int] = []
    fail_with: Exception | None = None
    # If set, the NEXT call blocks on this event and then clears it, so only
    # that one call is slow/stuck; later calls proceed normally.
    delay_once: asyncio.Event | None = None

    def __init__(self, hass, address) -> None:
        self.hass = hass
        self.address = address

    async def async_set_long_press_duration(self, seconds: int) -> None:
        type(self).calls.append(seconds)
        event = type(self).delay_once
        if event is not None:
            type(self).delay_once = None
            await event.wait()
        if type(self).fail_with is not None:
            raise type(self).fail_with

    @classmethod
    def reset(cls) -> None:
        cls.calls = []
        cls.fail_with = None
        cls.delay_once = None


def make_temp_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="barista_assist_test_"))
