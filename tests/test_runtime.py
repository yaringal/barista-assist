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
import json
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

    async def create_bag(
        self,
        *,
        preinfusion_s: float = 1.0,
        target_yield_g: float = 36.0,
        stop_compensation_g: float = 1.5,
    ) -> None:
        """Create and select a bag, without brewing."""
        self.runtime.stop_compensation_g = stop_compensation_g
        await self.runtime.async_new_bag(
            {
                "slot": self.runtime.definitions.slots[0],
                "coffee_name": "Test Coffee",
                "preinfusion_s": preinfusion_s,
                "target_yield_g": target_yield_g,
            }
        )

    async def start_shot(
        self,
        *,
        preinfusion_s: float = 1.0,
        target_yield_g: float = 36.0,
        stop_compensation_g: float = 1.5,
    ) -> str:
        """Create a bag and brew. Real wait of about preinfusion_s + 0.2s."""
        await self.create_bag(
            preinfusion_s=preinfusion_s,
            target_yield_g=target_yield_g,
            stop_compensation_g=stop_compensation_g,
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


class BrewValidationTests(RuntimeTestCase):
    """async_brew's pre-flight checks - each should raise before doing any
    real work (no scale connect, no DB shot row, no press)."""

    async def test_rejects_brew_when_machine_limit_not_confirmed(self):
        await self.create_bag()
        self.entry.options[CONF_MACHINE_LIMIT_CONFIRMED] = False

        with self.assertRaises(HomeAssistantError):
            await self.runtime.async_brew()
        self.assertIsNone(self.runtime.active_shot)

    async def test_rejects_brew_when_preinfusion_exceeds_safe_deadline(self):
        # Default options give safe_shot_deadline_s = 10 - 2 = 8.
        await self.create_bag(preinfusion_s=9.0)

        with self.assertRaises(HomeAssistantError):
            await self.runtime.async_brew()
        self.assertIsNone(self.runtime.active_shot)

    async def test_rejects_brew_when_stop_compensation_too_large_for_yield(self):
        # async_brew requires target_yield_g > stop_compensation_g + 1.0.
        # target_yield_g must still be within its own valid range (15-80) so
        # this exercises that check specifically, not bag-creation validation.
        await self.create_bag(target_yield_g=15.0, stop_compensation_g=14.0)

        with self.assertRaises(HomeAssistantError):
            await self.runtime.async_brew()
        self.assertIsNone(self.runtime.active_shot)

    async def test_rejects_second_brew_while_one_is_active(self):
        await self.start_shot()

        with self.assertRaises(HomeAssistantError):
            await self.runtime.async_brew()


class NewBagValidationTests(RuntimeTestCase):
    async def test_new_bag_validates_every_recipe_field_not_just_grind(self):
        """Regression test: async_new_bag used to validate only `grind`, so
        an out-of-range value for any other recipe field (e.g. an invalid
        temperature offset) would be stored unvalidated - and could later
        crash the temperature_offset select entity's current_option."""
        with self.assertRaises(HomeAssistantError):
            await self.runtime.async_new_bag(
                {
                    "slot": self.runtime.definitions.slots[0],
                    "coffee_name": "Test Coffee",
                    "temperature_offset_c": 99,  # not one of {-2,-1,0,1,2}
                }
            )


class SwitchModeTests(RuntimeTestCase):
    async def test_brew_refuses_when_switchbot_reports_switch_mode(self):
        """A Bot in switch (toggle) mode rather than press mode must never
        be brewed against - it would not behave like a momentary button."""
        self.hass.states.set(BREW_ENTITY, {"switch_mode": True})
        await self.create_bag()

        with self.assertRaises(HomeAssistantError):
            await self.runtime.async_brew()

        self.assertIsNone(self.runtime.active_shot)
        self.assertEqual(self.runtime.status, ShotPhase.ERROR.value)


class EarlyAbortTests(RuntimeTestCase):
    async def test_abort_during_preinfusion_succeeds(self):
        """Aborting before the proactive instant-tap reprogram has even run
        (still mid pre-infusion) must still fall back to reprogramming and
        pressing, and finalize cleanly as aborted."""
        await self.start_shot(preinfusion_s=2.0)
        self.assertEqual(self.runtime.status, ShotPhase.PREINFUSION.value)
        shot = self.runtime.active_shot
        self.assertFalse(shot.quick_press_ready)

        calls_before = len(self.hass.services.calls)
        await self.runtime.async_abort()

        self.assertGreater(len(self.hass.services.calls), calls_before)
        self.assertTrue(shot.quick_press_ready)
        self.assertIsNone(self.runtime.active_shot)
        self.assertEqual(self.runtime.status, ShotPhase.ABORTED.value)


class ShotTimeoutTests(RuntimeTestCase):
    async def test_shot_timeout_task_triggers_abort_after_deadline(self):
        """The background _shot_timeout task (not just async_abort called
        directly, as the other tests do) must itself invoke the abort path
        once the protected deadline passes."""
        await self.start_shot()
        timeout_task = self.hass.tasks[-1]  # _shot_timeout(), scheduled by async_brew()

        # Shrink the deadline live instead of waiting out the real ~8s
        # default - properties re-read entry.options on every access.
        self.entry.options[CONF_MACHINE_MAX_SHOT_SECONDS] = 0.3
        self.entry.options[CONF_SAFETY_MARGIN_SECONDS] = 0.1

        await timeout_task

        # asyncio.sleep never returns early, so by the time _shot_timeout's
        # own sleep(safe_shot_deadline_s) wakes it, elapsed_s is always >=
        # that same deadline - a naturally-firing timeout can only take the
        # late/manual_stop_required branch (the on-time-press branch is
        # already covered elsewhere by stops triggered well before it).
        self.assertEqual(self.runtime.status, ShotPhase.MANUAL_STOP_REQUIRED.value)
        self.assertIsNotNone(self.runtime.active_shot)


class FinalizeIdempotencyTests(RuntimeTestCase):
    async def test_finalize_is_a_noop_once_the_shot_is_already_cleared(self):
        """Two things racing to end the same shot (e.g. a late timeout firing
        after a settle-task already finalized it) must not double-write to
        the database or crash - the second call is simply a no-op."""
        await self.start_shot()

        await self.runtime._async_finalize("complete")
        self.assertIsNone(self.runtime.active_shot)
        self.assertEqual(self.runtime.status, ShotPhase.IDLE.value)

        await self.runtime._async_finalize("complete")  # must not raise
        self.assertIsNone(self.runtime.active_shot)


class FlowAnalysisWiringTests(RuntimeTestCase):
    """_async_finalize must actually run flow_analysis.analyze_shot on the
    real captured samples and persist a coherent result - not silently skip
    it, and not just write a placeholder string."""

    async def test_finalized_shot_persists_flow_analysis(self):
        await self.start_shot()
        await self.wait_for_extracting()

        # Spread over more than flow_analysis's 500ms smoothing window - too
        # tight a span makes every sample's window cover the whole sequence,
        # flattening the signal to a constant and looking like no flow at all.
        for weight in (4.0, 9.0, 14.0, 19.0, 24.0, 30.0, 36.0):
            self.scale.push_reading(make_reading(weight_g=weight))
            await asyncio.sleep(0.09)

        await self.runtime._async_finalize("complete")

        last_shot = self.runtime.last_shot
        self.assertIsNotNone(last_shot)
        self.assertIn(
            last_shot["classification"],
            {"healthy", "too_fast", "too_restrictive", "puck_prep_issue", "invalid_measurement"},
        )
        self.assertIsInstance(last_shot["channeling_suspicion"], float)
        analysis = json.loads(last_shot["analysis_json"])
        self.assertIn("late_accel", analysis)

    async def test_non_healthy_shot_is_excluded_from_future_baseline(self):
        """A shot too sparse to classify must not feed the next shot's
        baseline query - proves the real classification round-trips through
        storage correctly, not just that some string got written."""
        await self.start_shot()
        await self.wait_for_extracting()
        bag_id = self.runtime.active_shot.bag.id

        self.scale.push_reading(make_reading(weight_g=36.0))  # one sample: too few to classify
        await self.runtime._async_finalize("complete")

        self.assertEqual(self.runtime.last_shot["classification"], "invalid_measurement")
        self.assertIsNone(self.runtime.db.recent_healthy_features(bag_id))

    async def test_invalid_shot_logs_its_specific_reason(self):
        """An invalid shot must be diagnosable from the logs alone - e.g. a
        BLE dropout (too_few_samples/near_zero_final_weight) vs. something
        else - rather than showing up as an unexplained invalid_measurement."""
        await self.start_shot()
        await self.wait_for_extracting()
        self.scale.push_reading(make_reading(weight_g=36.0))  # one sample: too few to classify

        with self.assertLogs("custom_components.barista_assist.runtime", level="WARNING") as log:
            await self.runtime._async_finalize("complete")

        self.assertTrue(
            any("too_few_samples" in message for message in log.output),
            log.output,
        )


if __name__ == "__main__":
    unittest.main()
