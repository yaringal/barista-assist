import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "barista_assist" / "switchbot_protocol.py"
spec = importlib.util.spec_from_file_location("switchbot_protocol", MODULE)
protocol = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules["switchbot_protocol"] = protocol
spec.loader.exec_module(protocol)


class SwitchBotProtocolTests(unittest.TestCase):
    def test_set_long_press_command(self):
        self.assertEqual(protocol.build_set_long_press_command(7), bytes([0x57, 0x0F, 0x08, 0x07]))

    def test_press_command(self):
        self.assertEqual(protocol.build_press_command(), bytes([0x57, 0x01, 0x00]))

    def test_zero_duration_is_instant_tap(self):
        self.assertEqual(protocol.build_set_long_press_command(0), bytes([0x57, 0x0F, 0x08, 0x00]))

    def test_bad_duration(self):
        with self.assertRaises(ValueError):
            protocol.build_set_long_press_command(-1)
        with self.assertRaises(ValueError):
            protocol.build_set_long_press_command(256)

    def test_response_status(self):
        self.assertEqual(protocol.response_status(bytes([1, 0])), 1)


if __name__ == "__main__":
    unittest.main()
