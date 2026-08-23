from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_components" / "barista_assist" / "protocol.py"
spec = importlib.util.spec_from_file_location("barista_protocol", MODULE)
protocol = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules["barista_protocol"] = protocol
spec.loader.exec_module(protocol)


class ProtocolTests(unittest.TestCase):
    def make_packet(self) -> bytes:
        # 12,345 ms; 38.42 g; 2.16 g/s; 87% battery; 10.0 min standby.
        payload = bytearray(
            [
                0x03, 0x0B,
                0x00, 0x30, 0x39,
                0x01,
                0x00,
                0x00, 0x0F, 0x02,
                0x00,
                0x00, 0xD8,
                87,
                0x00, 0x64,
                2,
                0,
                0,
            ]
        )
        payload.append(protocol.xor_checksum(payload))
        return bytes(payload)

    def test_command_checksum(self) -> None:
        cmd = protocol.build_command(0x07)
        self.assertEqual(cmd[:5], bytes([0x03, 0x0A, 0x07, 0, 0]))
        self.assertEqual(cmd[-1], protocol.xor_checksum(cmd[:-1]))

    def test_parse_weight_packet(self) -> None:
        reading = protocol.parse_weight_packet(self.make_packet())
        self.assertEqual(reading.scale_ms, 12345)
        self.assertAlmostEqual(reading.weight_g, 38.42)
        self.assertAlmostEqual(reading.flow_g_s, 2.16)
        self.assertEqual(reading.battery_percent, 87)
        self.assertAlmostEqual(reading.standby_minutes, 10.0)
        self.assertFalse(reading.flow_smoothing)

    def test_negative_weight_and_flow(self) -> None:
        packet = bytearray(self.make_packet())
        packet[6] = 1
        packet[10] = 1
        packet[-1] = protocol.xor_checksum(packet[:-1])
        reading = protocol.parse_weight_packet(bytes(packet))
        self.assertAlmostEqual(reading.weight_g, -38.42)
        self.assertAlmostEqual(reading.flow_g_s, -2.16)

    def test_bad_checksum_rejected(self) -> None:
        packet = bytearray(self.make_packet())
        packet[7] ^= 1
        with self.assertRaises(protocol.BookooProtocolError):
            protocol.parse_weight_packet(bytes(packet))


if __name__ == "__main__":
    unittest.main()
