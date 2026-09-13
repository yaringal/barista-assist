"""Bag/recipe CRUD and the roast-level warm-start priors (docs/DESIGN.md
§19): selecting a slot, validating and updating a recipe field, creating a
new bag (from the API or the new-bag draft form). Mixed into BaristaRuntime
(see runtime.py) - not usable standalone."""

from __future__ import annotations

from datetime import date
import logging
from typing import Any

from homeassistant.exceptions import HomeAssistantError

from .flow_analysis import RoastLevelFlowBaseline, blend_toward_observed
from .storage import Bag

_LOGGER = logging.getLogger(__name__)


class RuntimeBagMixin:
    """Mixed into BaristaRuntime - see that class for the shared __init__/state."""

    async def async_select_slot(self, slot: str) -> None:
        if slot not in self.definitions.slots:
            raise HomeAssistantError(f"Unknown bean slot: {slot}")
        self.selected_slot = slot
        _LOGGER.debug("Selected bean slot: %s", slot)
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
        return numeric

    async def async_update_recipe_field(self, field: str, value: float | int) -> None:
        bag = self.selected_bag
        if bag is None:
            raise HomeAssistantError("No active bag in the selected slot")
        value = self._validate_recipe_field(field, value)
        _LOGGER.debug("Updating bag %s recipe field %s: %s -> %s", bag.id, field, getattr(bag, field), value)
        await self.hass.async_add_executor_job(
            self.db.update_recipe_field, bag.id, field, value
        )
        await self.async_refresh_cache()

    def _roast_level_blend_weight(self) -> float:
        return self.definitions.flow_analysis_constants["prior_weight_shots"]

    async def _async_roast_level_flow_baseline(
        self, roast_level: str | None, exclude_bag_id: str
    ) -> RoastLevelFlowBaseline | None:
        """Fetch+build the roast-level flow-rate reference blended_expected_
        flow_g_s/analyze_shot need - shared by async_brew and _async_finalize,
        the only two callers (docs/DESIGN.md's Phase 3b)."""
        features = await self.hass.async_add_executor_job(
            self.db.roast_level_baseline, roast_level, exclude_bag_id
        )
        if not features:
            return None
        return RoastLevelFlowBaseline(
            shot_count=features["shot_count"], median_flow_g_s=features["median_flow_g_s"]
        )

    def _roast_level_seeded_target_yield_g(
        self, dose_g: float, roast_level: Any, roast_level_features: dict[str, Any] | None
    ) -> float | None:
        """expert_rules.roast_level_ratio_prior's dose_g*ratio fallback,
        blended toward this installation's own accumulated ratio for the
        same roast_level (docs/DESIGN.md §19) when
        roast_level_features has any - same shared aggregate/shrinkage
        formula as blended_expected_flow_g_s. None when roast_level isn't a
        known key (not specified, or a value this prior doesn't cover)."""
        ratio = self.definitions.expert_rules["roast_level_ratio_prior"].get(roast_level)
        if ratio is None:
            return None
        if roast_level_features:
            ratio = blend_toward_observed(
                ratio,
                roast_level_features["median_ratio"],
                roast_level_features["shot_count"],
                self._roast_level_blend_weight(),
            )
        return dose_g * ratio

    def _roast_level_seeded_dose_g(
        self, roast_level: Any, roast_level_features: dict[str, Any] | None
    ) -> float | None:
        """expert_rules.roast_level_dose_prior's fallback, blended toward
        this installation's own accumulated dose for the same roast_level -
        same shape as _roast_level_seeded_target_yield_g above
        (docs/DESIGN.md §19). None when roast_level
        isn't a known key."""
        dose = self.definitions.expert_rules["roast_level_dose_prior"].get(roast_level)
        if dose is None:
            return None
        if roast_level_features:
            dose = blend_toward_observed(
                dose,
                roast_level_features["median_dose_g"],
                roast_level_features["shot_count"],
                self._roast_level_blend_weight(),
            )
        return dose

    def _roast_level_seeded_temperature_offset_c(self, roast_level: Any) -> int | None:
        """expert_rules.roast_level_temperature_prior's fallback - same
        shape and same "new bag, empty slot only" scope as
        _roast_level_seeded_target_yield_g, just for temperature_offset_c
        instead of target_yield_g. None when roast_level isn't a known key."""
        offset = self.definitions.expert_rules["roast_level_temperature_prior"].get(roast_level)
        return int(offset) if offset is not None else None

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

        # Fetched once, up front, rather than per-field: target_yield_g's,
        # dose_g's, and temperature_offset_c's seeds below all read the same
        # roast-level-keyed aggregate (docs/DESIGN.md's Phase 3b/§19 - one
        # shared aggregate, not independent lookups).
        # No bag to exclude yet - this bag doesn't exist until created below.
        roast_level_features = (
            await self.hass.async_add_executor_job(
                self.db.roast_level_baseline, data.get("roast_level"), None
            )
            if current is None
            else None
        )

        recipe: dict[str, Any] = {}
        for field in ("dose_g", "grind", "target_yield_g", "temperature_offset_c", "preinfusion_s"):
            seeded = None
            if field == "dose_g" and field not in data and current is None:
                # Same scope as target_yield_g below - see
                # roast_level_dose_prior's own comment in definitions.yaml.
                seeded = self._roast_level_seeded_dose_g(data.get("roast_level"), roast_level_features)
            elif field == "target_yield_g" and field not in data and current is None:
                # A genuinely new bag in an empty slot, nothing to inherit a
                # recipe from - see roast_level_ratio_prior's own comment in
                # definitions.yaml for why this is the only case it applies.
                seeded = self._roast_level_seeded_target_yield_g(
                    recipe["dose_g"], data.get("roast_level"), roast_level_features
                )
            elif field == "temperature_offset_c" and field not in data and current is None:
                # Same scope/reasoning as target_yield_g above, see
                # roast_level_temperature_prior's own comment in
                # definitions.yaml.
                seeded = self._roast_level_seeded_temperature_offset_c(data.get("roast_level"))
            value = seeded if seeded is not None else recipe_value(field)
            recipe[field] = self._validate_recipe_field(field, value)

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
                roast_level=data.get("roast_level") or None,
            )
        )
        _LOGGER.info(
            "Bag created: slot=%s coffee=%s dose=%sg grind=%s target_yield=%sg roast_level=%s",
            slot,
            bag.coffee_name,
            bag.dose_g,
            bag.grind,
            bag.target_yield_g,
            bag.roast_level,
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
                "roast_level": self.draft.roast_level,
                "starting_mass_g": self.draft.starting_mass_g,
            }
        )
        self.draft.roast_level = "medium"
        self.draft.coffee = ""
        self.draft.roaster = ""
        self.draft.roast_date = date.today()
        self._notify(force=True)
        return bag
