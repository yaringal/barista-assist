"""Shared entity base for declaratively defined Barista Assist entities."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, NAME, SIGNAL_UPDATE, integration_version
from .definitions import EntityDefinition


class BaristaAssistEntity(Entity):
    """Base entity backed by the integration runtime."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, runtime, definition: EntityDefinition) -> None:
        self.runtime = runtime
        self.definition = definition
        self._attr_unique_id = f"{runtime.entry.entry_id}_{definition.key}"
        version = integration_version()
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.entry.entry_id)},
            name=NAME,
            manufacturer="Barista Assist",
            model="Smart espresso workflow",
            sw_version=version,
        )

    @property
    def available(self) -> bool:
        return self.runtime.entity_available(self.definition)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_UPDATE, self._handle_runtime_update
            )
        )

    @callback
    def _handle_runtime_update(self) -> None:
        """Dispatcher-connected callbacks must be marked @callback or Home
        Assistant assumes they might block and runs them in the executor
        thread pool instead of inline on the event loop - which is exactly
        what was causing every async_write_ha_state() call here to violate
        HA's own thread-safety contract, regardless of which thread
        originally triggered the dispatch."""
        self.async_write_ha_state()
