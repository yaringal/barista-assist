"""Text entities built from definitions.yaml."""

from __future__ import annotations

from homeassistant.components.text import TextEntity, TextEntityDescription, TextMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .definitions import EntityDefinition, load_definitions
from .entity import BaristaAssistEntity


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities(
        BaristaText(entry.runtime_data, definition)
        for definition in load_definitions().platform("text")
    )


class BaristaText(BaristaAssistEntity, TextEntity):
    """Generic ephemeral text field."""

    def __init__(self, runtime, definition: EntityDefinition) -> None:
        BaristaAssistEntity.__init__(self, runtime, definition)
        self.entity_description = TextEntityDescription(
            key=definition.key, translation_key=definition.translation_key
        )
        self._attr_native_min = int(definition.minimum or 0)
        self._attr_native_max = int(definition.maximum or 255)
        self._attr_mode = TextMode(definition.mode or "text")

    @property
    def native_value(self) -> str:
        return str(self.runtime.entity_value(self.definition) or "")

    async def async_set_value(self, value: str) -> None:
        await self.runtime.async_set_entity_value(self.definition, value)
