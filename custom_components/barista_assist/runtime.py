"""Runtime controller: shot state machine plus bag/application state."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from enum import Enum
import json
import logging
import math
from pathlib import Path
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store

from .bookoo import BookooUltraClient
from .const import (
    CONF_BREW_ENTITY,
    CONF_MACHINE_LIMIT_CONFIRMED,
    CONF_MACHINE_MAX_SHOT_SECONDS,
    CONF_SAFETY_MARGIN_SECONDS,
    CONF_SCALE_ADDRESS,
    DOMAIN,
    SIGNAL_UPDATE,
)
from .definitions import EntityDefinition, load_definitions
from .flow_analysis import BaselineFeatures, analyze_shot
from .protocol import BookooReading
from .storage import Bag, BaristaDatabase, ShotSample
from .switchbot import SwitchBotBotConfigurator, resolve_bluetooth_address

_LOGGER = logging.getLogger(__name__)
_STORE_VERSION = 1
# How long the time-critical stop/abort path waits for an in-flight proactive
# Bot reprogram before giving up on it (see _async_ensure_quick_stop_press).
_QUICK_STOP_BOT_WAIT_TIMEOUT_S = 3.0
# How long the actual brew Bot press waits for _bot_lock before proceeding
# without it anyway (see _async_press_brew_bot) - it must never be blocked
# indefinitely behind a slow/stuck prepare call.
_BOT_PRESS_LOCK_TIMEOUT_S = 2.0
# The Barista Express's own built-in pre-infusion duration when the brew
# button is single-tapped rather than held (see BaristaRuntime.auto_pi) -
# fixed by the machine itself, not something Barista Assist can configure.
AUTO_PI_DURATION_S = 8.0
# Cap on points returned by _shot_plot_points, regardless of how many raw
# samples a shot has - keeps the `shot_plot` attribute payload bounded for an
# unusually long shot instead of growing without limit.
_SHOT_PLOT_MAX_POINTS = 300


class ShotPhase(str, Enum):
    """Visible controller states.

    Plain (str, Enum) rather than enum.StrEnum for Python 3.10 compatibility
    (StrEnum requires 3.11+); __str__ keeps str(member) == member.value like
    StrEnum, matching the behavior the rest of this module relies on.
    """

    IDLE = "idle"
    CONNECTING_SCALE = "connecting_scale"
    PREINFUSION = "preinfusion"
    EXTRACTING = "extracting"
    STOPPING = "stopping"
    SETTLING = "settling"
    STOP_ERROR = "stop_error"
    ABORTED = "aborted"
    TIMEOUT = "timeout"
    ERROR = "error"
    MANUAL_STOP_REQUIRED = "manual_stop_required"
    SCALE_DISCONNECTED = "scale_disconnected"
    # Display-only: never set via _set_phase. The `status` property reports
    # this instead of IDLE whenever there's no active shot and the scale
    # isn't connected, since "idle" reads as "everything's fine" when really
    # nothing can happen (brewing requires a connected scale) until it's
    # reconnected.
    CONNECT_SCALE = "connect_scale"

    def __str__(self) -> str:
        return self.value


@dataclass(slots=True)
class BagDraft:
    """Ephemeral new-bag form state; intentionally not part of coffee history."""

    coffee: str = ""
    roaster: str = ""
    roast_date: date | None = None
    starting_mass_g: float = 250.0


@dataclass(slots=True)
class ActiveShot:
    """In-memory state for the current shot."""

    id: str
    bag: Bag
    started_at: str
    started_monotonic: float
    target_yield_g: float
    stop_compensation_g: float
    preinfusion_s: float
    samples: list[ShotSample]
    # Set once the initial brew Bot press actually lands (see async_brew).
    # started_monotonic marks when brewing was *requested*, which can be
    # anywhere from milliseconds to (on constrained Bluetooth hardware)
    # nearly a minute before the machine is physically engaged - using it as
    # the reference point for the safety deadline or for sample timing means
    # BLE connection delay silently eats into both. press_monotonic is what
    # the physical machine's own timing - and therefore ours - should
    # actually be measured against.
    press_monotonic: float | None = None
    # Wall-clock counterpart to press_monotonic, set at the same instant -
    # samples are timestamped in elapsed_ms (monotonic, not wall-clock), so
    # this is what lets the live-shot graph convert them back to real
    # absolute timestamps for charting (see BaristaRuntime._shot_plot_points).
    press_wall_time: str | None = None
    stop_command_elapsed_ms: int | None = None
    stop_scheduled: bool = False
    stop_triggered: bool = False
    quick_press_ready: bool = False


class BaristaRuntime:
    """Single Barista Assist installation."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.definitions = load_definitions()
        self.db = BaristaDatabase(
            Path(hass.config.path(".storage", f"barista_assist_{entry.entry_id}.sqlite3"))
        )
        self.store = Store[dict[str, Any]](
            hass, _STORE_VERSION, f"{DOMAIN}.{entry.entry_id}.state"
        )
        self.scale = BookooUltraClient(
            hass,
            str(entry.data[CONF_SCALE_ADDRESS]),
            self._handle_reading,
            self._handle_scale_connection,
        )

        defaults = self.definitions.defaults
        self.selected_slot = self.definitions.slots[0]
        self.stop_compensation_g = float(
            defaults["controller"]["stop_compensation_g"]
        )
        self.auto_pi = bool(defaults["controller"].get("auto_pi", False))
        self.draft = BagDraft(
            roast_date=date.today(),
            starting_mass_g=float(defaults["new_bag"]["starting_mass_g"]),
        )
        self._phase = ShotPhase.IDLE
        self.scale_connected = False
        self.active_shot: ActiveShot | None = None
        self.last_shot: dict[str, Any] | None = None
        # Kept around after a shot finalizes purely so the live-shot dashboard
        # graph can keep showing it (frozen) instead of going blank the
        # instant the shot ends - see _shot_plot_points.
        self._last_shot_samples: list[ShotSample] = []
        self._last_shot_press_wall_time: str | None = None
        self._bags: dict[str, Bag] = {}
        self._bag_remaining: dict[str, float | None] = {}
        self._last_dispatch = 0.0
        self._timeout_task: asyncio.Task[None] | None = None
        self._settle_task: asyncio.Task[None] | None = None
        self._phase_task: asyncio.Task[None] | None = None
        self._manual_finalize_task: asyncio.Task[None] | None = None
        self._shot_lock = asyncio.Lock()
        self._actuation_lock = asyncio.Lock()
        # _shot_lock (brew) and _actuation_lock (stop/abort) are intentionally
        # separate so a fast abort never has to wait behind brew's own slow
        # scale-connect/tare preamble - but both paths eventually talk BLE to
        # the exact same brew Bot, and running two of those sessions
        # concurrently is exactly how a shot ends up racing itself (see
        # _async_prepare_brew_bot/_async_press_brew_bot). This lock is the
        # actual point of mutual exclusion between them.
        self._bot_lock = asyncio.Lock()

    # ---------------------------------------------------------------------
    # Config & derived properties
    # ---------------------------------------------------------------------
    @property
    def status(self) -> str:
        if self._phase == ShotPhase.IDLE and not self.scale_connected:
            return ShotPhase.CONNECT_SCALE.value
        return self._phase.value

    @property
    def brew_entity(self) -> str:
        return str(self.entry.options.get(CONF_BREW_ENTITY, self.entry.data.get(CONF_BREW_ENTITY, "")))

    @property
    def machine_max_shot_s(self) -> float:
        return float(self.entry.options[CONF_MACHINE_MAX_SHOT_SECONDS])

    @property
    def safety_margin_s(self) -> float:
        return float(
            self.entry.options.get(
                CONF_SAFETY_MARGIN_SECONDS,
                self.definitions.defaults["controller"]["safety_margin_s"],
            )
        )

    @property
    def safe_shot_deadline_s(self) -> float:
        return self.machine_max_shot_s - self.safety_margin_s

    @property
    def machine_limit_confirmed(self) -> bool:
        return bool(self.entry.options.get(CONF_MACHINE_LIMIT_CONFIRMED, False))

    @property
    def selected_bag(self) -> Bag | None:
        return self._bags.get(self.selected_slot)

    # ---------------------------------------------------------------------
    # Lifecycle
    # ---------------------------------------------------------------------
    async def async_initialize(self) -> None:
        """Load durable state, migrate the database, and start BLE."""
        legacy_pi = float(
            self.entry.options.get(
                "preinfusion_seconds",
                self.entry.data.get(
                    "preinfusion_seconds",
                    self.definitions.defaults["recipe"]["preinfusion_s"],
                ),
            )
        )
        await self.hass.async_add_executor_job(
            lambda: self.db.initialize(legacy_preinfusion_s=legacy_pi)
        )
        state = await self.store.async_load() or {}
        selected = state.get("selected_slot")
        if selected is None:
            selected = await self.hass.async_add_executor_job(self.db.legacy_selected_slot)
        if selected in self.definitions.slots:
            self.selected_slot = selected

        legacy_stop = self.entry.options.get(
            "stop_compensation_grams",
            self.entry.data.get(
                "stop_compensation_grams",
                self.definitions.defaults["controller"]["stop_compensation_g"],
            ),
        )
        self.stop_compensation_g = float(
            state.get("stop_compensation_g", legacy_stop)
        )
        self.auto_pi = bool(
            state.get("auto_pi", self.definitions.defaults["controller"].get("auto_pi", False))
        )
        await self._async_save_state()
        await self.async_refresh_cache()
        await self.scale.async_start()

    async def async_close(self) -> None:
        for task in self._background_tasks():
            if task:
                task.cancel()
        await self.scale.async_stop()

    def _background_tasks(self) -> tuple[asyncio.Task[None] | None, ...]:
        return (self._timeout_task, self._settle_task, self._phase_task, self._manual_finalize_task)

    async def _async_save_state(self) -> None:
        await self.store.async_save(
            {
                "selected_slot": self.selected_slot,
                "stop_compensation_g": self.stop_compensation_g,
                "auto_pi": self.auto_pi,
                "machine_max_shot_s": self.machine_max_shot_s,
                "safety_margin_s": self.safety_margin_s,
                "safe_shot_deadline_s": self.safe_shot_deadline_s,
            }
        )

    def _set_phase(self, phase: ShotPhase) -> None:
        if phase != self._phase:
            _LOGGER.debug("Shot phase: %s -> %s", self._phase.value, phase.value)
        self._phase = phase
        self._notify(force=True)

    async def async_refresh_cache(self) -> None:
        self._bags = await self.hass.async_add_executor_job(self.db.active_bags)
        self.last_shot = await self.hass.async_add_executor_job(self.db.last_shot)
        self._bag_remaining = {
            slot: await self.hass.async_add_executor_job(self.db.bag_remaining_g, bag.id)
            for slot, bag in self._bags.items()
        }
        self._notify(force=True)

    def _notify(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_dispatch < 0.5:
            return
        self._last_dispatch = now
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)

    # ---------------------------------------------------------------------
    # Declarative entity interface
    # ---------------------------------------------------------------------
    def entity_available(self, definition: EntityDefinition) -> bool:
        if definition.requires_scale and not self.scale_connected:
            return False
        if definition.requires_bag and self.selected_bag is None:
            return False
        if definition.requires_active_shot and self.active_shot is None:
            return False
        if definition.requires_no_active_shot and self.active_shot is not None:
            return False
        return True

    def entity_value(self, definition: EntityDefinition) -> Any:
        source, field = definition.source, definition.field
        if source in ("runtime", "controller"):
            return getattr(self, str(field))
        if source == "draft":
            return getattr(self.draft, str(field))
        if source == "scale":
            reading = self.scale.last_reading
            return getattr(reading, str(field)) if reading else None
        if source == "last_shot":
            return self.last_shot.get(str(field)) if self.last_shot else None
        if source == "bag":
            bag = self.selected_bag
            if bag is None:
                return None
            if field == "remaining_g":
                return self._bag_remaining.get(self.selected_slot)
            return getattr(bag, str(field))
        raise HomeAssistantError(f"Unsupported entity source: {source}")

    def entity_attributes(self, definition: EntityDefinition) -> dict[str, Any]:
        if not definition.attributes:
            return {}
        result: dict[str, Any] = {}
        bag = self.selected_bag
        for attribute in definition.attributes:
            if attribute == "scale_connected":
                value = self.scale_connected
            elif attribute == "selected_slot":
                value = self.selected_slot
            elif attribute == "stop_compensation_g":
                value = self.stop_compensation_g
            elif attribute == "shot_plot":
                value = self._shot_plot_points()
            elif attribute == "bag_id":
                value = bag.id if bag else None
            elif attribute == "remaining_g":
                value = self._bag_remaining.get(self.selected_slot) if bag else None
            elif bag and hasattr(bag, attribute):
                value = getattr(bag, attribute)
            else:
                continue
            result[attribute] = value
        return result

    def _shot_plot_points(self) -> list[list[float]]:
        """[epoch_ms, weight_g, flow_g_s] points for the dashboard's live-shot
        graph: the active shot's own samples while one is running (growing
        live), or the last completed shot's otherwise (frozen - see
        _async_finalize) - so the graph shows real data exactly for a shot's
        actual duration instead of a wall-clock rolling window that drifts
        away from it. Absolute epoch timestamps let the chart place points on
        a real time axis despite samples being timestamped in elapsed_ms
        (monotonic, not wall-clock) - see ActiveShot.press_wall_time.
        """
        shot = self.active_shot
        if shot is not None and shot.press_wall_time is not None:
            samples, press_wall_time = shot.samples, shot.press_wall_time
        else:
            samples, press_wall_time = self._last_shot_samples, self._last_shot_press_wall_time
        if not samples or press_wall_time is None:
            return []
        anchor_ms = int(datetime.fromisoformat(press_wall_time).timestamp() * 1000)
        step = max(1, math.ceil(len(samples) / _SHOT_PLOT_MAX_POINTS))
        return [
            [anchor_ms + sample.elapsed_ms, sample.weight_g, round(sample.flow_g_s, 2)]
            for sample in samples[::step]
        ]

    async def async_set_entity_value(
        self, definition: EntityDefinition, value: Any
    ) -> None:
        source, field = definition.source, str(definition.field)
        if source == "bag":
            await self.async_update_recipe_field(field, value)
            return
        if source == "controller":
            if field == "selected_slot":
                await self.async_select_slot(str(value))
                return
            if field == "stop_compensation_g":
                self.stop_compensation_g = float(value)
                await self._async_save_state()
                self._notify(force=True)
                return
            if field == "auto_pi":
                self.auto_pi = bool(value)
                await self._async_save_state()
                self._notify(force=True)
                return
        if source == "draft":
            if field in {"coffee", "roaster"}:
                setattr(self.draft, field, str(value).strip())
            elif field == "roast_date":
                setattr(self.draft, field, value)
            elif field == "starting_mass_g":
                if float(value) <= 0:
                    raise HomeAssistantError("Starting bag mass must be positive")
                self.draft.starting_mass_g = float(value)
            else:
                raise HomeAssistantError(f"Unknown draft field: {field}")
            self._notify(force=True)
            return
        raise HomeAssistantError(f"Entity {definition.key} is not writable")

    async def async_run_action(self, action: str) -> Any:
        actions = {
            "brew": self.async_brew,
            "abort": self.async_abort,
            "tare": self.async_tare,
            "create_bag": self.async_create_bag_from_draft,
        }
        try:
            handler = actions[action]
        except KeyError as err:
            raise HomeAssistantError(f"Unknown Barista Assist action: {action}") from err
        return await handler()

    # ---------------------------------------------------------------------
    # Bag and recipe operations
    # ---------------------------------------------------------------------
    async def async_select_slot(self, slot: str) -> None:
        if slot not in self.definitions.slots:
            raise HomeAssistantError(f"Unknown bean slot: {slot}")
        self.selected_slot = slot
        await self._async_save_state()
        self._notify(force=True)

    def _validate_recipe_field(self, field: str, value: float | int) -> float | int:
        matching = [
            definition
            for definition in (*self.definitions.platform("number"), *self.definitions.platform("select"))
            if definition.source == "bag" and definition.field == field
        ]
        if not matching:
            raise HomeAssistantError(f"Unknown recipe field: {field}")
        definition = matching[0]
        if definition.platform == "select":
            valid = {option_value for _label, option_value in definition.options}
            if value not in valid:
                raise HomeAssistantError(f"Invalid value for {field}: {value}")
            return int(value) if field == "temperature_offset_c" else value
        numeric = float(value)
        assert definition.minimum is not None and definition.maximum is not None
        if not definition.minimum <= numeric <= definition.maximum:
            raise HomeAssistantError(
                f"{field} must be between {definition.minimum} and {definition.maximum}"
            )
        assert definition.step is not None
        steps = (numeric - definition.minimum) / definition.step
        if abs(steps - round(steps)) > 1e-7:
            raise HomeAssistantError(f"{field} must use {definition.step:g}-unit steps")
        return numeric

    async def async_update_recipe_field(self, field: str, value: float | int) -> None:
        bag = self.selected_bag
        if bag is None:
            raise HomeAssistantError("No active bag in the selected slot")
        value = self._validate_recipe_field(field, value)
        await self.hass.async_add_executor_job(
            self.db.update_recipe_field, bag.id, field, value
        )
        await self.async_refresh_cache()

    async def async_new_bag(self, data: dict[str, Any]) -> Bag:
        slot = str(data["slot"])
        if slot not in self.definitions.slots:
            raise HomeAssistantError("Unknown bean slot")
        defaults = self.definitions.defaults["recipe"]
        current = self._bags.get(slot)

        def recipe_value(field: str) -> Any:
            if field in data:
                return data[field]
            if current is not None:
                return getattr(current, field)
            return defaults[field]

        recipe = {
            field: self._validate_recipe_field(field, recipe_value(field))
            for field in (
                "dose_g",
                "grind",
                "target_yield_g",
                "temperature_offset_c",
                "preinfusion_s",
            )
        }
        bag = await self.hass.async_add_executor_job(
            lambda: self.db.new_bag(
                slot=slot,
                coffee_name=str(data["coffee_name"]),
                roaster=data.get("roaster"),
                roast_date=data.get("roast_date"),
                starting_mass_g=float(
                    data.get(
                        "starting_mass_g",
                        self.definitions.defaults["new_bag"]["starting_mass_g"],
                    )
                ),
                dose_g=float(recipe["dose_g"]),
                grind=float(recipe["grind"]),
                target_yield_g=float(recipe["target_yield_g"]),
                temperature_offset_c=int(recipe["temperature_offset_c"]),
                preinfusion_s=float(recipe["preinfusion_s"]),
            )
        )
        await self.async_select_slot(slot)
        await self.async_refresh_cache()
        return bag

    async def async_create_bag_from_draft(self) -> Bag:
        if not self.draft.coffee:
            raise HomeAssistantError("Enter a coffee name before creating the bag")
        bag = await self.async_new_bag(
            {
                "slot": self.selected_slot,
                "coffee_name": self.draft.coffee,
                "roaster": self.draft.roaster,
                "roast_date": self.draft.roast_date.isoformat()
                if self.draft.roast_date
                else None,
                "starting_mass_g": self.draft.starting_mass_g,
            }
        )
        self.draft.coffee = ""
        self.draft.roaster = ""
        self.draft.roast_date = date.today()
        self._notify(force=True)
        return bag

    # ---------------------------------------------------------------------
    # Shot state machine
    # ---------------------------------------------------------------------

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
            threshold = shot.target_yield_g - shot.stop_compensation_g
            if (
                not shot.stop_scheduled
                and elapsed_ms > 1000
                and reading.weight_g >= threshold
            ):
                shot.stop_scheduled = True
                self.hass.async_create_task(
                    self.async_stop_at_target(), "barista_assist_target_stop"
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

    # -- brew / stop / abort / finalize --
    async def async_brew(self) -> str:
        _LOGGER.debug("async_brew called")
        async with self._shot_lock:
            if self.active_shot is not None:
                raise HomeAssistantError("A shot is already active")
            bag = self.selected_bag
            if bag is None:
                raise HomeAssistantError("Create/select an active bag before brewing")
            if not self.machine_limit_confirmed:
                raise HomeAssistantError(
                    "Confirm the Barista Express programmed maximum shot duration before brewing"
                )
            if self.safe_shot_deadline_s <= 5:
                raise HomeAssistantError(
                    "Machine maximum shot duration must exceed the safety margin by at least 5 seconds"
                )
            preinfusion_s = AUTO_PI_DURATION_S if self.auto_pi else bag.preinfusion_s
            if preinfusion_s >= self.safe_shot_deadline_s:
                raise HomeAssistantError(
                    "Pre-infusion must be shorter than the protected shot window"
                )
            if bag.target_yield_g <= self.stop_compensation_g + 1.0:
                raise HomeAssistantError("Stop compensation is too large for target yield")

            self._set_phase(ShotPhase.CONNECTING_SCALE)
            await self.scale.async_ensure_connected()
            await self.scale.async_wait_for_fresh_reading()

            started_at = datetime.now(timezone.utc).isoformat()
            shot_id = await self.hass.async_add_executor_job(
                lambda: self.db.create_shot(
                    bag=bag,
                    started_at=started_at,
                    stop_compensation_g=self.stop_compensation_g,
                )
            )
            self.active_shot = ActiveShot(
                id=shot_id,
                bag=bag,
                started_at=started_at,
                started_monotonic=time.monotonic(),
                target_yield_g=bag.target_yield_g,
                stop_compensation_g=self.stop_compensation_g,
                preinfusion_s=preinfusion_s,
                samples=[],
                # Auto PI never holds the button, so the Bot is never
                # reprogrammed away from its default instant tap - the stop
                # press can go out immediately whenever it's needed.
                quick_press_ready=self.auto_pi,
            )
            _LOGGER.info(
                "Shot %s started: bag=%s dose=%sg target_yield=%sg preinfusion=%ss (auto_pi=%s)",
                shot_id,
                bag.coffee_name,
                bag.dose_g,
                bag.target_yield_g,
                preinfusion_s,
                self.auto_pi,
            )
            self._set_phase(ShotPhase.PREINFUSION)

            try:
                if self.auto_pi:
                    # The Barista Express runs its own pre-infusion on a
                    # single short tap - no hold duration to program, so
                    # this skips the direct-BLE Bot-configure step entirely
                    # and presses through Home Assistant's switchbot
                    # integration only.
                    await self._async_press_brew_bot()
                else:
                    await self._async_prepare_brew_bot(int(preinfusion_s))
                    await self._async_press_brew_bot()
            except Exception:
                await self._async_finalize(ShotPhase.ERROR.value)
                raise
            self.active_shot.press_monotonic = time.monotonic()
            self.active_shot.press_wall_time = datetime.now(timezone.utc).isoformat()
            connect_delay_s = self.active_shot.press_monotonic - self.active_shot.started_monotonic
            _LOGGER.debug(
                "Brew Bot engaged %.2fs after brew was requested (BLE connect+program+press)",
                connect_delay_s,
            )

            self._phase_task = self.hass.async_create_background_task(
                self._mark_extracting_after_preinfusion(shot_id, preinfusion_s),
                "barista_assist_preinfusion_phase",
            )
            self._timeout_task = self.hass.async_create_background_task(
                self._shot_timeout(), "barista_assist_shot_timeout"
            )

            # Tare and start the scale's own physical timer only now that the
            # machine is actually engaged, so both its on-device display and
            # our own zero-weight reference reflect the real start of the
            # shot rather than however long the Bluetooth connection above
            # took. The machine is already pouring by this point - unlike a
            # failure earlier in this method, there's no "the shot never
            # happened" to fall back to, so this can only warn and continue
            # with an untared baseline rather than abort a shot that's
            # already physically running.
            try:
                await self.scale.async_set_flow_smoothing(False)
                await self.scale.async_tare_and_start_timer()
            except Exception as err:
                _LOGGER.warning(
                    "Could not tare the scale or start its timer after "
                    "pressing the brew Bot; continuing with an untared "
                    "weight baseline: %s",
                    err,
                )
            return shot_id

    async def _mark_extracting_after_preinfusion(
        self, shot_id: str, preinfusion_s: float
    ) -> None:
        try:
            await asyncio.sleep(preinfusion_s)
            shot = self.active_shot
            if shot is None or shot.id != shot_id or shot.stop_scheduled:
                return
            self._set_phase(ShotPhase.EXTRACTING)
            if self.auto_pi:
                # Never held the button in the first place (see async_brew),
                # so there's nothing to reprogram back to an instant tap.
                return
            # Reprogram the Bot to an instant tap now, off the time-critical stop
            # path, so the eventual stop/abort press doesn't also hold for
            # preinfusion_s seconds like the start press did. If a real stop
            # needs the Bot before this finishes, it will cancel this attempt
            # (see _async_ensure_quick_stop_press) rather than wait for it.
            try:
                await self._async_prepare_brew_bot(0)
                shot.quick_press_ready = True
            except Exception as err:
                _LOGGER.warning(
                    "Could not reprogram brew Bot for an instant stop press ahead of time: %s",
                    err,
                )
        except asyncio.CancelledError:
            return

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

    async def _shot_timeout(self) -> None:
        try:
            await asyncio.sleep(self.safe_shot_deadline_s)
            if self.active_shot is not None:
                _LOGGER.warning(
                    "Barista Assist shot reached its protected %.1fs stop deadline "
                    "(machine maximum %.1fs, margin %.1fs)",
                    self.safe_shot_deadline_s,
                    self.machine_max_shot_s,
                    self.safety_margin_s,
                )
                await self.async_abort(reason=ShotPhase.TIMEOUT.value)
        except asyncio.CancelledError:
            return

    @staticmethod
    def _elapsed_since_press(shot: ActiveShot) -> float:
        """Time since the machine was actually engaged, for safety-deadline
        checks - not since brewing was requested (see
        ActiveShot.press_monotonic). If the initial press hasn't landed yet,
        the machine isn't running at all yet, so there's nothing to protect
        against; treat it as 0 rather than the (BLE-connect-inflated) time
        since the request.
        """
        if shot.press_monotonic is None:
            return 0.0
        return time.monotonic() - shot.press_monotonic

    async def _async_press_stop(self, shot: ActiveShot, verb: str) -> None:
        """Press the brew Bot to stop/abort the shot, or set STOP_ERROR and raise.

        Resets stop_triggered back to False on failure: both callers set it
        to True *before* attempting the press, to stop a second concurrent
        caller from also pressing - but left True after a failed press, it
        permanently blocks every future stop/abort attempt for this shot
        (including the protected-deadline timeout's own safety-net abort),
        since they all guard on "if shot.stop_triggered: return". A
        transient BLE failure (e.g. BleakOutOfConnectionSlotsError) would
        otherwise leave the shot stuck in STOP_ERROR forever - active_shot
        never clears, Brew never re-enables, and even clicking Abort again
        silently no-ops - with no way out short of reloading the integration.
        _actuation_lock (held by both callers for the whole call) still
        prevents a real concurrent double-press: nothing can observe
        stop_triggered as False again until this method has already returned
        and the lock has been released.
        """
        try:
            await self._async_ensure_quick_stop_press(shot)
            await self._async_press_brew_bot()
        except Exception as err:
            _LOGGER.exception("Failed to press brew Bot while trying to %s the shot", verb)
            self._set_phase(ShotPhase.STOP_ERROR)
            shot.stop_triggered = False
            raise HomeAssistantError(f"Failed to {verb} shot: {err}") from err

    async def async_stop_at_target(self) -> None:
        late = False
        async with self._actuation_lock:
            shot = self.active_shot
            if shot is None or shot.stop_triggered:
                return
            elapsed_s = self._elapsed_since_press(shot)
            _LOGGER.debug("async_stop_at_target called at elapsed=%.2fs", elapsed_s)
            if elapsed_s >= self.safe_shot_deadline_s:  # too close to the deadline to safely press again - see async_abort
                late = True
            else:
                shot.stop_triggered = True
                shot.stop_command_elapsed_ms = int(elapsed_s * 1000)
                self._set_phase(ShotPhase.STOPPING)
                await self._async_press_stop(shot, "stop")
                self._set_phase(ShotPhase.SETTLING)
                self._settle_task = self.hass.async_create_background_task(
                    self._settle_then_finalize(), "barista_assist_settle"
                )
        if late:
            await self.async_abort(reason=ShotPhase.TIMEOUT.value)

    async def _settle_then_finalize(self) -> None:
        try:
            await asyncio.sleep(3.0)
            await self._async_finalize("complete")
        except asyncio.CancelledError:
            return

    async def async_abort(self, *, reason: str = ShotPhase.ABORTED.value) -> None:
        async with self._actuation_lock:
            shot = self.active_shot
            if shot is None or shot.stop_triggered:
                return
            shot.stop_triggered = True
            elapsed_s = self._elapsed_since_press(shot)
            _LOGGER.info("async_abort called (reason=%s) at elapsed=%.2fs", reason, elapsed_s)
            shot.stop_command_elapsed_ms = int(elapsed_s * 1000)
            if elapsed_s < self.safe_shot_deadline_s:  # don't try to stop if machine auto-termination will be within safety_margin_s
                await self._async_press_stop(shot, "abort")
                await asyncio.sleep(3.0)  # settle
                await self._async_finalize(reason)
                return

            # Never press after the protected deadline: pressing after a
            # single-tap/programmed-volume shot naturally ended would start a
            # new one instead of stopping anything. Barista Assist always
            # holds the button for pre-infusion, which Breville's own manual
            # documents as a distinct "manual" mode from that single-tap one -
            # so the current shot may in fact still be running rather than
            # having ended, and there's no reliable way to tell those two
            # situations apart from software alone, so this stays
            # conservative and never presses either way. The machine does
            # appear to have its own independent volume-based safety cutoff
            # that applies here too (see README's shot-duration safety
            # section), but its exact timing isn't something Barista Assist
            # can rely on with full confidence - the user is expected to
            # physically stop the machine themselves if it's still pouring
            # past this point; Barista Assist has no way to detect that.
            _LOGGER.warning(
                "Past the protected deadline (elapsed=%.2fs) - not pressing the "
                "brew Bot; entering manual_stop_required",
                elapsed_s,
            )
            self._set_phase(ShotPhase.MANUAL_STOP_REQUIRED)
            remaining_s = max(0.0, self.machine_max_shot_s - elapsed_s + 1.0)
            self._manual_finalize_task = self.hass.async_create_background_task(
                self._finalize_after_late_abort(remaining_s, reason),
                "barista_assist_late_abort_finalize",
            )

    async def _finalize_after_late_abort(self, delay_s: float, reason: str) -> None:
        try:
            # wait for the machine's own timer to plausibly have ended the shot before closing the DB record
            await asyncio.sleep(delay_s)
            await self._async_finalize(reason)
        except asyncio.CancelledError:
            return

    async def _async_finalize(self, status: str) -> None:
        shot = self.active_shot
        if shot is None:
            return
        _LOGGER.info(
            "Finalizing shot %s as %s (%d samples)", shot.id, status, len(shot.samples)
        )
        try:
            await self.scale.async_stop_timer()
        except Exception as err:
            _LOGGER.debug("Could not stop the scale's own timer: %s", err)
        for task in self._background_tasks():
            if task and task is not asyncio.current_task():
                task.cancel()
        last_weight = shot.samples[-1].weight_g if shot.samples else None

        baseline_features = await self.hass.async_add_executor_job(
            self.db.recent_healthy_features, shot.bag.id
        )
        analysis = analyze_shot(
            shot.samples,
            target_yield_g=shot.target_yield_g,
            preinfusion_s=shot.preinfusion_s,
            baseline=BaselineFeatures(**baseline_features) if baseline_features else None,
        )
        if analysis.invalid_reason is not None:
            _LOGGER.warning(
                "Shot %s could not be flow-classified (%s); excluded from diagnostics and future baselines",
                shot.id,
                analysis.invalid_reason,
            )

        await self.hass.async_add_executor_job(
            lambda: self.db.finalize_shot(
                shot.id,
                ended_at=datetime.now(timezone.utc).isoformat(),
                actual_yield_g=last_weight,
                status=status,
                stop_command_elapsed_ms=shot.stop_command_elapsed_ms,
                samples=shot.samples,
                classification=str(analysis.classification),
                channeling_suspicion=analysis.channeling_suspicion,
                analysis_json=json.dumps(asdict(analysis)),
            )
        )
        self._last_shot_samples = shot.samples
        self._last_shot_press_wall_time = shot.press_wall_time
        self.active_shot = None
        self._set_phase(ShotPhase.IDLE if status == "complete" else ShotPhase(status))
        await self.async_refresh_cache()

    async def async_tare(self) -> None:
        await self.scale.async_ensure_connected()
        await self.scale.async_tare()

    async def async_export_shots_text(self) -> str:
        """Return every stored shot and raw time series as paste-friendly text."""
        return await self.hass.async_add_executor_job(self.db.export_shots_text)
