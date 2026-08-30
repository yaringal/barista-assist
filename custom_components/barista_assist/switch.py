"""Switch entities built from definitions.yaml."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .definitions import EntityDefinition, load_definitions
from .entity import BaristaAssistEntity


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities(
        BaristaSwitch(entry.runtime_data, definition)
        for definition in load_definitions().platform("switch")
    )


class BaristaSwitch(BaristaAssistEntity, SwitchEntity):
    """Generic YAML-mapped boolean toggle."""

    def __init__(self, runtime, definition: EntityDefinition) -> None:
        BaristaAssistEntity.__init__(self, runtime, definition)
        self.entity_description = SwitchEntityDescription(
            key=definition.key, translation_key=definition.translation_key
        )

    @property
    def is_on(self) -> bool:
        return bool(self.runtime.entity_value(self.definition))

    async def async_turn_on(self, **kwargs) -> None:
        await self.runtime.async_set_entity_value(self.definition, True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.runtime.async_set_entity_value(self.definition, False)
