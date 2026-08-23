"""Config flow for Barista Assist."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_ble_device_from_address,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector

from .const import (
    BOOKOO_SERVICE_UUID,
    CONF_BREW_ENTITY,
    CONF_MACHINE_LIMIT_CONFIRMED,
    CONF_MACHINE_MAX_SHOT_SECONDS,
    CONF_SAFETY_MARGIN_SECONDS,
    CONF_SCALE_ADDRESS,
    DOMAIN,
)
from .definitions import load_definitions
from .switchbot import resolve_bluetooth_address


def _is_ultra(info: BluetoothServiceInfoBleak) -> bool:
    return BOOKOO_SERVICE_UUID in {uuid.lower() for uuid in info.service_uuids}


def _brew_selector() -> selector.EntitySelector:
    return selector.EntitySelector(
        selector.EntitySelectorConfig(
            filter={"integration": "switchbot", "domain": "switch"}
        )
    )


def _machine_limit_selector() -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(min=20, max=120, step=1, mode="box")
    )


def _margin_selector() -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(min=1, max=10, step=0.5, mode="box")
    )


def _confirmation_selector() -> selector.BooleanSelector:
    return selector.BooleanSelector()


def _settings_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    brew_key = (
        vol.Required(CONF_BREW_ENTITY, default=defaults[CONF_BREW_ENTITY])
        if defaults.get(CONF_BREW_ENTITY)
        else vol.Required(CONF_BREW_ENTITY)
    )
    machine_limit = defaults.get(CONF_MACHINE_MAX_SHOT_SECONDS)
    margin = defaults.get(
        CONF_SAFETY_MARGIN_SECONDS,
        load_definitions().defaults["controller"]["safety_margin_s"],
    )
    confirmation = defaults.get(CONF_MACHINE_LIMIT_CONFIRMED, False)
    machine_key = (
        vol.Required(CONF_MACHINE_MAX_SHOT_SECONDS, default=machine_limit)
        if machine_limit is not None
        else vol.Required(CONF_MACHINE_MAX_SHOT_SECONDS)
    )
    return vol.Schema(
        {
            brew_key: _brew_selector(),
            machine_key: _machine_limit_selector(),
            vol.Required(CONF_SAFETY_MARGIN_SECONDS, default=margin): _margin_selector(),
            vol.Required(CONF_MACHINE_LIMIT_CONFIRMED, default=confirmation): _confirmation_selector(),
        }
    )


def _validate_machine_settings(
    user_input: dict[str, Any], errors: dict[str, str]
) -> None:
    machine_limit = float(user_input[CONF_MACHINE_MAX_SHOT_SECONDS])
    margin = float(user_input[CONF_SAFETY_MARGIN_SECONDS])
    if machine_limit - margin <= 5:
        errors[CONF_MACHINE_MAX_SHOT_SECONDS] = "machine_limit_too_short"
    if not user_input.get(CONF_MACHINE_LIMIT_CONFIRMED, False):
        errors[CONF_MACHINE_LIMIT_CONFIRMED] = "machine_limit_must_be_confirmed"


class BaristaAssistConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure one Barista Assist installation."""

    VERSION = 1
    MINOR_VERSION = 3

    @staticmethod
    async def async_migrate_entry(hass: HomeAssistant, config_entry) -> bool:
        """Migrate v0.2.0 timeout settings to the physical-machine safety model."""
        if config_entry.version == 1 and config_entry.minor_version < 3:
            options = dict(config_entry.options)
            legacy_key = "max_shot_seconds"
            if CONF_MACHINE_MAX_SHOT_SECONDS not in options and legacy_key in options:
                options[CONF_MACHINE_MAX_SHOT_SECONDS] = options[legacy_key]
            options.pop(legacy_key, None)
            options.setdefault(
                CONF_SAFETY_MARGIN_SECONDS,
                load_definitions().defaults["controller"]["safety_margin_s"],
            )
            # Force explicit confirmation of the physical Barista Express setting.
            options[CONF_MACHINE_LIMIT_CONFIRMED] = False
            hass.config_entries.async_update_entry(config_entry, options=options)
        return True

    def __init__(self) -> None:
        self._discovery: BluetoothServiceInfoBleak | None = None
        self._devices: dict[str, BluetoothServiceInfoBleak] = {}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        if not _is_ultra(discovery_info) or not discovery_info.connectable:
            return self.async_abort(reason="not_supported")
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovery = discovery_info
        self.context["title_placeholders"] = {"name": discovery_info.name}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._discovery is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            _validate_machine_settings(user_input, errors)
            if not errors and self.hass.states.get(user_input[CONF_BREW_ENTITY]) is None:
                errors[CONF_BREW_ENTITY] = "entity_not_found"
            if (
                not errors
                and resolve_bluetooth_address(self.hass, user_input[CONF_BREW_ENTITY]) is None
            ):
                errors[CONF_BREW_ENTITY] = "brew_address_not_found"
            if not errors:
                return self.async_create_entry(
                    title=f"Barista Assist — {self._discovery.name}",
                    data={CONF_SCALE_ADDRESS: self._discovery.address},
                    options=dict(user_input),
                )
        return self.async_show_form(
            step_id="bluetooth_confirm",
            data_schema=_settings_schema(user_input),
            errors=errors,
            description_placeholders={"name": self._discovery.name},
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        self._devices = {
            info.address: info
            for info in async_discovered_service_info(self.hass, connectable=True)
            if _is_ultra(info)
        }
        if not self._devices:
            return self.async_abort(reason="no_devices_found")

        errors: dict[str, str] = {}
        if user_input is not None:
            _validate_machine_settings(user_input, errors)
            address = user_input[CONF_SCALE_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            if not errors and async_ble_device_from_address(self.hass, address, connectable=True) is None:
                errors[CONF_SCALE_ADDRESS] = "cannot_connect"
            if not errors and self.hass.states.get(user_input[CONF_BREW_ENTITY]) is None:
                errors[CONF_BREW_ENTITY] = "entity_not_found"
            if (
                not errors
                and resolve_bluetooth_address(self.hass, user_input[CONF_BREW_ENTITY]) is None
            ):
                errors[CONF_BREW_ENTITY] = "brew_address_not_found"
            if not errors:
                settings = dict(user_input)
                settings.pop(CONF_SCALE_ADDRESS, None)
                return self.async_create_entry(
                    title=f"Barista Assist — {self._devices[address].name}",
                    data={CONF_SCALE_ADDRESS: address},
                    options=settings,
                )

        devices = {
            address: f"{info.name} ({address})" for address, info in self._devices.items()
        }
        schema = _settings_schema(user_input).extend(
            {vol.Required(CONF_SCALE_ADDRESS): vol.In(devices)}
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return BaristaAssistOptionsFlow()


class BaristaAssistOptionsFlow(OptionsFlow):
    """Edit actuator and machine-safety settings."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            _validate_machine_settings(user_input, errors)
            if not errors:
                return self.async_create_entry(title="", data=user_input)
        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init", data_schema=_settings_schema(current), errors=errors
        )
