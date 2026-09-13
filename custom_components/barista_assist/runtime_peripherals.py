"""Everything BaristaRuntime does that talks to something outside this
process: the BLE scale/brew-Bot hardware, and Home Assistant's own
notification/event system for taste-feedback push notifications. Mixed into
BaristaRuntime (see runtime.py) - not usable standalone."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_ON
from homeassistant.exceptions import HomeAssistantError

from .const import CONF_NOTIFY_SERVICE
from .protocol import BookooReading
from .runtime_shared import ActiveShot, ShotPhase, _FLAVOR_AXES
from .storage import ShotSample
from .switchbot import SwitchBotBotConfigurator, resolve_bluetooth_address

_LOGGER = logging.getLogger(__name__)
# How long the time-critical stop/abort path waits for an in-flight proactive
# Bot reprogram before giving up on it (see _async_ensure_quick_stop_press).
_QUICK_STOP_BOT_WAIT_TIMEOUT_S = 3.0
# How long the actual brew Bot press waits for _bot_lock before proceeding
# without it anyway (see _async_press_brew_bot) - it must never be blocked
# indefinitely behind a slow/stuck prepare call.
_BOT_PRESS_LOCK_TIMEOUT_S = 2.0
# Prefix + ":"-delimited fields ("barista_flavor:{shot_id}:{axis}:{tag}") for
# the actionable-notification action ids the flavor-feedback notifications
# use (see _async_send_flavor_feedback_notifications/
# _handle_flavor_notification_action) - namespaced so the bus listener never
# reacts to an unrelated integration's own mobile_app_notification_action.
_FLAVOR_ACTION_PREFIX = "barista_flavor"


def _flavor_tag_actions(shot_id: str, axis: str, *, prefix: str) -> list[dict[str, str]]:
    """The 2-tags-plus-Balanced actionable-notification button list for one
    axis, namespaced under `prefix` (always _FLAVOR_ACTION_PREFIX - kept as
    a parameter rather than hardcoded since the id-parsing convention it
    matches, "{prefix}:{shot_id}:{axis}:{tag}", is shared with
    _handle_flavor_notification_action)."""
    actions = [
        {"action": f"{prefix}:{shot_id}:{axis}:{tag}", "title": title}
        for tag, title in _FLAVOR_AXES[axis]
    ]
    actions.append({"action": f"{prefix}:{shot_id}:{axis}:balanced", "title": "Balanced"})
    return actions


class RuntimePeripheralsMixin:
    """Mixed into BaristaRuntime - see that class for the shared __init__/state."""

    # -- scale callbacks --
    def _handle_scale_connection(self, connected: bool) -> None:
        _LOGGER.debug("Scale %s", "connected" if connected else "disconnected")
        self.scale_connected = connected
        if not connected and self.active_shot is not None:
            _LOGGER.warning(
                "Scale disconnected mid-shot; issuing a best-effort auto-abort"
            )
            # Without the scale there's no way to track the pour or trigger
            # the target-weight stop, and Brew/Abort now both require a
            # connected scale - so a dropped shot would otherwise be stuck
            # forever with no way to start a new one, even after the scale
            # reconnects. Best-effort abort (like a manual one, it still
            # correctly refuses to clear the shot if the stop press itself
            # also fails) so reconnecting the scale leaves a clean slate.
            self.hass.async_create_task(
                self.async_abort(reason=ShotPhase.SCALE_DISCONNECTED.value),
                "barista_assist_scale_dropped_abort",
            )
        self._notify(force=True)

    # -- read scale and decide if to stop shot --
    def _handle_reading(self, reading: BookooReading) -> None:
        shot = self.active_shot
        if shot is not None and shot.press_monotonic is not None:
            # Samples only start once the machine is actually engaged, not
            # from when brewing was requested (see ActiveShot.press_monotonic) -
            # readings that arrive during Bot connection setup aren't part of
            # the shot's real timeline and would otherwise pad every
            # exported/analyzed shot with a flat prefix of BLE-connect delay.
            elapsed_ms = int((time.monotonic() - shot.press_monotonic) * 1000)
            shot.samples.append(
                ShotSample(
                    seq=len(shot.samples),
                    elapsed_ms=elapsed_ms,
                    scale_ms=reading.scale_ms,
                    weight_g=reading.weight_g,
                    flow_g_s=reading.flow_g_s,
                    battery_percent=reading.battery_percent,
                )
            )
            margin_g = self._effective_stop_margin_g(shot)
            threshold = shot.target_yield_g - margin_g
            if (
                not shot.stop_scheduled
                and elapsed_ms > 1000
                and reading.weight_g >= threshold
            ):
                shot.stop_scheduled = True
                shot.effective_stop_margin_g = margin_g
                _LOGGER.info(
                    "Stop scheduled at %.2fs: weight=%.2fg margin=%.2fg "
                    "(early_stop_margin_min_g=%.2fg) threshold=%.2fg",
                    elapsed_ms / 1000.0,
                    reading.weight_g,
                    margin_g,
                    shot.early_stop_margin_min_g,
                    threshold,
                )
                self.hass.async_create_task(
                    self.async_stop_at_target(shot.id), "barista_assist_target_stop"
                )
        self._notify()

    # -- brew Bot actuation (SwitchBot) --
    async def _async_prepare_brew_bot(self, hold_seconds: int) -> None:
        """Program the Bot's stored press-hold duration (0 = an instant tap).

        Guarded by _bot_lock: brew, the proactive mid-shot reprogram, and the
        stop/abort fallback reprogram can all reach this from different,
        independently-locked call paths, and racing two BLE sessions to the
        same Bot is exactly how a shot can end up starving/failing itself.
        """
        if self._bot_lock.locked():
            _LOGGER.debug("Waiting for _bot_lock before programming Bot to %ss", hold_seconds)
        async with self._bot_lock:
            address = resolve_bluetooth_address(self.hass, self.brew_entity)
            if address is None:
                raise HomeAssistantError(
                    "Could not resolve the Bluetooth address of the selected brew SwitchBot"
                )
            _LOGGER.debug("Programming brew Bot long-press duration to %ss", hold_seconds)
            await SwitchBotBotConfigurator(
                self.hass, address
            ).async_set_long_press_duration(hold_seconds)
            _LOGGER.debug("Brew Bot programmed to %ss", hold_seconds)

    async def _async_press_brew_bot(self) -> None:
        """The actual button press - used by brew, stop, and abort alike, so
        unlike _async_prepare_brew_bot this is always time-critical (it's
        what physically stops a pour). It still prefers to wait for
        _bot_lock (see _async_prepare_brew_bot) to avoid racing a concurrent
        BLE session, but only up to _BOT_PRESS_LOCK_TIMEOUT_S - it must never
        be blocked indefinitely behind a slow/stuck prepare call.
        """
        try:
            await asyncio.wait_for(
                self._bot_lock.acquire(), timeout=_BOT_PRESS_LOCK_TIMEOUT_S
            )
            held_lock = True
        except asyncio.TimeoutError:
            held_lock = False
            _LOGGER.warning(
                "Pressing the brew Bot without waiting further for an "
                "in-flight Bot operation to finish - this press can't wait"
            )
        try:
            entity_id = self.brew_entity
            state = self.hass.states.get(entity_id)
            if state is None:
                raise HomeAssistantError(f"Brew SwitchBot entity {entity_id!r} is unavailable")
            if state.attributes.get("switch_mode") is True:
                raise HomeAssistantError(
                    "The brew SwitchBot is in switch mode. Configure it as a press/long-press Bot."
                )
            await self.hass.services.async_call(
                "switch", SERVICE_TURN_ON, {ATTR_ENTITY_ID: entity_id}, blocking=True
            )
            _LOGGER.debug("Pressed brew Bot %s", entity_id)
        finally:
            if held_lock:
                self._bot_lock.release()

    async def _async_ensure_quick_stop_press(self, shot: ActiveShot) -> None:
        """Fall back to reprogramming here if the proactive attempt hasn't landed yet.

        Waits for (rather than cancels) any in-flight proactive attempt: both
        want the exact same outcome, and cancelling a task mid-connection can
        leak the BLE connection it was opening - bleak_retry_connector's
        establish_connection() has no cancellation cleanup, so a client that
        was mid-connect when cancelled is never disconnected. Starting a
        second, fresh connection to the same Bot on top of that leaked one is
        exactly how a single-slot/limited Bluetooth adapter runs out of
        connection slots. The wait is bounded so a truly stuck attempt still
        can't hang the time-critical stop/abort path forever.
        """
        if shot.quick_press_ready:
            _LOGGER.debug("Quick stop press already programmed ahead of time")
            return
        phase_task = self._phase_task
        if phase_task is not None and not phase_task.done():
            _LOGGER.debug(
                "Waiting up to %ss for the in-flight proactive Bot reprogram",
                _QUICK_STOP_BOT_WAIT_TIMEOUT_S,
            )
            with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
                await asyncio.wait_for(
                    asyncio.shield(phase_task), timeout=_QUICK_STOP_BOT_WAIT_TIMEOUT_S
                )
            if shot.quick_press_ready:
                _LOGGER.debug("Proactive Bot reprogram landed while waiting")
                return
            if not phase_task.done():
                # Still in flight after the bounded wait: don't also open a
                # second, concurrent connection to the same Bot on top of it
                # (the very race this method exists to avoid) - accept the
                # slower fallback press instead and let the proactive attempt
                # finish (or fail) in the background on its own.
                _LOGGER.warning(
                    "The proactive brew Bot reprogram is still in flight; "
                    "this press may hold for the configured pre-infusion duration"
                )
                return
        try:
            await self._async_prepare_brew_bot(0)
            shot.quick_press_ready = True
        except Exception as err:
            _LOGGER.warning(
                "Could not reprogram brew Bot for an instant stop press; "
                "the press may hold for the configured pre-infusion duration: %s",
                err,
            )

    async def async_tare(self) -> None:
        _LOGGER.debug("Tare requested")
        await self.scale.async_ensure_connected()
        await self.scale.async_tare()

    # -- taste-feedback push notifications --
    def _schedule_flavor_feedback_notifications(self, shot_id: str, bag_id: str) -> None:
        """Schedule the taste-feedback push notifications for a just-
        completed shot, flavor_feedback_delay_s from now (definitions.yaml's
        defaults.controller) - skipped entirely if no notify target is
        configured. A no-op background task either way is fine to fire off
        and forget; _async_send_flavor_feedback_notifications is kept
        separately callable so tests can invoke it directly instead of
        waiting out a real multi-minute sleep."""
        if not self.entry.options.get(CONF_NOTIFY_SERVICE):
            return

        async def _after_delay() -> None:
            try:
                delay_s = self.definitions.defaults["controller"]["flavor_feedback_delay_s"]
                await asyncio.sleep(delay_s)
                await self._async_send_flavor_feedback_notifications(shot_id, bag_id)
            except asyncio.CancelledError:
                return
            except Exception:
                # A background task's own exception isn't guaranteed to
                # surface anywhere a user would see it at release time - log
                # it here, with the one piece of context (which shot) that
                # matters for diagnosing it, rather than relying on asyncio's
                # generic "exception was never retrieved" logging.
                _LOGGER.exception(
                    "Failed to send flavor-feedback notifications for shot %s", shot_id
                )
            finally:
                self._flavor_notification_tasks.pop(shot_id, None)

        self._flavor_notification_tasks[shot_id] = self.hass.async_create_background_task(
            _after_delay(), f"barista_assist_flavor_feedback_{shot_id}"
        )

    async def _async_send_flavor_feedback_notifications(self, shot_id: str, bag_id: str) -> None:
        """Send the two independent per-axis taste-feedback notifications
        (see _FLAVOR_AXES), each the same plain 3-button actionable
        notification (the axis's own two tags, plus Balanced) answerable
        with a single tap - no app-opening required, and no other question
        type exists to send instead (docs/DESIGN.md's Phase 5). Each
        button's action id ("barista_flavor:{shot_id}:{axis}:{tag}") carries
        everything _handle_flavor_notification_action needs to record the
        answer."""
        service = self.entry.options.get(CONF_NOTIFY_SERVICE)
        if not service:
            return
        _LOGGER.debug(
            "Sending flavor-feedback notifications for shot %s via notify.%s", shot_id, service
        )
        for axis in _FLAVOR_AXES:
            actions = _flavor_tag_actions(shot_id, axis, prefix=_FLAVOR_ACTION_PREFIX)
            try:
                await self.hass.services.async_call(
                    "notify",
                    service,
                    {
                        "message": f"How was the {axis} on that last shot?",
                        "data": {"actions": actions},
                    },
                )
            except Exception:
                # The two axes are independent notifications - one axis's
                # notify call failing (e.g. the device is offline) shouldn't
                # also silently swallow the other axis's, so each is caught
                # and logged separately rather than letting the first
                # failure abort the whole loop.
                _LOGGER.warning(
                    "Failed to send the %s flavor-feedback notification for shot %s via "
                    "notify.%s",
                    axis,
                    shot_id,
                    service,
                    exc_info=True,
                )

    async def _handle_flavor_notification_action(self, event: Any) -> None:
        """Fires once per tapped button, for exactly one axis of one shot -
        the extraction and mouthfeel notifications are answered (or not)
        completely independently, so this only ever records the one axis
        the event is actually about. Tapping neither notification for a
        shot means no event ever fires and no tag is ever recorded for it
        on either axis; tapping only one means only that axis's column gets
        written for this shot, and the other stays unanswered - see
        storage.record_flavor_tag/recent_flavor_tags for how an unanswered
        axis is then treated (skipped, not defaulted to "balanced" or
        anything else) when a future recommendation is computed."""
        action = str(event.data.get("action", ""))
        prefix = f"{_FLAVOR_ACTION_PREFIX}:"
        if not action.startswith(prefix):
            return  # not one of ours - some other integration's notification
        try:
            shot_id, axis, tag = action[len(prefix):].split(":")
        except ValueError:
            _LOGGER.warning("Malformed flavor-feedback action id: %s", action)
            return
        try:
            recorded = await self.hass.async_add_executor_job(
                self.db.record_flavor_tag, shot_id, axis, tag
            )
        except Exception:
            _LOGGER.exception("Failed to record flavor tag %s (axis=%s) for shot %s", tag, axis, shot_id)
            return
        if not recorded:
            # The shot this notification was about no longer exists (e.g.
            # deleted from shot history before the notification was
            # answered) - the UPDATE silently affected zero rows rather than
            # raising, so this is the only place that would ever surface it.
            _LOGGER.warning(
                "Flavor-feedback action for unknown shot %s (axis=%s, tag=%s) - shot may "
                "have been deleted",
                shot_id,
                axis,
                tag,
            )
            return
        _LOGGER.debug("Recorded flavor tag %s (axis=%s) for shot %s", tag, axis, shot_id)
        await self.async_refresh_cache()
