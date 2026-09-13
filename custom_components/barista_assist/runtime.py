"""Runtime controller: shot state machine plus bag/application state.

BaristaRuntime itself is composed from several mixins, one per concern - see
each file's own module docstring:
  - runtime_entities.py    - the declarative entity interface + dashboard text
  - runtime_bag.py         - bag/recipe CRUD + roast-level warm-start priors
  - runtime_shot.py        - the brew/stop/abort/finalize state machine
  - runtime_peripherals.py - BLE hardware + HA notification/event I/O
This file keeps only what's genuinely shared by all of them: __init__ (since
every mixin operates on state it sets up), and the small
config/lifecycle methods that touch almost every other section's state
rather than belonging to any one of them.
"""

from __future__ import annotations

import asyncio
from datetime import date
import logging
from pathlib import Path
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError  # noqa: F401 - re-exported: read directly by tests/test_runtime.py
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store

from .bookoo import BookooUltraClient
from .const import (
    CONF_BREW_ENTITY,
    CONF_MACHINE_LIMIT_CONFIRMED,
    CONF_NOTIFY_SERVICE,
    CONF_SCALE_ADDRESS,
    DOMAIN,
    SIGNAL_UPDATE,
)
from .definitions import load_definitions
from .runtime_bag import RuntimeBagMixin
from .runtime_entities import RuntimeEntitiesMixin
from .runtime_peripherals import RuntimePeripheralsMixin
from .runtime_shared import (
    ActiveShot,
    BagDraft,
    ShotPhase,
    _FLAVOR_AXES,
    _GRIND_BAND_CONTROLLER_FIELDS,
    _GRIND_BAND_DELTA_FIELDS,
    _GRIND_BAND_MAX_FIELDS,
    _recipe_snapshot,
)
from .runtime_shot import RuntimeShotMixin
from .storage import Bag, BaristaDatabase, ShotSample

_LOGGER = logging.getLogger(__name__)
_STORE_VERSION = 1
def _grind_band_by_name(bands: list[dict[str, Any]], name: str) -> dict[str, Any]:
    """expert_rules.grind_correction.bands' entry for one band `name` - used
    to seed each grind_band_* controller attribute from its YAML default
    (see __init__/async_initialize/_GRIND_BAND_MAX_FIELDS/
    _GRIND_BAND_DELTA_FIELDS)."""
    return next(band for band in bands if band["name"] == name)


class BaristaRuntime(
    RuntimeEntitiesMixin, RuntimeBagMixin, RuntimeShotMixin, RuntimePeripheralsMixin
):
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
        self.early_stop_margin_min_g = float(
            defaults["controller"]["early_stop_margin_min_g"]
        )
        self.early_stop_margin_max_g = float(
            defaults["controller"]["early_stop_margin_max_g"]
        )
        self.adapt_pi = bool(defaults["controller"]["adapt_pi"])
        self.machine_pi_s = float(defaults["controller"]["machine_pi_s"])
        self.machine_max_shot_s = float(defaults["controller"]["machine_max_shot_s"])
        self.safety_margin_s = float(defaults["controller"]["safety_margin_s"])
        self.stop_latency_normal_s = float(defaults["controller"]["stop_latency_normal_s"])
        self.stop_latency_elevated_s = float(defaults["controller"]["stop_latency_elevated_s"])
        flavor_min_step = self.definitions.expert_rules["flavor_correction"]["minimum_meaningful_step"]
        self.min_step_target_yield_g = float(flavor_min_step["target_yield_g"])
        self.min_step_dose_g = float(flavor_min_step["dose_g"])
        self.min_step_temperature_offset_c = float(flavor_min_step["temperature_offset_c"])
        grind_bands = self.definitions.expert_rules["grind_correction"]["bands"]
        for band_name, attr in _GRIND_BAND_MAX_FIELDS.items():
            setattr(self, attr, float(_grind_band_by_name(grind_bands, band_name)["duration_ratio_max"]))
        for band_name, attr in _GRIND_BAND_DELTA_FIELDS.items():
            setattr(self, attr, float(_grind_band_by_name(grind_bands, band_name)["grind_delta"]))
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
        self._bags: dict[str, Bag] = {}
        self._bag_remaining: dict[str, float | None] = {}
        # bag_id -> axis -> that bag's most-recent-first answered flavor tags
        # (see storage.recent_flavor_tags) - cached the same way
        # _bag_remaining is, since entity_value()/native_value is a sync
        # property and must never touch the database directly.
        self._bag_flavor_tags: dict[str, dict[str, list[str]]] = {}
        self._flavor_notification_tasks: dict[str, asyncio.Task[None]] = {}
        self._flavor_action_unsub: Any = None
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
    def safe_shot_deadline_s(self) -> float:
        return self.machine_max_shot_s - self.safety_margin_s

    @property
    def machine_limit_confirmed(self) -> bool:
        return bool(self.entry.options.get(CONF_MACHINE_LIMIT_CONFIRMED, False))

    @property
    def selected_bag(self) -> Bag | None:
        return self._bags.get(self.selected_slot)

    @property
    def grind_band_healthy_delta_label(self) -> str:
        """healthy's grind_delta is fixed at 0.0 (grind_correction.
        recommend_grind_delta's overshoot damping uses that exact value to
        find "no correction needed" - see _GRIND_BAND_DELTA_FIELDS) - this
        read-only sensor fills that slot in the dashboard for visual parity
        with the other bands' editable grind_delta entities."""
        return "0"

    # ---------------------------------------------------------------------
    # Lifecycle
    # ---------------------------------------------------------------------
    async def async_initialize(self) -> None:
        """Load durable state, migrate the database, and start BLE."""
        await self.hass.async_add_executor_job(self.db.initialize)
        state = await self.store.async_load() or {}
        selected = state.get("selected_slot")
        if selected in self.definitions.slots:
            self.selected_slot = selected

        self.early_stop_margin_min_g = float(
            state.get(
                "early_stop_margin_min_g",
                self.definitions.defaults["controller"]["early_stop_margin_min_g"],
            )
        )
        self.early_stop_margin_max_g = float(
            state.get(
                "early_stop_margin_max_g",
                self.definitions.defaults["controller"]["early_stop_margin_max_g"],
            )
        )
        self.adapt_pi = bool(
            state.get("adapt_pi", self.definitions.defaults["controller"]["adapt_pi"])
        )
        self.machine_pi_s = float(
            state.get("machine_pi_s", self.definitions.defaults["controller"]["machine_pi_s"])
        )
        self.machine_max_shot_s = float(
            state.get(
                "machine_max_shot_s", self.definitions.defaults["controller"]["machine_max_shot_s"]
            )
        )
        self.safety_margin_s = float(
            state.get("safety_margin_s", self.definitions.defaults["controller"]["safety_margin_s"])
        )
        self.stop_latency_normal_s = float(
            state.get(
                "stop_latency_normal_s",
                self.definitions.defaults["controller"]["stop_latency_normal_s"],
            )
        )
        self.stop_latency_elevated_s = float(
            state.get(
                "stop_latency_elevated_s",
                self.definitions.defaults["controller"]["stop_latency_elevated_s"],
            )
        )
        flavor_min_step = self.definitions.expert_rules["flavor_correction"]["minimum_meaningful_step"]
        self.min_step_target_yield_g = float(
            state.get("min_step_target_yield_g", flavor_min_step["target_yield_g"])
        )
        self.min_step_dose_g = float(state.get("min_step_dose_g", flavor_min_step["dose_g"]))
        self.min_step_temperature_offset_c = float(
            state.get("min_step_temperature_offset_c", flavor_min_step["temperature_offset_c"])
        )
        for attr in _GRIND_BAND_CONTROLLER_FIELDS:
            state.setdefault(attr, getattr(self, attr))
            setattr(self, attr, float(state[attr]))
        await self._async_save_state()
        await self.async_refresh_cache()
        await self.scale.async_start()
        self._flavor_action_unsub = self.hass.bus.async_listen(
            "mobile_app_notification_action", self._handle_flavor_notification_action
        )
        _LOGGER.debug(
            "BaristaRuntime initialized: selected_slot=%s adapt_pi=%s machine_max_shot_s=%.1f "
            "safety_margin_s=%.1f early_stop_margin=%.2f-%.2fg stop_latency=%.2f/%.2fs "
            "(normal/elevated) notify_service=%s",
            self.selected_slot,
            self.adapt_pi,
            self.machine_max_shot_s,
            self.safety_margin_s,
            self.early_stop_margin_min_g,
            self.early_stop_margin_max_g,
            self.stop_latency_normal_s,
            self.stop_latency_elevated_s,
            self.entry.options.get(CONF_NOTIFY_SERVICE) or "(disabled)",
        )

    async def async_close(self) -> None:
        _LOGGER.debug("Closing BaristaRuntime")
        for task in self._background_tasks():
            if task:
                task.cancel()
        for task in self._flavor_notification_tasks.values():
            task.cancel()
        if self._flavor_action_unsub is not None:
            self._flavor_action_unsub()
            self._flavor_action_unsub = None
        await self.scale.async_stop()

    def _background_tasks(self) -> tuple[asyncio.Task[None] | None, ...]:
        return (self._timeout_task, self._settle_task, self._phase_task, self._manual_finalize_task)

    async def _async_save_state(self) -> None:
        await self.store.async_save(
            {
                "selected_slot": self.selected_slot,
                "early_stop_margin_min_g": self.early_stop_margin_min_g,
                "early_stop_margin_max_g": self.early_stop_margin_max_g,
                "adapt_pi": self.adapt_pi,
                "machine_pi_s": self.machine_pi_s,
                "machine_max_shot_s": self.machine_max_shot_s,
                "safety_margin_s": self.safety_margin_s,
                "stop_latency_normal_s": self.stop_latency_normal_s,
                "stop_latency_elevated_s": self.stop_latency_elevated_s,
                "safe_shot_deadline_s": self.safe_shot_deadline_s,
                "min_step_target_yield_g": self.min_step_target_yield_g,
                "min_step_dose_g": self.min_step_dose_g,
                "min_step_temperature_offset_c": self.min_step_temperature_offset_c,
                **{attr: getattr(self, attr) for attr in _GRIND_BAND_CONTROLLER_FIELDS},
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
        self._bag_flavor_tags = {}
        self._bag_shot_health = {}
        self._bag_puck_prep_streak = {}
        for bag in self._bags.values():
            # A nested comprehension here would need `await` inside a
            # comprehension whose immediate enclosing scope is another
            # comprehension, not this async function itself - not valid
            # Python (unlike the single-level ones above) - hence the loop.
            # limit=expert_rules.flavor_correction.state_replay_limit (not
            # recent_flavor_tags's own default of 5, tuned for the old
            # fixed-count persistence check): flavor_correction.
            # resolve_flavor_state replays a whole primary/repeat/confirm/
            # escalate cycle from scratch each time, which can span more
            # than 5 answered shots.
            self._bag_flavor_tags[bag.id] = {
                axis: await self.hass.async_add_executor_job(
                    self.db.recent_flavor_tags,
                    bag.id,
                    axis,
                    self.definitions.expert_rules["flavor_correction"]["state_replay_limit"],
                )
                for axis in _FLAVOR_AXES
            }
            self._bag_shot_health[bag.id] = await self.hass.async_add_executor_job(
                self.db.latest_shot_health, bag.id
            )
            self._bag_puck_prep_streak[bag.id] = await self.hass.async_add_executor_job(
                self.db.consecutive_puck_prep_issue_count,
                bag.id,
                {**_recipe_snapshot(bag), "grind": bag.grind},
            )
        # _last_shot_samples backs the Live Shot card's frozen plot for the
        # last completed shot (_shot_plot_points) - otherwise only ever set
        # in-memory when a shot finishes during this same runtime session
        # (_async_finalize), so without this it goes blank after every
        # restart even though self.last_shot's own metadata (classification,
        # yield, etc.) correctly reloads from the database above.
        self._last_shot_samples = (
            await self.hass.async_add_executor_job(self.db.full_shot_samples, self.last_shot["id"])
            if self.last_shot
            else []
        )
        self._notify(force=True)

    def _notify(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_dispatch < 0.5:
            return
        self._last_dispatch = now
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)
