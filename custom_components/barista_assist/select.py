"""Select entities built from definitions.yaml."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .definitions import EntityDefinition, load_definitions
from .entity import BaristaAssistEntity


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities(
        BaristaSelect(entry.runtime_data, definition)
        for definition in load_definitions().platform("select")
    )


class BaristaSelect(BaristaAssistEntity, SelectEntity):
    """Generic YAML-mapped select."""

    def __init__(self, runtime, definition: EntityDefinition) -> None:
        BaristaAssistEntity.__init__(self, runtime, definition)
        self.entity_description = SelectEntityDescription(
            key=definition.key, translation_key=definition.translation_key
        )
        self._attr_options = definition.option_labels()

    @property
    def current_option(self) -> str | None:
        value = self.runtime.entity_value(self.definition)
        return self.definition.option_label(value) if value is not None else None

    async def async_select_option(self, option: str) -> None:
        await self.runtime.async_set_entity_value(
            self.definition, self.definition.option_value(option)
        )
