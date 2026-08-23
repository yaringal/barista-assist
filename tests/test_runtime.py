"""Regression tests for the shot state machine in runtime.py.

Each test below is a direct regression test for one of the bugs found and
fixed in BaristaRuntime's shot-stop/abort logic:

- Auto-stop must actually press the brew Bot (the stop_scheduled/
  stop_triggered flag conflation previously made this a no-op).
- A failed stop/abort press must not be reported as a successful one.
- The stop/abort press must be reprogrammed to an instant tap rather than
  holding for the configured pre-infusion duration.
- A stop/abort must never wait for, nor race, an in-flight proactive
  reprogram attempt for the same Bot.
- The controller must never press the Bot once the protected shot deadline
  has passed.

These run against fakes for hass/BLE (see ha_stubs.py and runtime_fakes.py),
not a real Home Assistant install or real Bluetooth hardware.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ha_stubs  # noqa: E402
from runtime_fakes import (  # noqa: E402
    FakeBotConfigurator,
    FakeConfigEntry,
    FakeHass,
    FakeScale,
    make_temp_dir,
)

runtime_module = ha_stubs.import_runtime_module()
BaristaRuntime = runtime_module.BaristaRuntime
ShotPhase = runtime_module.ShotPhase
HomeAssistantError = runtime_module.HomeAssistantError

from custom_components.barista_assist.const import (  # noqa: E402
    CONF_BREW_ENTITY,
    CONF_MACHINE_LIMIT_CONFIRMED,
    CONF_MACHINE_MAX_SHOT_SECONDS,
    CONF_SAFETY_MARGIN_SECONDS,
    CONF_SCALE_ADDRESS,
)
from custom_components.barista_assist.protocol import BookooReading  # noqa: E402

BREW_ENTITY = "switch.brew_bot"


def make_reading(weight_g: float, flow_g_s: float = 1.0) -> BookooReading:
    return BookooReading(
        scale_ms=0,
        weight_g=weight_g,
        flow_g_s=flow_g_s,
        battery_percent=90,
        standby_minutes=0.0,
        buzzer_level=0,
        flow_smoothing=False,
    )


class RuntimeTestCase(unittest.IsolatedAsyncioTestCase):
    """Wires a BaristaRuntime up against fakes: no real HA install, no BLE."""

    async def asyncSetUp(self) -> None:
        runtime_module.BookooUltraClient = FakeScale
        runtime_module.SwitchBotBotConfigurator = FakeBotConfigurator
        runtime_module.resolve_bluetooth_address = (
            lambda hass, entity_id: "AA:BB:CC:DD:EE:FF"
        )
        FakeBotConfigurator.reset()

        self._temp_dir = make_temp_dir()
        self.hass = FakeHass(self._temp_dir)
        self.entry = FakeConfigEntry(
            entry_id="test-entry",
            data={CONF_SCALE_ADDRESS: "11:22:33:44:55:66"},
            options={
                CONF_BREW_ENTITY: BREW_ENTITY,
                CONF_MACHINE_MAX_SHOT_SECONDS: 10.0,
                CONF_SAFETY_MARGIN_SECONDS: 2.0,
                CONF_MACHINE_LIMIT_CONFIRMED: True,
            },
        )
        self.hass.states.set(BREW_ENTITY, {"switch_mode": False})

        self.runtime = BaristaRuntime(self.hass, self.entry)
        await self.runtime.async_initialize()
        self.scale: FakeScale = self.runtime.scale

    async def asyncTearDown(self) -> None:
        for task in self.hass.tasks:
            if not task.done():
                task.cancel()
        await asyncio.sleep(0)
        shutil.rmtree(self._temp_dir, ignore_errors=True)

    async def start_shot(
        self,
        *,
        preinfusion_s: float = 1.0,
        target_yield_g: float = 36.0,
        stop_compensation_g: float = 1.5,
    ) -> str:
        """Create a bag and brew. Real wait of about preinfusion_s + 0.2s."""
        self.runtime.stop_compensation_g = stop_compensation_g
        await self.runtime.async_new_bag(
            {
                "slot": self.runtime.definitions.slots[0],
                "coffee_name": "Test Coffee",
                "preinfusion_s": preinfusion_s,
                "target_yield_g": target_yield_g,
            }
        )
        return await self.runtime.async_brew()

    async def wait_for_extracting(self) -> None:
        for _ in range(200):
            if self.runtime.status == ShotPhase.EXTRACTING.value:
                return
            await asyncio.sleep(0.02)
        self.fail("shot never reached EXTRACTING phase")


class AutoStopTests(RuntimeTestCase):
    async def test_target_weight_triggers_an_actual_press(self):
        """Regression test: stop_scheduled/stop_triggered used to be the same
        flag, so the auto-stop task always no-opped and never pressed."""
        await self.start_shot()
        await self.wait_for_extracting()
        await asyncio.sleep(0.05)

        calls_before = len(self.hass.services.calls)
        self.scale.push_reading(make_reading(weight_g=35.0))  # >= 36 - 1.5
        await self.hass.tasks[-1]  # the async_stop_at_target() task just scheduled

        self.assertGreater(len(self.hass.services.calls), calls_before)
        self.assertEqual(self.runtime.status, ShotPhase.SETTLING.value)
        self.assertTrue(self.runtime.active_shot.stop_triggered)


class StopFailureTests(RuntimeTestCase):
    async def test_abort_press_failure_does_not_finalize_shot(self):
        """Regression test: async_abort used to swallow a press failure and
        finalize the shot anyway, even though the machine was never told to
        stop."""
        await self.start_shot()
        await self.wait_for_extracting()
        self.hass.services.fail_next_call()

        with self.assertRaises(HomeAssistantError):
            await self.runtime.async_abort()

        self.assertEqual(self.runtime.status, ShotPhase.STOP_ERROR.value)
        self.assertIsNotNone(self.runtime.active_shot)

    async def test_target_stop_press_failure_does_not_finalize_shot(self):
        """Same guarantee via the automatic target-weight stop path."""
        await self.start_shot()
        await self.wait_for_extracting()
        await asyncio.sleep(0.05)
        self.hass.services.fail_next_call()

        self.scale.push_reading(make_reading(weight_g=35.0))
        task = self.hass.tasks[-1]
        with self.assertRaises(HomeAssistantError):
            await task

        self.assertEqual(self.runtime.status, ShotPhase.STOP_ERROR.value)
        self.assertIsNotNone(self.runtime.active_shot)


class InstantTapTests(RuntimeTestCase):
    async def test_ensure_quick_stop_press_reprograms_when_not_already_ready(self):
        """Regression test for _async_ensure_quick_stop_press's own contract:
        called directly, decoupled from whether the proactive reprogram in
        _mark_extracting_after_preinfusion happened to run first (it hadn't,
        here - preinfusion is still in progress). Both async_stop_at_target
        and async_abort rely on this call actually reprogramming the Bot to
        an instant tap; without it, the stop/abort press would still hold
        for the configured pre-infusion duration."""
        await self.start_shot(preinfusion_s=1.0)
        shot = self.runtime.active_shot
        self.assertFalse(shot.quick_press_ready)  # proactive reprogram hasn't run yet

        calls_before = list(FakeBotConfigurator.calls)
        await self.runtime._async_ensure_quick_stop_press(shot)

        self.assertTrue(shot.quick_press_ready)
        self.assertEqual(FakeBotConfigurator.calls, calls_before + [0])

    async def test_fallback_cancels_stuck_proactive_reprogram_instead_of_racing(self):
        """Regression test: making the fallback reprogram share a lock with
        the proactive one let the urgent stop wait on non-urgent prep work;
        it must instead cancel the stuck attempt and proceed immediately,
        without two concurrent reprogram attempts racing each other."""
        await self.start_shot(preinfusion_s=1.0)
        # Arm the stuck-call event only now: the initial brew-time reprogram
        # (seconds=1) has already completed above, so this targets the next
        # call instead, which is the proactive reprogram (seconds=0).
        FakeBotConfigurator.delay_once = asyncio.Event()
        await self.wait_for_extracting()
        await asyncio.sleep(0.05)  # proactive reprogram call has started and is stuck

        self.assertFalse(self.runtime.active_shot.quick_press_ready)
        self.assertEqual(FakeBotConfigurator.calls, [1, 0])  # start press, stuck proactive attempt
        phase_task = self.runtime._phase_task
        self.assertFalse(phase_task.done())

        self.scale.push_reading(make_reading(weight_g=35.0))
        await self.hass.tasks[-1]  # async_stop_at_target()

        self.assertTrue(phase_task.done())
        self.assertEqual(self.runtime.status, ShotPhase.SETTLING.value)
        # start press, stuck proactive attempt, fallback reprogram, then the real stop press
        self.assertEqual(FakeBotConfigurator.calls, [1, 0, 0])
        self.assertTrue(self.runtime.active_shot.quick_press_ready)


class DeadlineSafetyTests(RuntimeTestCase):
    async def test_never_presses_after_protected_deadline(self):
        """Safety invariant: once the protected shot deadline has passed,
        the controller must never press the Bot again (it could otherwise
        start a new shot on a machine that already ended this one) and must
        instead enter manual_stop_required."""
        await self.start_shot()
        await self.wait_for_extracting()
        await asyncio.sleep(0.05)

        shot = self.runtime.active_shot
        shot.started_monotonic -= 100.0  # simulate far past safe_shot_deadline_s

        calls_before = len(self.hass.services.calls)
        await self.runtime.async_abort(reason="timeout")

        self.assertEqual(len(self.hass.services.calls), calls_before)  # never pressed
        self.assertEqual(self.runtime.status, ShotPhase.MANUAL_STOP_REQUIRED.value)
        self.assertIsNotNone(self.runtime.active_shot)

        await self.hass.tasks[-1]  # _finalize_after_late_abort, scheduled just above

        self.assertIsNone(self.runtime.active_shot)
        self.assertEqual(self.runtime.status, "timeout")


if __name__ == "__main__":
    unittest.main()
