"""Runtime controller: shot state machine plus bag/application state."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
import logging
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
from .protocol import BookooReading
from .storage import Bag, BaristaDatabase, ShotSample
from .switchbot import SwitchBotBotConfigurator, resolve_bluetooth_address

_LOGGER = logging.getLogger(__name__)
_STORE_VERSION = 1


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
    samples: list[ShotSample]
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
        self.draft = BagDraft(
            roast_date=date.today(),
            starting_mass_g=float(defaults["new_bag"]["starting_mass_g"]),
        )
        self._phase = ShotPhase.IDLE
        self.scale_connected = False
        self.active_shot: ActiveShot | None = None
        self.last_shot: dict[str, Any] | None = None
        self._bags: dict[str, Bag] = {}
        self._bag_remaining: dict[str, float | None] = {}
        self._last_dispatch = 0.0
        self._timeout_task: asyncio.Task[None] | None = None
        self._settle_task: asyncio.Task[None] | None = None
        self._phase_task: asyncio.Task[None] | None = None
        self._manual_finalize_task: asyncio.Task[None] | None = None
        self._shot_lock = asyncio.Lock()
        self._actuation_lock = asyncio.Lock()

    @property
    def status(self) -> str:
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
        await self._async_save_state()
        await self.async_refresh_cache()
        await self.scale.async_start()

    async def async_close(self) -> None:
        for task in (self._timeout_task, self._settle_task, self._phase_task, self._manual_finalize_task):
            if task:
                task.cancel()
        await self.scale.async_stop()

    async def _async_save_state(self) -> None:
        await self.store.async_save(
            {
                "selected_slot": self.selected_slot,
                "stop_compensation_g": self.stop_compensation_g,
                "machine_max_shot_s": self.machine_max_shot_s,
                "safety_margin_s": self.safety_margin_s,
                "safe_shot_deadline_s": self.safe_shot_deadline_s,
            }
        )

    def _set_phase(self, phase: ShotPhase) -> None:
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
        return True

    def entity_value(self, definition: EntityDefinition) -> Any:
        source, field = definition.source, definition.field
        if source == "runtime":
            return getattr(self, str(field))
        if source == "controller":
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

        dose_g = self._validate_recipe_field("dose_g", recipe_value("dose_g"))
        grind = self._validate_recipe_field("grind", recipe_value("grind"))
        target_yield_g = self._validate_recipe_field(
            "target_yield_g", recipe_value("target_yield_g")
        )
        temperature_offset_c = self._validate_recipe_field(
            "temperature_offset_c", recipe_value("temperature_offset_c")
        )
        preinfusion_s = self._validate_recipe_field(
            "preinfusion_s", recipe_value("preinfusion_s")
        )
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
                dose_g=float(dose_g),
                grind=float(grind),
                target_yield_g=float(target_yield_g),
                temperature_offset_c=int(temperature_offset_c),
                preinfusion_s=float(preinfusion_s),
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
    def _handle_scale_connection(self, connected: bool) -> None:
        self.scale_connected = connected
        self._notify(force=True)

    def _handle_reading(self, reading: BookooReading) -> None:
        shot = self.active_shot
        if shot is not None:
            elapsed_ms = int((time.monotonic() - shot.started_monotonic) * 1000)
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

    async def _async_prepare_brew_bot(self, hold_seconds: int) -> None:
        """Program the Bot's stored press-hold duration (0 = an instant tap)."""
        address = resolve_bluetooth_address(self.hass, self.brew_entity)
        if address is None:
            raise HomeAssistantError(
                "Could not resolve the Bluetooth address of the selected brew SwitchBot"
            )
        await SwitchBotBotConfigurator(
            self.hass, address
        ).async_set_long_press_duration(hold_seconds)

    async def _async_press_brew_bot(self) -> None:
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

    async def async_brew(self) -> str:
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
            if bag.preinfusion_s >= self.safe_shot_deadline_s:
                raise HomeAssistantError(
                    "Pre-infusion must be shorter than the protected shot window"
                )
            if bag.target_yield_g <= self.stop_compensation_g + 1.0:
                raise HomeAssistantError("Stop compensation is too large for target yield")

            self._set_phase(ShotPhase.CONNECTING_SCALE)
            await self.scale.async_ensure_connected()
            await self.scale.async_wait_for_fresh_reading()
            await self.scale.async_set_flow_smoothing(False)
            await self.scale.async_tare_and_start_timer()
            await asyncio.sleep(0.20)

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
                samples=[],
            )
            self._set_phase(ShotPhase.PREINFUSION)

            try:
                await self._async_prepare_brew_bot(int(bag.preinfusion_s))
                await self._async_press_brew_bot()
            except Exception:
                await self._async_finalize(ShotPhase.ERROR.value)
                raise

            self._phase_task = self.hass.async_create_background_task(
                self._mark_extracting_after_preinfusion(shot_id, bag.preinfusion_s),
                "barista_assist_preinfusion_phase",
            )
            self._timeout_task = self.hass.async_create_background_task(
                self._shot_timeout(), "barista_assist_shot_timeout"
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

        Pre-empts (rather than waits for) any in-flight proactive attempt, since
        this is called from the time-critical stop/abort path and a competing
        BLE connection to the same Bot must not be left running concurrently.
        """
        if shot.quick_press_ready:
            return
        phase_task = self._phase_task
        if phase_task is not None and not phase_task.done():
            phase_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await phase_task
        if shot.quick_press_ready:
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

    async def async_stop_at_target(self) -> None:
        late = False
        async with self._actuation_lock:
            shot = self.active_shot
            if shot is None or shot.stop_triggered:
                return
            elapsed_s = time.monotonic() - shot.started_monotonic
            if elapsed_s >= self.safe_shot_deadline_s:
                late = True
            else:
                shot.stop_triggered = True
                shot.stop_command_elapsed_ms = int(elapsed_s * 1000)
                self._set_phase(ShotPhase.STOPPING)
                try:
                    await self._async_ensure_quick_stop_press(shot)
                    await self._async_press_brew_bot()
                except Exception as err:
                    _LOGGER.exception("Failed to press brew Bot for target stop")
                    self._set_phase(ShotPhase.STOP_ERROR)
                    raise HomeAssistantError(f"Failed to stop shot: {err}") from err
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
            elapsed_s = time.monotonic() - shot.started_monotonic
            shot.stop_command_elapsed_ms = int(elapsed_s * 1000)
            if elapsed_s < self.safe_shot_deadline_s:
                try:
                    await self._async_ensure_quick_stop_press(shot)
                    await self._async_press_brew_bot()
                except Exception as err:
                    _LOGGER.exception("Failed to press brew Bot while aborting shot")
                    self._set_phase(ShotPhase.STOP_ERROR)
                    raise HomeAssistantError(f"Failed to abort shot: {err}") from err
                await asyncio.sleep(1.0)
                await self._async_finalize(reason)
                return

            # Never press after the protected deadline: the Barista Express may
            # already have ended the programmed shot, and another press could
            # start a new shot. Wait for the machine limit and keep logging.
            self._set_phase(ShotPhase.MANUAL_STOP_REQUIRED)
            remaining_s = max(0.0, self.machine_max_shot_s - elapsed_s + 1.0)
            self._manual_finalize_task = self.hass.async_create_background_task(
                self._finalize_after_late_abort(remaining_s, reason),
                "barista_assist_late_abort_finalize",
            )

    async def _finalize_after_late_abort(self, delay_s: float, reason: str) -> None:
        try:
            await asyncio.sleep(delay_s)
            await self._async_finalize(reason)
        except asyncio.CancelledError:
            return

    async def _async_finalize(self, status: str) -> None:
        shot = self.active_shot
        if shot is None:
            return
        for task in (self._timeout_task, self._settle_task, self._phase_task, self._manual_finalize_task):
            if task and task is not asyncio.current_task():
                task.cancel()
        last_weight = shot.samples[-1].weight_g if shot.samples else None
        await self.hass.async_add_executor_job(
            lambda: self.db.finalize_shot(
                shot.id,
                ended_at=datetime.now(timezone.utc).isoformat(),
                actual_yield_g=last_weight,
                status=status,
                stop_command_elapsed_ms=shot.stop_command_elapsed_ms,
                samples=shot.samples,
            )
        )
        self.active_shot = None
        self._set_phase(ShotPhase.IDLE if status == "complete" else ShotPhase(status))
        await self.async_refresh_cache()

    async def async_tare(self) -> None:
        await self.scale.async_ensure_connected()
        await self.scale.async_tare()

    async def async_export_shots_text(self) -> str:
        """Return every stored shot and raw time series as paste-friendly text."""
        return await self.hass.async_add_executor_job(self.db.export_shots_text)
