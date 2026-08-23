"""Date entities built from definitions.yaml."""

from __future__ import annotations

from datetime import date

from homeassistant.components.date import DateEntity, DateEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .definitions import EntityDefinition, load_definitions
from .entity import BaristaAssistEntity


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities(
        BaristaDate(entry.runtime_data, definition)
        for definition in load_definitions().platform("date")
    )


class BaristaDate(BaristaAssistEntity, DateEntity):
    """Generic ephemeral date field."""

    def __init__(self, runtime, definition: EntityDefinition) -> None:
        BaristaAssistEntity.__init__(self, runtime, definition)
        self.entity_description = DateEntityDescription(
            key=definition.key, translation_key=definition.translation_key
        )

    @property
    def native_value(self) -> date | None:
        return self.runtime.entity_value(self.definition)

    async def async_set_value(self, value: date) -> None:
        await self.runtime.async_set_entity_value(self.definition, value)
