"""Minimal fake `homeassistant` / `bleak_retry_connector` modules.

These exist purely so `runtime.py` (and the sibling modules it imports:
bookoo.py, switchbot.py) can be imported and exercised without a real Home
Assistant install or a real Bluetooth stack. Only the names those modules
actually reference at import or call time are provided; nothing else is
implemented, since real HA/BLE behavior is out of scope for these tests.
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "custom_components" / "barista_assist"


def _new_module(name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    sys.modules[name] = module
    return module


def install() -> None:
    """Idempotently register the fake homeassistant/bleak_retry_connector modules."""
    if "homeassistant" in sys.modules:
        return

    ha = _new_module("homeassistant")
    ha.__path__ = []

    config_entries = _new_module("homeassistant.config_entries")

    class ConfigEntry:  # only ever used as a type hint (module has `from __future__ import annotations`)
        pass

    config_entries.ConfigEntry = ConfigEntry

    const = _new_module("homeassistant.const")
    const.ATTR_ENTITY_ID = "entity_id"
    const.SERVICE_TURN_ON = "turn_on"

    core = _new_module("homeassistant.core")

    class HomeAssistant:  # only ever used as a type hint
        pass

    core.HomeAssistant = HomeAssistant

    exceptions = _new_module("homeassistant.exceptions")

    class HomeAssistantError(Exception):
        pass

    exceptions.HomeAssistantError = HomeAssistantError

    helpers = _new_module("homeassistant.helpers")
    helpers.__path__ = []

    dispatcher = _new_module("homeassistant.helpers.dispatcher")

    def async_dispatcher_send(hass, signal, *args) -> None:
        pass

    dispatcher.async_dispatcher_send = async_dispatcher_send

    storage = _new_module("homeassistant.helpers.storage")

    class Store:
        """In-memory stand-in for homeassistant.helpers.storage.Store."""

        def __init__(self, hass, version, key) -> None:
            self.hass = hass
            self.version = version
            self.key = key
            self._data = None

        def __class_getitem__(cls, item):
            return cls

        async def async_load(self):
            return self._data

        async def async_save(self, data) -> None:
            self._data = data

    storage.Store = Store

    device_registry = _new_module("homeassistant.helpers.device_registry")
    device_registry.CONNECTION_BLUETOOTH = "bluetooth"
    device_registry.async_get = lambda hass: None

    entity_registry = _new_module("homeassistant.helpers.entity_registry")
    entity_registry.async_get = lambda hass: None

    components = _new_module("homeassistant.components")
    components.__path__ = []

    bluetooth = _new_module("homeassistant.components.bluetooth")
    bluetooth.async_ble_device_from_address = lambda hass, address, connectable=True: None

    bleak_retry_connector = _new_module("bleak_retry_connector")

    class BleakClientWithServiceCache:
        pass

    async def establish_connection(*args, **kwargs):
        raise RuntimeError(
            "establish_connection should never be called in tests; "
            "patch BookooUltraClient/SwitchBotBotConfigurator instead"
        )

    bleak_retry_connector.BleakClientWithServiceCache = BleakClientWithServiceCache
    bleak_retry_connector.establish_connection = establish_connection


def _stub_package(name: str, path: Path) -> types.ModuleType:
    """Register a bare package in sys.modules with a real __path__, without
    executing its (real, possibly HA-import-heavy) __init__.py.
    """
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    module = types.ModuleType(name)
    module.__path__ = [str(path)]
    module.__package__ = name
    sys.modules[name] = module
    return module


def import_runtime_module() -> types.ModuleType:
    """Import custom_components.barista_assist.runtime.

    Deliberately skips executing custom_components/barista_assist/__init__.py
    (which pulls in services.py/websocket.py and a much larger HA surface:
    frontend, http, etc.) since this test suite only needs runtime.py and the
    modules it imports directly.
    """
    install()

    cached = sys.modules.get("custom_components.barista_assist.runtime")
    if cached is not None:
        return cached

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    custom_components = _stub_package("custom_components", REPO_ROOT / "custom_components")
    barista_assist = _stub_package("custom_components.barista_assist", PACKAGE_DIR)
    custom_components.barista_assist = barista_assist

    return importlib.import_module("custom_components.barista_assist.runtime")
