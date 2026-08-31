"""Regression tests for the shot state machine in runtime.py.

Each test below is a direct regression test for one of the bugs found and
fixed in BaristaRuntime's shot-stop/abort logic:

- Auto-stop must actually press the brew Bot (the stop_scheduled/
  stop_triggered flag conflation previously made this a no-op).
- A failed stop/abort press must not be reported as a successful one.
- The stop/abort press must be reprogrammed to an instant tap rather than
  holding for the configured pre-infusion duration.
- A stop/abort must wait (bounded) for an in-flight proactive reprogram
  attempt for the same Bot instead of cancelling it, since cancelling a
  bleak_retry_connector connection attempt mid-flight can leak the BLE
  connection slot it was opening; it must also never start a second,
  concurrent connection attempt to the same Bot on top of a still-stuck one.
- The controller must never press the Bot once the protected shot deadline
  has passed.

These run against fakes for hass/BLE (see ha_stubs.py and runtime_fakes.py),
not a real Home Assistant install or real Bluetooth hardware.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

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
        ha_stubs.reset_stores()

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

    async def test_abort_can_be_retried_after_a_failed_press(self):
        """Regression test: a failed stop/abort press used to leave
        stop_triggered permanently True (it's set before the press attempt,
        to block a second concurrent caller, but was never reset back on
        failure) - so every later stop/abort attempt on that shot silently
        no-op'd forever, including a manual retry and the protected-deadline
        timeout's own safety-net abort. active_shot would never clear and
        Brew would never re-enable, with no way out short of reloading the
        integration to force the stuck BLE connection to release."""
        await self.start_shot()
        await self.wait_for_extracting()
        self.hass.services.fail_next_call()

        with self.assertRaises(HomeAssistantError):
            await self.runtime.async_abort()
        self.assertEqual(self.runtime.status, ShotPhase.STOP_ERROR.value)
        self.assertFalse(self.runtime.active_shot.stop_triggered)

        await self.runtime.async_abort()  # retry - must actually press, not no-op

        # async_abort presses, settles (3s), and finalizes all within the one
        # call - so by the time it returns the shot is fully wrapped up.
        self.assertEqual(self.runtime.status, ShotPhase.ABORTED.value)
        self.assertIsNone(self.runtime.active_shot)


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

    async def test_fallback_waits_for_stuck_proactive_reprogram_without_racing_it(self):
        """Regression test: the fallback used to cancel a stuck in-flight
        proactive reprogram and immediately start a fresh one. Cancelling
        mid-connection can leak the BLE connection slot it was opening
        (bleak_retry_connector's establish_connection() has no cancellation
        cleanup) - starting a second connection to the same Bot on top of
        that leak is exactly how a single-slot Bluetooth adapter runs out of
        connection slots. It must instead wait (bounded) for the same
        in-flight attempt and never also start a competing one."""
        await self.start_shot(preinfusion_s=1.0)
        # Arm the stuck-call event only now: the initial brew-time reprogram
        # (seconds=1) has already completed above, so this targets the next
        # call instead, which is the proactive reprogram (seconds=0).
        stuck_event = asyncio.Event()
        FakeBotConfigurator.delay_once = stuck_event
        await self.wait_for_extracting()
        await asyncio.sleep(0.05)  # proactive reprogram call has started and is stuck

        self.assertFalse(self.runtime.active_shot.quick_press_ready)
        self.assertEqual(FakeBotConfigurator.calls, [1, 0])  # start press, stuck proactive attempt
        phase_task = self.runtime._phase_task
        self.assertFalse(phase_task.done())

        with (
            mock.patch.object(runtime_module, "_QUICK_STOP_BOT_WAIT_TIMEOUT_S", 0.05),
            mock.patch.object(runtime_module, "_BOT_PRESS_LOCK_TIMEOUT_S", 0.05),
        ):
            self.scale.push_reading(make_reading(weight_g=35.0))
            await self.hass.tasks[-1]  # async_stop_at_target()

        # The stuck proactive attempt is left running (never cancelled, so
        # its connection can't be leaked) rather than raced with a second one.
        self.assertFalse(phase_task.done())
        self.assertEqual(self.runtime.status, ShotPhase.SETTLING.value)
        self.assertEqual(FakeBotConfigurator.calls, [1, 0])  # no third, competing attempt
        self.assertFalse(self.runtime.active_shot.quick_press_ready)

        # Letting the stuck attempt finish afterwards is harmless: it just
        # sets quick_press_ready too late to have helped this particular
        # stop press, which already fell back to holding for the full PI.
        stuck_event.set()
        await phase_task
        self.assertTrue(phase_task.done())


class AdaptPiTests(RuntimeTestCase):
    """Adapt PI *off* (machine-controlled): a single short tap starts the
    Barista Express's own built-in pre-infusion (machine_pi_s) instead of
    Barista Assist holding the button for the bag's configured
    preinfusion_s - so the direct-BLE Bot-configure step
    (SwitchBotBotConfigurator) must never be used at all, only Home
    Assistant's switchbot integration (switch.turn_on).

    Adapt PI *on* (the default - the app adapts/holds for the bag's own
    preinfusion_s) is exercised implicitly by nearly every other test in
    this file, since it's what RuntimeTestCase's default config produces."""

    async def test_start_press_never_reprograms_the_bot(self):
        self.runtime.adapt_pi = False
        self.runtime.machine_pi_s = 0.05
        await self.start_shot(preinfusion_s=1.0)
        self.assertTrue(self.runtime.active_shot.quick_press_ready)
        self.assertEqual(FakeBotConfigurator.calls, [])
        await self.wait_for_extracting()
        self.assertEqual(FakeBotConfigurator.calls, [])

    async def test_uses_the_machine_pi_duration_not_the_bag_value(self):
        """Regression test: a machine-controlled shot must use machine_pi_s
        regardless of the bag's configured preinfusion_s, since the machine
        (not Barista Assist) controls the pre-infusion length in this mode."""
        self.runtime.adapt_pi = False
        self.runtime.machine_pi_s = 0.05
        await self.start_shot(preinfusion_s=1.0)
        await asyncio.sleep(0.15)
        self.assertEqual(self.runtime.status, ShotPhase.EXTRACTING.value)

    async def test_stop_press_also_uses_only_the_switchbot_integration(self):
        self.runtime.adapt_pi = False
        self.runtime.machine_pi_s = 0.05
        await self.start_shot(preinfusion_s=1.0)
        await self.wait_for_extracting()
        # _handle_reading only schedules the target-weight stop once the
        # shot has run for over 1s since press_monotonic (see runtime.py).
        await asyncio.sleep(1.05)
        calls_before = len(self.hass.services.calls)
        self.scale.push_reading(make_reading(weight_g=35.0))
        await self.hass.tasks[-1]

        self.assertGreater(len(self.hass.services.calls), calls_before)
        self.assertEqual(self.runtime.status, ShotPhase.SETTLING.value)
        self.assertEqual(FakeBotConfigurator.calls, [])

    async def test_shot_record_persists_the_adapt_pi_flag(self):
        """Regression test: shots didn't record whether Adapt PI was on,
        making it impossible to tell from an exported shot whether the
        machine or the app actually controlled pre-infusion for that shot."""
        self.runtime.adapt_pi = False
        self.runtime.machine_pi_s = 0.05
        await self.start_shot(preinfusion_s=1.0)
        await self.wait_for_extracting()
        await asyncio.sleep(1.05)
        self.scale.push_reading(make_reading(weight_g=35.0))
        await self.hass.tasks[-1]
        await asyncio.sleep(3.1)  # settle then finalize

        self.assertFalse(bool(self.runtime.last_shot["adapt_pi"]))

    async def test_app_controlled_shot_does_reprogram_the_bot(self):
        """The default (adapt_pi=True): the app holds the button for the
        bag's own preinfusion_s, which does require the direct-BLE
        Bot-configure step - the inverse of every test above."""
        self.assertTrue(self.runtime.adapt_pi)
        await self.start_shot(preinfusion_s=1.0)
        self.assertFalse(self.runtime.active_shot.quick_press_ready)
        self.assertEqual(FakeBotConfigurator.calls, [1])
        await self.wait_for_extracting()
        self.assertEqual(FakeBotConfigurator.calls, [1, 0])


class ButtonAvailabilityTests(RuntimeTestCase):
    """Brew and Abort should each only be usable in the phase they apply to,
    rather than always showing as pressable regardless of shot state."""

    def _button(self, key: str):
        return next(d for d in self.runtime.definitions.platform("button") if d.key == key)

    async def test_brew_is_unavailable_while_a_shot_is_active(self):
        brew = self._button("brew")
        await self.create_bag()
        self.runtime.scale_connected = True
        self.assertTrue(self.runtime.entity_available(brew))

        await self.runtime.async_brew()
        self.assertFalse(self.runtime.entity_available(brew))

    async def test_brew_is_unavailable_without_a_connected_scale(self):
        brew = self._button("brew")
        await self.create_bag()
        self.assertFalse(self.runtime.scale_connected)
        self.assertFalse(self.runtime.entity_available(brew))

        self.runtime.scale_connected = True
        self.assertTrue(self.runtime.entity_available(brew))

    async def test_abort_is_unavailable_with_no_active_shot(self):
        abort = self._button("abort")
        self.assertFalse(self.runtime.entity_available(abort))

        await self.start_shot()
        self.runtime.scale_connected = True
        self.assertTrue(self.runtime.entity_available(abort))

    async def test_tare_is_unavailable_without_a_connected_scale(self):
        tare = self._button("tare")
        self.assertFalse(self.runtime.scale_connected)
        self.assertFalse(self.runtime.entity_available(tare))

        self.runtime.scale_connected = True
        self.assertTrue(self.runtime.entity_available(tare))

    async def test_abort_is_unavailable_without_a_connected_scale(self):
        """Deliberate: Abort stays gated on the scale too, even though that
        means it can gray out mid-shot if the scale drops out - the user
        explicitly chose consistency with Brew over keeping Abort reachable
        during a scale dropout (the physical machine button and the Bot's
        own switch entity remain available regardless)."""
        abort = self._button("abort")
        await self.start_shot()
        self.assertFalse(self.runtime.scale_connected)
        self.assertFalse(self.runtime.entity_available(abort))

        self.runtime.scale_connected = True
        self.assertTrue(self.runtime.entity_available(abort))

        self.runtime.scale_connected = False
        self.assertFalse(self.runtime.entity_available(abort))


class ShotPlotPointsTests(RuntimeTestCase):
    """_shot_plot_points feeds the Live shot dashboard graph's data_generator:
    it must grow live during an active shot, freeze at the last completed
    shot's data afterward, and translate elapsed_ms into real epoch
    timestamps anchored to when the shot actually started."""

    def _status(self):
        return next(d for d in self.runtime.definitions.platform("sensor") if d.key == "status")

    async def test_empty_before_any_shot_has_ever_run(self):
        self.assertEqual(self.runtime._shot_plot_points(), [])
        self.assertEqual(self.runtime.entity_attributes(self._status())["shot_plot"], [])

    async def test_grows_live_during_an_active_shot(self):
        await self.start_shot(preinfusion_s=1.0)
        await self.wait_for_extracting()
        press_wall_ms = int(
            datetime.fromisoformat(self.runtime.active_shot.press_wall_time).timestamp() * 1000
        )
        self.scale.push_reading(make_reading(weight_g=5.0, flow_g_s=1.5))

        points = self.runtime._shot_plot_points()
        self.assertGreater(len(points), 0)
        last = points[-1]
        self.assertEqual(last[1], 5.0)
        self.assertEqual(last[2], 1.5)
        self.assertGreaterEqual(last[0], press_wall_ms)

    async def test_freezes_at_the_last_shot_after_finalizing(self):
        await self.start_shot(preinfusion_s=1.0)
        await self.wait_for_extracting()
        # _handle_reading only schedules the target-weight stop once the
        # shot has run for over 1s since press_monotonic (see runtime.py).
        await asyncio.sleep(1.05)
        calls_before = len(self.hass.services.calls)
        self.scale.push_reading(make_reading(weight_g=36.0, flow_g_s=1.0))
        await self.hass.tasks[-1]
        self.assertGreater(len(self.hass.services.calls), calls_before)
        await asyncio.sleep(3.1)  # settle then finalize

        self.assertIsNone(self.runtime.active_shot)
        frozen = self.runtime._shot_plot_points()
        self.assertGreater(len(frozen), 0)
        # Calling again after the shot is long gone must return the exact
        # same points, not an empty list or a re-derived one.
        self.assertEqual(self.runtime._shot_plot_points(), frozen)


class BotLockSerializationTests(RuntimeTestCase):
    async def test_press_waits_for_an_in_flight_prepare_call(self):
        """Regression test: _async_prepare_brew_bot (used by brew and the
        proactive/fallback reprogram) and _async_press_brew_bot (the actual
        press, used by brew, stop, and abort) used to be reachable
        concurrently from different call paths - brew holds _shot_lock,
        stop/abort hold the separate _actuation_lock - so nothing prevented
        two BLE sessions to the same Bot running at once. Both must now
        serialize through the shared _bot_lock regardless of which
        higher-level path calls them."""
        await self.create_bag()
        stuck_event = asyncio.Event()
        FakeBotConfigurator.delay_once = stuck_event

        prepare_task = asyncio.ensure_future(self.runtime._async_prepare_brew_bot(7))
        await asyncio.sleep(0.05)  # prepare call has started and is stuck, holding _bot_lock
        self.assertEqual(FakeBotConfigurator.calls, [7])
        self.assertFalse(prepare_task.done())

        press_task = asyncio.ensure_future(self.runtime._async_press_brew_bot())
        await asyncio.sleep(0.05)
        self.assertFalse(press_task.done())  # blocked on _bot_lock, not racing ahead
        self.assertEqual(len(self.hass.services.calls), 0)

        stuck_event.set()
        await asyncio.wait_for(prepare_task, timeout=5)
        await asyncio.wait_for(press_task, timeout=5)
        self.assertEqual(len(self.hass.services.calls), 1)


class ScaleDisconnectTests(RuntimeTestCase):
    async def test_active_shot_is_aborted_and_cleared_when_the_scale_disconnects(self):
        """Regression test: without this, a scale dropout mid-shot left
        active_shot set forever - and since Brew/Abort both now require a
        connected scale, reconnecting the scale still couldn't start a new
        shot without restarting Home Assistant."""
        await self.start_shot(preinfusion_s=1.0)
        self.assertIsNotNone(self.runtime.active_shot)

        self.scale.push_connection(False)
        self.assertFalse(self.runtime.scale_connected)
        await asyncio.wait_for(self.hass.tasks[-1], timeout=5)  # the scheduled best-effort abort

        self.assertIsNone(self.runtime.active_shot)
        self.assertEqual(self.runtime.status, ShotPhase.SCALE_DISCONNECTED.value)

    async def test_scale_disconnect_with_no_active_shot_does_not_schedule_anything(self):
        tasks_before = len(self.hass.tasks)
        self.scale.push_connection(False)
        self.assertEqual(len(self.hass.tasks), tasks_before)


class PressRelativeTimingTests(RuntimeTestCase):
    async def test_samples_before_the_press_are_dropped_and_elapsed_is_press_relative(self):
        """Regression test: a live shot showed ~50s of BLE connection delay
        (a slow/retried SwitchBot connect) counted as part of the shot's own
        timeline - samples showed a ~60s flat prefix before any real flow,
        and the shot got cut off by the safety timeout despite the machine
        having only been running a normal amount of time. Readings that
        arrive before the brew Bot is actually pressed aren't part of the
        real shot and must not be recorded, and elapsed_ms/the safety
        deadline must be measured from the actual press (ActiveShot.press_monotonic),
        not from when brewing was requested."""
        await self.create_bag(preinfusion_s=1.0)
        stuck_event = asyncio.Event()
        FakeBotConfigurator.delay_once = stuck_event

        brew_task = asyncio.ensure_future(self.runtime.async_brew())
        await asyncio.sleep(0.3)  # past the tare settle sleep; brew is now stuck connecting to the Bot

        shot = self.runtime.active_shot
        self.assertIsNotNone(shot)
        self.assertIsNone(shot.press_monotonic)
        self.scale.push_reading(make_reading(weight_g=99.0))  # arrives during the "connect delay"
        self.assertEqual(shot.samples, [])

        stuck_event.set()
        await asyncio.wait_for(brew_task, timeout=5)

        self.assertIsNotNone(shot.press_monotonic)
        self.scale.push_reading(make_reading(weight_g=1.0))
        self.assertEqual(len(shot.samples), 1)
        self.assertLess(shot.samples[0].elapsed_ms, 200)  # not inflated by the connect delay

    async def test_safety_deadline_is_measured_from_the_press_not_the_request(self):
        """A slow Bot connection must not eat into the protected safety
        window: the deadline check compares elapsed-since-press, not
        elapsed-since-request, against safe_shot_deadline_s."""
        await self.create_bag(preinfusion_s=1.0)
        stuck_event = asyncio.Event()
        FakeBotConfigurator.delay_once = stuck_event

        brew_task = asyncio.ensure_future(self.runtime.async_brew())
        await asyncio.sleep(0.3)  # past the tare settle sleep; brew is now stuck connecting to the Bot

        shot = self.runtime.active_shot
        # Simulate a connect delay long enough to have blown the deadline if
        # it were (incorrectly) measured from the request.
        shot.started_monotonic -= self.runtime.safe_shot_deadline_s + 100.0

        stuck_event.set()
        await asyncio.wait_for(brew_task, timeout=5)

        calls_before = len(self.hass.services.calls)
        await self.runtime.async_abort()

        # Pressed normally - the request-time-only delay above must not have
        # been treated as "already past the deadline".
        self.assertGreater(len(self.hass.services.calls), calls_before)
        self.assertEqual(self.runtime.status, ShotPhase.ABORTED.value)


class ScaleTareTimingTests(RuntimeTestCase):
    async def test_scale_is_tared_after_the_press_lands_not_before(self):
        """Taring and starting the scale's own timer before the brew Bot is
        actually pressed means the scale's physical timer - and our own
        zero-weight reference - starts counting from whenever Bluetooth
        connection setup happened to finish, not from the real physical
        start of the shot. Both must wait until the press has landed."""
        await self.create_bag(preinfusion_s=1.0)
        stuck_event = asyncio.Event()
        FakeBotConfigurator.delay_once = stuck_event

        brew_task = asyncio.ensure_future(self.runtime.async_brew())
        await asyncio.sleep(0.05)  # brew is stuck connecting to the Bot for the initial press

        self.assertEqual(self.scale.tare_and_start_timer_calls, 0)
        self.assertEqual(self.scale.set_flow_smoothing_calls, [])

        stuck_event.set()
        await asyncio.wait_for(brew_task, timeout=5)

        self.assertEqual(self.scale.tare_and_start_timer_calls, 1)
        self.assertEqual(self.scale.set_flow_smoothing_calls, [False])


class ScaleTimerTests(RuntimeTestCase):
    async def test_finalizing_a_shot_stops_the_scales_own_timer(self):
        """The scale keeps its own onboard timer running (started by
        async_tare_and_start_timer at brew time) until explicitly told to
        stop - without this, the scale's display keeps counting up
        indefinitely after a shot ends instead of freezing at the real
        shot duration."""
        await self.start_shot()
        self.assertEqual(self.scale.stop_timer_calls, 0)

        await self.runtime._async_finalize("complete")

        self.assertEqual(self.scale.stop_timer_calls, 1)


class StatusPropertyTests(RuntimeTestCase):
    async def test_status_prompts_to_connect_the_scale_when_idle_and_disconnected(self):
        """Plain 'idle' reads as 'everything's fine' - but nothing can
        actually happen (brewing requires a connected scale) until it's
        reconnected, so the idle status should say so instead."""
        self.assertEqual(self.runtime.status, ShotPhase.CONNECT_SCALE.value)

        self.runtime.scale_connected = True
        self.assertEqual(self.runtime.status, ShotPhase.IDLE.value)

    async def test_status_reflects_the_real_phase_during_a_shot_even_if_scale_drops(self):
        """The connect_scale override only applies while idle - it must not
        mask what's actually happening mid-shot."""
        await self.start_shot()
        self.runtime.scale_connected = False
        self.assertNotEqual(self.runtime.status, ShotPhase.CONNECT_SCALE.value)


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
        shot.press_monotonic -= 100.0  # simulate far past safe_shot_deadline_s

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
        # default - dashboard-editable runtime attributes, not properties
        # re-read from entry.options.
        self.runtime.machine_max_shot_s = 0.3
        self.runtime.safety_margin_s = 0.1

        await timeout_task

        # asyncio.sleep never returns early, so by the time _shot_timeout's
        # own sleep(safe_shot_deadline_s) wakes it, elapsed_s is always >=
        # that same deadline - a naturally-firing timeout can only take the
        # late/manual_stop_required branch (the on-time-press branch is
        # already covered elsewhere by stops triggered well before it).
        self.assertEqual(self.runtime.status, ShotPhase.MANUAL_STOP_REQUIRED.value)
        self.assertIsNotNone(self.runtime.active_shot)


class SafetyTimeSettingsTests(RuntimeTestCase):
    """machine_max_shot_s/safety_margin_s used to be read-only properties
    sourced live from entry.options (config-flow-only); they're now
    dashboard-editable number entities backed by the runtime's own Store,
    the same pattern as stop_compensation_g."""

    def _number(self, key: str):
        return next(d for d in self.runtime.definitions.platform("number") if d.key == key)

    async def test_seeded_from_entry_options_on_first_load(self):
        """asyncSetUp's FakeConfigEntry seeds machine_max_shot_seconds=10.0/
        safety_margin_seconds=2.0 - since nothing has ever been saved to the
        Store yet, async_initialize (already run in asyncSetUp) must have
        migrated those in as the starting values."""
        self.assertEqual(self.runtime.machine_max_shot_s, 10.0)
        self.assertEqual(self.runtime.safety_margin_s, 2.0)

    async def test_editable_via_the_declarative_entity_interface(self):
        machine_max = self._number("machine_max_shot_seconds")
        margin = self._number("safety_margin_seconds")
        self.assertEqual(self.runtime.entity_value(machine_max), 10.0)
        self.assertEqual(self.runtime.entity_value(margin), 2.0)

        await self.runtime.async_set_entity_value(machine_max, 45.0)
        await self.runtime.async_set_entity_value(margin, 4.0)

        self.assertEqual(self.runtime.machine_max_shot_s, 45.0)
        self.assertEqual(self.runtime.safety_margin_s, 4.0)
        self.assertEqual(self.runtime.entity_value(machine_max), 45.0)
        self.assertEqual(self.runtime.entity_value(margin), 4.0)

    async def test_persisted_value_wins_over_entry_options_on_next_load(self):
        """Once a value has been saved to the Store (e.g. via the dashboard),
        it must take priority over entry.options on a later reload - entry.
        options only seeds the very first migration, it isn't re-consulted
        forever."""
        machine_max = self._number("machine_max_shot_seconds")
        await self.runtime.async_set_entity_value(machine_max, 45.0)

        self.entry.options[CONF_MACHINE_MAX_SHOT_SECONDS] = 999.0  # must be ignored now
        reloaded = BaristaRuntime(self.hass, self.entry)
        await reloaded.async_initialize()
        try:
            self.assertEqual(reloaded.machine_max_shot_s, 45.0)
        finally:
            await reloaded.async_close()


class MachinePiSettingTests(RuntimeTestCase):
    """machine_pi_s (the machine's own built-in pre-infusion duration, used
    when Adapt PI is off) must not be a hardcoded constant - it's a
    dashboard-editable number entity, the same pattern as
    machine_max_shot_s/safety_margin_s/stop_compensation_g, since a real
    Barista Express's own default can differ or be reprogrammed."""

    def _number(self, key: str):
        return next(d for d in self.runtime.definitions.platform("number") if d.key == key)

    async def test_editable_via_the_declarative_entity_interface(self):
        machine_pi = self._number("machine_pi_seconds")
        self.assertEqual(self.runtime.entity_value(machine_pi), self.runtime.machine_pi_s)

        await self.runtime.async_set_entity_value(machine_pi, 12.0)

        self.assertEqual(self.runtime.machine_pi_s, 12.0)
        self.assertEqual(self.runtime.entity_value(machine_pi), 12.0)

    async def test_shot_record_logs_the_actual_effective_preinfusion_used(self):
        """Regression test: shots used to always log the bag's own recipe
        preinfusion_s, regardless of which mode actually controlled the
        shot - making a machine-controlled shot's logged preinfusion_s
        wrong (and, since machine_pi_s used to be a hardcoded constant,
        unrecoverable even by cross-referencing adapt_pi)."""
        self.runtime.machine_pi_s = 6.0
        self.runtime.adapt_pi = False
        await self.start_shot(preinfusion_s=1.0)  # bag's own recipe value - must be ignored
        self.assertEqual(self.runtime.active_shot.preinfusion_s, 6.0)
        await self.runtime.async_abort()
        self.assertEqual(self.runtime.last_shot["preinfusion_s"], 6.0)

        self.runtime.adapt_pi = True
        await self.start_shot(preinfusion_s=4.0)
        self.assertEqual(self.runtime.active_shot.preinfusion_s, 4.0)
        await self.runtime.async_abort()
        self.assertEqual(self.runtime.last_shot["preinfusion_s"], 4.0)


class FinalizeIdempotencyTests(RuntimeTestCase):
    async def test_finalize_is_a_noop_once_the_shot_is_already_cleared(self):
        """Two things racing to end the same shot (e.g. a late timeout firing
        after a settle-task already finalized it) must not double-write to
        the database or crash - the second call is simply a no-op."""
        await self.start_shot()
        self.runtime.scale_connected = True  # isolate from the connect_scale status override

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
