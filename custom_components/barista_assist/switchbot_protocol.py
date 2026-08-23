"""Pure SwitchBot Bot BLE protocol helpers."""

from __future__ import annotations

BOT_MAGIC = 0x57
COMMAND_EXECUTE_ACTION = 0x01
COMMAND_EXTENDED = 0x0F
COMMAND_EXT_SET_LONG_PRESS = 0x08
ACTION_PUSH_PULL = 0x00
STATUS_OK = 0x01
STATUS_ERROR = 0x02
STATUS_BUSY = 0x03
STATUS_UNSUPPORTED = 0x05
STATUS_LOW_BATTERY = 0x06
STATUS_ENCRYPTED = 0x07
STATUS_UNENCRYPTED = 0x08
STATUS_PASSWORD_ERROR = 0x09


class SwitchBotProtocolError(ValueError):
    """Raised for malformed or unsuccessful SwitchBot Bot protocol messages."""


def build_press_command() -> bytes:
    """Build the standard Bot push-and-pull action command."""
    return bytes([BOT_MAGIC, COMMAND_EXECUTE_ACTION, ACTION_PUSH_PULL])


def build_set_long_press_command(seconds: int) -> bytes:
    """Build the extended command that changes the Bot's stored long-press duration.

    0 means no hold at all (an instant tap); this is a real device value, not
    just an internal default, so it must stay allowed here.
    """
    if not 0 <= seconds <= 255:
        raise ValueError("SwitchBot long-press duration must be 0-255 seconds")
    return bytes([BOT_MAGIC, COMMAND_EXTENDED, COMMAND_EXT_SET_LONG_PRESS, seconds])


def response_status(data: bytes) -> int:
    """Return the mandatory response status byte."""
    if not data:
        raise SwitchBotProtocolError("Empty SwitchBot response")
    return data[0]


def describe_status(status: int) -> str:
    """Convert a SwitchBot status byte into a useful error message."""
    return {
        STATUS_OK: "ok",
        STATUS_ERROR: "device reported action error",
        STATUS_BUSY: "device is busy",
        STATUS_UNSUPPORTED: "device does not support the command",
        STATUS_LOW_BATTERY: "device battery is too low",
        STATUS_ENCRYPTED: "device requires encrypted protocol access",
        STATUS_UNENCRYPTED: "device reports unencrypted protocol mode",
        STATUS_PASSWORD_ERROR: "device password rejected the command",
    }.get(status, f"device returned status 0x{status:02x}")
