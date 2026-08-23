"""Button entities built from definitions.yaml."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .definitions import EntityDefinition, load_definitions
from .entity import BaristaAssistEntity


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities(
        BaristaButton(entry.runtime_data, definition)
        for definition in load_definitions().platform("button")
    )


class BaristaButton(BaristaAssistEntity, ButtonEntity):
    """Generic runtime action button."""

    def __init__(self, runtime, definition: EntityDefinition) -> None:
        BaristaAssistEntity.__init__(self, runtime, definition)
        self.entity_description = ButtonEntityDescription(
            key=definition.key, translation_key=definition.translation_key
        )

    async def async_press(self) -> None:
        assert self.definition.action is not None
        await self.runtime.async_run_action(self.definition.action)
