"""BOOKOO Themis Ultra protocol helpers.

Protocol source: BOOKOO OpenSource/bookoo_ultra_scale/protocols.md,
last updated 2026-08-12.
"""

from __future__ import annotations

from dataclasses import dataclass


class BookooProtocolError(ValueError):
    """Raised when a BOOKOO packet is malformed."""


@dataclass(frozen=True, slots=True)
class BookooReading:
    """Decoded scale reading."""

    scale_ms: int
    weight_g: float
    flow_g_s: float
    battery_percent: int
    standby_minutes: float
    buzzer_level: int
    flow_smoothing: bool


def xor_checksum(data: bytes | bytearray) -> int:
    """Return XOR checksum for bytes."""
    value = 0
    for byte in data:
        value ^= byte
    return value


def build_command(command: int, data2: int = 0, data3: int = 0) -> bytes:
    """Build a six-byte BOOKOO command packet."""
    if not all(0 <= value <= 0xFF for value in (command, data2, data3)):
        raise ValueError("BOOKOO command fields must fit in one byte")
    packet = bytearray((0x03, 0x0A, command, data2, data3))
    packet.append(xor_checksum(packet))
    return bytes(packet)


def _u24(data: bytes, offset: int) -> int:
    return (data[offset] << 16) | (data[offset + 1] << 8) | data[offset + 2]


_SIGN_POSITIVE = 0x2B  # ASCII '+'
_SIGN_NEGATIVE = 0x2D  # ASCII '-'


def _signed_magnitude(sign: int, magnitude: int) -> int:
    # BOOKOO's own protocol doc doesn't name the sign byte's values, but the
    # scale actually sends the ASCII sign character itself ('+'/'-'), not a
    # boolean flag - confirmed against BOOKOO's reference decoder
    # (github.com/makerwolf/aiobookoo/blob/main/aiobookoo/decode.py). Treating
    # any non-zero byte as negative (as this used to) misreads '+' (0x2B)
    # as negative too, since it's non-zero - inverting nearly every real
    # reading. Any other byte is treated as neutral/zero, matching that
    # reference implementation.
    if sign == _SIGN_NEGATIVE:
        return -magnitude
    if sign == _SIGN_POSITIVE:
        return magnitude
    return 0


def parse_weight_packet(data: bytes) -> BookooReading:
    """Decode a 20-byte Ultra weight packet (type 0x0B)."""
    if len(data) != 20:
        raise BookooProtocolError(f"Expected 20 bytes, received {len(data)}")
    if data[0] != 0x03 or data[1] != 0x0B:
        raise BookooProtocolError("Not a BOOKOO Ultra weight packet")
    if xor_checksum(data[:-1]) != data[-1]:
        raise BookooProtocolError("Invalid BOOKOO packet checksum")

    scale_ms = _u24(data, 2)
    weight_raw = _signed_magnitude(data[6], _u24(data, 7))
    flow_raw = _signed_magnitude(data[10], (data[11] << 8) | data[12])
    standby_raw = (data[14] << 8) | data[15]

    return BookooReading(
        scale_ms=scale_ms,
        weight_g=weight_raw / 100.0,
        flow_g_s=flow_raw / 100.0,
        battery_percent=max(0, min(100, data[13])),
        standby_minutes=standby_raw / 10.0,
        buzzer_level=data[16],
        flow_smoothing=bool(data[17]),
    )
