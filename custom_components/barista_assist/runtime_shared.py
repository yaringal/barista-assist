"""Types and constants shared by the runtime_*.py mixins - the bottom of the
runtime package's own dependency graph (imports nothing from any other
runtime_*.py file, so every mixin - and runtime.py itself - can import from
here with no circular-import risk)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any

from .storage import Bag, ShotSample


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
    # "medium" default: most bags dialed in with this integration so far
    # have been medium roasts, and roast_level_ratio_prior's medium ratio
    # (2.2) is the best-corroborated of the three anyway (see its own
    # comment in definitions.yaml) - "Not specified" is still a real,
    # selectable option for a bag that genuinely isn't one.
    roast_level: str = "medium"
    starting_mass_g: float = 250.0


@dataclass(slots=True)
class ActiveShot:
    """In-memory state for the current shot."""

    id: str
    bag: Bag
    started_at: str
    started_monotonic: float
    target_yield_g: float
    early_stop_margin_min_g: float
    preinfusion_s: float
    # flow_analysis.blended_expected_flow_g_s's rate for this bag's
    # roast_level (docs/DESIGN.md's Phase 3b - not this bag's
    # own history), fixed once at brew time (see async_brew) - feeds the
    # Live Shot/Shot History charts' idealized-curve overlay (_shot_markers)
    # so it stays stable for this shot even if the roast-level pool changes
    # before it finishes.
    expected_flow_g_s: float
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
    stop_command_elapsed_ms: int | None = None
    stop_scheduled: bool = False
    stop_triggered: bool = False
    quick_press_ready: bool = False
    # The actual _effective_stop_margin_g(shot) value at the instant the
    # automatic target-weight stop was scheduled - None for a shot stopped
    # by manual abort/timeout instead, since no weight-triggered margin was
    # ever computed for it. Recorded (not recomputed after the fact) because
    # early_stop_margin_min_g/the learned latencies keep changing, so a
    # historical shot's own record would otherwise silently drift to
    # whatever those settings happen to be *now* instead of what actually
    # applied when it ran.
    effective_stop_margin_g: float | None = None


def _recipe_snapshot(bag: Bag) -> dict[str, Any]:
    """dose_g/target_yield_g/temperature_offset_c/preinfusion_s as a plain
    dict - grind_correction.hold_constant's own field list, and the base
    grind_correction.recommend_grind_delta's current_recipe/previous_shot
    params and storage.consecutive_puck_prep_issue_count's current_recipe
    both need (the latter adds "grind" on top: {**_recipe_snapshot(bag),
    "grind": bag.grind}). Built once here so the field list can't drift
    between call sites."""
    return {
        "dose_g": bag.dose_g,
        "target_yield_g": bag.target_yield_g,
        "temperature_offset_c": bag.temperature_offset_c,
        "preinfusion_s": bag.preinfusion_s,
    }


# --- Constants shared across more than one runtime_*.py mixin ---------------

# Prefix + ":"-delimited fields ("barista_flavor:{shot_id}:{axis}:{tag}") for
# the actionable-notification action ids the flavor-feedback notifications
# use (see runtime_peripherals.py's _async_send_flavor_feedback_notifications/
# _handle_flavor_notification_action) - namespaced so the bus listener never
# reacts to an unrelated integration's own mobile_app_notification_action.
_FLAVOR_AXES = {
    "extraction": [("sour_sharp", "Sour / Sharp"), ("bitter_harsh", "Bitter / Harsh")],
    "mouthfeel": [("thin_weak", "Thin / Weak"), ("dry_astringent", "Dry / Astringent")],
}
# tag value -> the same human-readable label _FLAVOR_AXES already uses for
# notification action button titles - reused by runtime_entities.py's
# _recommended_flavor_note/_stalled_flavor_tags so the dashboard note never
# shows a raw tag key like "sour_sharp".
_FLAVOR_TAG_LABELS = {tag: title for tags in _FLAVOR_AXES.values() for tag, title in tags}
# expert_rules.flavor_correction.minimum_meaningful_step's own YAML field
# name -> the dashboard-editable BaristaRuntime attribute that overrides it
# (see runtime_entities.py's _flavor_correction_config and
# runtime_entities.py's async_set_entity_value "min_step_*" handling) - these
# are ordinary recipe-adjacent settings a user tunes from the dashboard, the
# same as early_stop_margin_min_g/machine_max_shot_s, not a config-flow
# option.
_MIN_STEP_FIELDS = {
    "target_yield_g": "min_step_target_yield_g",
    "dose_g": "min_step_dose_g",
    "temperature_offset_c": "min_step_temperature_offset_c",
}
# expert_rules.grind_correction.bands' own "name" -> the dashboard-editable
# BaristaRuntime attribute that overrides that band's duration_ratio_max/
# grind_delta (see runtime_entities.py's _grind_correction_config and
# async_set_entity_value's "grind_band_*" handling). grossly_restrictive has
# no max entry - its duration_ratio_max stays the fixed catch-all None - and
# healthy has no delta entry - its grind_delta stays fixed at 0.0, the value
# grind_correction.recommend_grind_delta's overshoot damping uses to find
# "no correction needed" (healthy_index).
_GRIND_BAND_MAX_FIELDS = {
    "grossly_fast": "grind_band_grossly_fast_max",
    "moderately_fast": "grind_band_moderately_fast_max",
    "slightly_fast": "grind_band_slightly_fast_max",
    "healthy": "grind_band_healthy_max",
    "slightly_restrictive": "grind_band_slightly_restrictive_max",
    "moderately_restrictive": "grind_band_moderately_restrictive_max",
}
_GRIND_BAND_DELTA_FIELDS = {
    "grossly_fast": "grind_band_grossly_fast_delta",
    "moderately_fast": "grind_band_moderately_fast_delta",
    "slightly_fast": "grind_band_slightly_fast_delta",
    "slightly_restrictive": "grind_band_slightly_restrictive_delta",
    "moderately_restrictive": "grind_band_moderately_restrictive_delta",
    "grossly_restrictive": "grind_band_grossly_restrictive_delta",
}
_GRIND_BAND_CONTROLLER_FIELDS = tuple(_GRIND_BAND_MAX_FIELDS.values()) + tuple(
    _GRIND_BAND_DELTA_FIELDS.values()
)
