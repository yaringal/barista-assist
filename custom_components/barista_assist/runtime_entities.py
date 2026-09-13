"""The declarative entity interface every platform file (number.py,
sensor.py, select.py, switch.py, button.py, date.py, text.py) drives -
generic entity_available/entity_value/entity_attributes/
async_set_entity_value/async_run_action dispatch - plus the dashboard-facing
"current -> recommended" text builders that back the "recommended"
attribute. Mixed into BaristaRuntime (see runtime.py) - not usable
standalone."""

from __future__ import annotations

import logging
import math
from typing import Any

from homeassistant.exceptions import HomeAssistantError

from .definitions import EntityDefinition
from .flavor_correction import resolve_flavor_state
from .runtime_shared import (
    _FLAVOR_TAG_LABELS,
    _GRIND_BAND_CONTROLLER_FIELDS,
    _GRIND_BAND_DELTA_FIELDS,
    _GRIND_BAND_MAX_FIELDS,
    _MIN_STEP_FIELDS,
)
from .storage import Bag

_LOGGER = logging.getLogger(__name__)
# Cap on points returned by _shot_plot_points, regardless of how many raw
# samples a shot has - keeps the `shot_plot` attribute payload bounded for an
# unusually long shot instead of growing without limit.
_SHOT_PLOT_MAX_POINTS = 300


class RuntimeEntitiesMixin:
    """Mixed into BaristaRuntime - see that class for the shared __init__/state."""

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
            if field == "recommended_grind_note":
                return self._recommended_grind_note()
            return self.last_shot.get(str(field)) if self.last_shot else None
        if source == "bag":
            bag = self.selected_bag
            if bag is None:
                return None
            if field == "remaining_g":
                return self._bag_remaining.get(self.selected_slot)
            if field == "recommended_flavor_note":
                return self._recommended_flavor_note(bag)
            return getattr(bag, str(field))
        raise HomeAssistantError(f"Unsupported entity source: {source}")

    def _recommended_grind_note(self) -> str | None:
        """"Current -> recommended" text for the last shot's grind
        (docs/DESIGN.md section 28), e.g. "15.0 -> 14.0". Based on the grind
        that shot actually ran at (shots.grind, a snapshot taken when the
        shot was created) rather than the bag's live current grind, which
        may have since been changed for an unrelated reason and would no
        longer match what recommended_grind_delta was computed against.
        None when the last shot has no recommendation at all (healthy, or
        excluded as puck_prep_issue/invalid_measurement)."""
        if not self.last_shot:
            return None
        delta = self.last_shot.get("recommended_grind_delta")
        if delta is None:
            return None
        current = self.last_shot["grind"]
        return f"{current:g} → {current + delta:g}"

    def _flavor_correction_config(self) -> dict[str, Any]:
        """expert_rules.flavor_correction, with minimum_meaningful_step
        overridden by this bag's dashboard-editable min_step_* attributes
        (see _MIN_STEP_FIELDS) - the live, possibly user-tuned values,
        not just the YAML defaults. Returns a copy; never mutates
        self.definitions.expert_rules in place."""
        config = self.definitions.expert_rules["flavor_correction"]
        overrides = {field: getattr(self, attr) for field, attr in _MIN_STEP_FIELDS.items()}
        return {
            **config,
            "minimum_meaningful_step": {**config["minimum_meaningful_step"], **overrides},
        }

    def _grind_correction_config(self) -> dict[str, Any]:
        """expert_rules.grind_correction, with each band's duration_ratio_max/
        grind_delta overridden by this installation's dashboard-editable
        grind_band_* attributes (see _GRIND_BAND_MAX_FIELDS/
        _GRIND_BAND_DELTA_FIELDS) - the live, possibly user-tuned values, not
        just the YAML defaults. grossly_restrictive's duration_ratio_max
        (there is no such override - it's always the catch-all last band)
        and healthy's grind_delta (always 0.0 - grind_correction.
        recommend_grind_delta's overshoot damping locates "no correction
        needed" by that exact value) are left untouched. Returns a copy;
        never mutates self.definitions.expert_rules in place."""
        config = self.definitions.expert_rules["grind_correction"]
        bands = []
        for band in config["bands"]:
            name = band["name"]
            overridden = dict(band)
            max_attr = _GRIND_BAND_MAX_FIELDS.get(name)
            if max_attr is not None:
                overridden["duration_ratio_max"] = getattr(self, max_attr)
            delta_attr = _GRIND_BAND_DELTA_FIELDS.get(name)
            if delta_attr is not None:
                overridden["grind_delta"] = getattr(self, delta_attr)
            bands.append(overridden)
        return {**config, "bands": bands}

    def _stalled_flavor_tags(self, bag: Bag) -> list[str]:
        """Which axes have converged as far as they can go with nowhere
        further to escalate to (resolve_flavor_state's own "recommendation
        is None but active_tag is still set" signal) - a real,
        informative state _all_flavor_field_recommendations can't
        represent on its own (it only ever returns fields that *do* have a
        recommendation), so _recommended_flavor_note surfaces it
        separately instead of staying silent."""
        config = self._flavor_correction_config()
        stalled = []
        for tags_by_axis in self._bag_flavor_tags.get(bag.id, {}).values():
            state = resolve_flavor_state(list(reversed(tags_by_axis)), config)
            if state["recommendation"] is None and state["active_tag"] is not None:
                stalled.append(state["active_tag"])
        return stalled

    def _all_flavor_field_recommendations(self, bag: Bag) -> dict[str, tuple[float, float, str]]:
        """recipe field -> (current, recommended, tag) for every axis's
        current-stage recommendation on this bag (docs/DESIGN.md's Phase 5
        escalation model, replayed fresh via
        flavor_correction.resolve_flavor_state - derived, not
        persisted, same convention as elsewhere in this class), regardless
        of whether grind still has something to correct - that suppression
        only applies to the single *active* recommendation
        (_active_flavor_field_recommendation below), not this "every lever
        that could help" diagnostic view backing the last-shot summary.

        Two axes can recommend the same field at once (bitter_harsh and
        dry_astringent both use the yield lever, for instance) - the axis
        iteration order (_FLAVOR_AXES: extraction, then mouthfeel) decides
        which one wins for that field; the other is dropped rather than
        combined (logged at debug level, not silent, since it's a real
        collision worth being able to spot). Not yet resolved more precisely
        than that - a rare enough edge case to leave for once it's actually
        seen in practice."""
        config = self._flavor_correction_config()
        result: dict[str, tuple[float, float, str]] = {}
        for tags_by_axis in self._bag_flavor_tags.get(bag.id, {}).values():
            # storage.recent_flavor_tags (cached here in async_refresh_cache)
            # returns newest-first; resolve_flavor_state replays oldest-first.
            state = resolve_flavor_state(list(reversed(tags_by_axis)), config)
            recommendation = state["recommendation"]
            if recommendation is None:
                continue
            field = recommendation["field"]
            if field in result:
                _LOGGER.debug(
                    "Bag %s: %s axis's recommendation for %s dropped - %s already claimed it",
                    bag.id,
                    state["active_tag"],
                    field,
                    result[field][2],
                )
                continue
            current = getattr(bag, field)
            signed_delta = (
                recommendation["delta"]
                if recommendation["direction"] == "increase"
                else -recommendation["delta"]
            )
            result[field] = (current, current + signed_delta, state["active_tag"])
        return result

    def _active_flavor_field_recommendation(self, bag: Bag) -> dict[str, tuple[float, float, str]]:
        """The single future-shot recipe recommendation (docs/DESIGN.md's
        Phase 5 suppress-guard): {} entirely while this bag's last
        shot still needs grind correcting (not yet classified
        "healthy", per _bag_shot_health cached in async_refresh_cache), and
        narrowed to at most one field even when
        _all_flavor_field_recommendations finds more than one (same
        axis-iteration-order tie-break that method already uses for a
        same-field collision, just extended into a cross-field "only one
        lever active at all" rule)."""
        health = self._bag_shot_health.get(bag.id)
        if health is not None and health["classification"] != "healthy":
            return {}
        all_recommendations = self._all_flavor_field_recommendations(bag)
        if not all_recommendations:
            return {}
        field = next(iter(all_recommendations))
        return {field: all_recommendations[field]}

    def _puck_prep_streak_note(self, bag: Bag) -> str | None:
        """A note once this bag's consecutive puck_prep_issue-at-unchanged-
        recipe streak (_bag_puck_prep_streak, cached in async_refresh_cache)
        reaches the streak threshold - None below that, a real "nothing to
        flag" state, not a fault."""
        streak = self._bag_puck_prep_streak.get(bag.id, 0)
        if not self._puck_prep_issue_streak_reached(streak):
            return None
        return f"puck_prep_issue x{streak} in a row at this recipe - grind overridden to coarsen"

    def _recommended_flavor_note(self, bag: Bag) -> str | None:
        """"Current -> recommended" text per flavor-correction axis (docs/
        DESIGN.md section 13), e.g. "target_yield_g: 36.0 -> 41.0
        (Sour / Sharp)", joined with "; " when more than one field currently
        has a recommendation, plus a note for any axis that's converged as
        far as it can go with nowhere further to escalate to
        (_stalled_flavor_tags) and the puck_prep_issue streak note (see
        _puck_prep_streak_note) when applicable. None when nothing has
        anything to report at all - that's a real "no recommendation"
        state, not a fault. Tags are shown via _FLAVOR_TAG_LABELS (the same
        human-readable labels the notification action buttons use), not
        their raw "sour_sharp"-style keys."""
        notes = [
            f"{field}: {current:g} → {recommended:g} ({_FLAVOR_TAG_LABELS[tag]})"
            for field, (current, recommended, tag) in self._all_flavor_field_recommendations(
                bag
            ).items()
        ]
        notes.extend(
            f"{_FLAVOR_TAG_LABELS[tag]} isn't resolving with automatic adjustment alone"
            for tag in self._stalled_flavor_tags(bag)
        )
        streak_note = self._puck_prep_streak_note(bag)
        if streak_note is not None:
            notes.append(streak_note)
        return "; ".join(notes) if notes else None

    def _recipe_field_short_note(self, bag: Bag, field: str) -> str | None:
        """Compact "current -> recommended" text (e.g. "18.0 -> 18.5") for
        one recipe field, without the "(tag)" annotation
        _recommended_flavor_note includes - sized for a "recommended"
        secondary attribute on the same recipe field's own tile (see
        _recommended_note_for/entity_attributes) rather than a separate
        tile or the combined dashboard summary. Uses
        _active_flavor_field_recommendation (the single, suppress-gated
        recommendation), not the full diagnostic set."""
        recommendation = self._active_flavor_field_recommendation(bag).get(field)
        if recommendation is None:
            return None
        current, recommended, _tag = recommendation
        return f"{current:g} → {recommended:g}"

    def _recommended_note_for(self, definition: EntityDefinition, bag: Bag | None) -> str | None:
        """The "recommended" attribute's value for one recipe-field entity
        (dose/grind/target_yield/temperature_offset) - a decoration on that
        field's own tile (via dashboard.yaml's state_content), not a
        separate card. Dispatches by definition.field to whichever
        recommendation source actually covers it: grind's comes from the
        last completed shot (_recommended_grind_note, Phase 4); the others
        come from flavor_correction's persistent-pattern tags on the
        selected bag (_recipe_field_short_note, Phase 5)."""
        if definition.field == "grind":
            return self._recommended_grind_note()
        if bag is None:
            return None
        return self._recipe_field_short_note(bag, str(definition.field))

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
            elif attribute == "early_stop_margin_min_g":
                value = self.early_stop_margin_min_g
            elif attribute == "early_stop_margin_max_g":
                value = self.early_stop_margin_max_g
            elif attribute == "shot_plot":
                value = self._shot_plot_points()
            elif attribute == "shot_markers":
                value = self._shot_markers()
            elif attribute == "bag_id":
                value = bag.id if bag else None
            elif attribute == "remaining_g":
                value = self._bag_remaining.get(self.selected_slot) if bag else None
            elif attribute == "recommended":
                value = self._recommended_note_for(definition, bag)
            elif bag and hasattr(bag, attribute):
                value = getattr(bag, attribute)
            else:
                continue
            result[attribute] = value
        return result

    def _shot_plot_points(self) -> list[list[float]]:
        """[elapsed_ms, weight_g, flow_g_s] points for the dashboard's Live
        Shot card: the active shot's own samples while one is running
        (growing live), or the last completed shot's otherwise (frozen).

        Deliberately relative to the shot's own start, not real wall-clock
        time: an earlier version returned absolute epoch timestamps for an
        apexcharts-card-based graph, but that card's rolling time window
        always tracks real "now", not the timestamp of whatever data it was
        last given - a finished shot anchored to its own real press time
        would render correctly for a while, then silently scroll off screen
        as wall-clock time kept moving while the shot's own timestamps
        didn't. barista-assist-live-shot-card (a plain custom element,
        sharing its rendering with the Shots view's own per-shot chart)
        plots elapsed seconds on its x-axis instead, so a frozen shot simply
        has nothing to do with real time and can never drift out of view.
        """
        shot = self.active_shot
        samples = shot.samples if shot is not None and shot.press_monotonic is not None else self._last_shot_samples
        if not samples:
            return []
        step = max(1, math.ceil(len(samples) / _SHOT_PLOT_MAX_POINTS))
        return [
            [sample.elapsed_ms, sample.weight_g, round(sample.flow_g_s, 2)]
            for sample in samples[::step]
        ]

    def _shot_markers(self) -> dict[str, float | int | None]:
        """Metadata the Live Shot/Shot History charts overlay on top of
        _shot_plot_points' raw [elapsed_ms, weight_g, flow_g_s] points: the
        pre-infusion/extraction boundary, the stop-press instant once it's
        actually happened, and this shot's own expected flow rate (roast-
        level-keyed, not derived from this bag's own history - see
        docs/DESIGN.md's Phase 3b - fixed once per shot at
        brew time - see async_brew, ActiveShot.
        expected_flow_g_s) for the frontend's flat-then-ramp idealized
        curve, derived there from target_yield_g. Same live-vs-frozen
        dual source as _shot_plot_points. None values mean "not known
        yet" (e.g. stop_command_elapsed_ms before the shot has actually
        stopped) - the frontend must not treat that as zero."""
        shot = self.active_shot
        if shot is not None and shot.press_monotonic is not None:
            return {
                "preinfusion_ms": int(shot.preinfusion_s * 1000),
                "stop_command_elapsed_ms": shot.stop_command_elapsed_ms,
                "expected_flow_g_s": shot.expected_flow_g_s,
                "target_yield_g": shot.target_yield_g,
            }
        if self.last_shot:
            return {
                "preinfusion_ms": int(self.last_shot["preinfusion_s"] * 1000),
                "stop_command_elapsed_ms": self.last_shot.get("stop_command_elapsed_ms"),
                "expected_flow_g_s": self.last_shot.get("expected_flow_g_s"),
                "target_yield_g": self.last_shot["target_yield_g"],
            }
        return {}

    async def async_set_entity_value(
        self, definition: EntityDefinition, value: Any
    ) -> None:
        source, field = definition.source, str(definition.field)
        _LOGGER.debug("Setting entity value: source=%s field=%s value=%r", source, field, value)
        if source == "bag":
            await self.async_update_recipe_field(field, value)
            return
        if source == "controller":
            if field == "selected_slot":
                await self.async_select_slot(str(value))
                return
            if field == "early_stop_margin_min_g":
                self.early_stop_margin_min_g = float(value)
                await self._async_save_state()
                self._notify(force=True)
                return
            if field == "early_stop_margin_max_g":
                self.early_stop_margin_max_g = float(value)
                await self._async_save_state()
                self._notify(force=True)
                return
            if field == "adapt_pi":
                self.adapt_pi = bool(value)
                await self._async_save_state()
                self._notify(force=True)
                return
            if field == "machine_pi_s":
                self.machine_pi_s = float(value)
                await self._async_save_state()
                self._notify(force=True)
                return
            if field == "machine_max_shot_s":
                self.machine_max_shot_s = float(value)
                await self._async_save_state()
                self._notify(force=True)
                return
            if field == "safety_margin_s":
                self.safety_margin_s = float(value)
                await self._async_save_state()
                self._notify(force=True)
                return
            if field == "min_step_target_yield_g":
                self.min_step_target_yield_g = float(value)
                await self._async_save_state()
                self._notify(force=True)
                return
            if field == "min_step_dose_g":
                self.min_step_dose_g = float(value)
                await self._async_save_state()
                self._notify(force=True)
                return
            if field == "min_step_temperature_offset_c":
                self.min_step_temperature_offset_c = float(value)
                await self._async_save_state()
                self._notify(force=True)
                return
            if field in _GRIND_BAND_CONTROLLER_FIELDS:
                setattr(self, field, float(value))
                await self._async_save_state()
                self._notify(force=True)
                return
        if source == "draft":
            if field in {"coffee", "roaster"}:
                setattr(self.draft, field, str(value).strip())
            elif field == "roast_level":
                self.draft.roast_level = str(value).strip()
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
