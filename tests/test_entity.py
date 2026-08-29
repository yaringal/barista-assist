"""Regression test: reported against a real install - Home Assistant logged
'calls async_write_ha_state from a thread other than the event loop'
thousands of times, with every occurrence blaming entity.py's
_handle_runtime_update. The dispatcher-connected handler was a plain,
undecorated method; Home Assistant's job scheduler treats an undecorated
callable as possibly-blocking and runs it in the executor thread pool
instead of inline on the event loop, which is exactly what made every
async_write_ha_state() call inside it violate HA's own thread-safety
contract - regardless of which thread actually triggered the dispatch.
"""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ha_stubs import import_barista_module  # noqa: E402

entity = import_barista_module("entity")


class DispatcherCallbackTests(unittest.TestCase):
    def test_handle_runtime_update_is_marked_as_a_hass_callback(self):
        handler = entity.BaristaAssistEntity._handle_runtime_update
        self.assertTrue(getattr(handler, "_hass_callback", False))


if __name__ == "__main__":
    unittest.main()
