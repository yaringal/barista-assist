"""Sensor entities built from definitions.yaml."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .definitions import EntityDefinition, load_definitions
from .entity import BaristaAssistEntity


def _description(definition: EntityDefinition) -> SensorEntityDescription:
    return SensorEntityDescription(
        key=definition.key,
        translation_key=definition.translation_key,
        native_unit_of_measurement=definition.unit,
        device_class=(
            SensorDeviceClass(definition.device_class)
            if definition.device_class
            else None
        ),
        state_class=(
            SensorStateClass(definition.state_class)
            if definition.state_class
            else None
        ),
        suggested_display_precision=definition.precision,
    )


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities(
        BaristaSensor(entry.runtime_data, definition)
        for definition in load_definitions().platform("sensor")
    )


class BaristaSensor(BaristaAssistEntity, SensorEntity):
    """Generic runtime-backed sensor."""

    def __init__(self, runtime, definition: EntityDefinition) -> None:
        BaristaAssistEntity.__init__(self, runtime, definition)
        self.entity_description = _description(definition)

    @property
    def native_value(self):
        return self.runtime.entity_value(self.definition)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        attributes = self.runtime.entity_attributes(self.definition)
        return attributes or None
