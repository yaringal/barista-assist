"""Number entities built from definitions.yaml."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .definitions import EntityDefinition, load_definitions
from .entity import BaristaAssistEntity


def _description(definition: EntityDefinition) -> NumberEntityDescription:
    return NumberEntityDescription(
        key=definition.key,
        translation_key=definition.translation_key,
        native_min_value=definition.minimum,
        native_max_value=definition.maximum,
        native_step=definition.step,
        native_unit_of_measurement=definition.unit,
        mode=NumberMode(definition.mode or "box"),
    )


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities(
        BaristaNumber(entry.runtime_data, definition)
        for definition in load_definitions().platform("number")
    )


class BaristaNumber(BaristaAssistEntity, NumberEntity):
    """Generic editable numeric value."""

    def __init__(self, runtime, definition: EntityDefinition) -> None:
        BaristaAssistEntity.__init__(self, runtime, definition)
        self.entity_description = _description(definition)

    @property
    def native_value(self) -> float | None:
        value = self.runtime.entity_value(self.definition)
        return float(value) if value is not None else None

    async def async_set_native_value(self, value: float) -> None:
        await self.runtime.async_set_entity_value(self.definition, value)
