"""Pure (no Home Assistant imports) wire protocol v1 helpers.

See docs/protocol.md for the message reference.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
import json
from typing import Any

PROTOCOL_VERSION = 1

KNOWN_PLATFORMS = frozenset(
    {"sensor", "binary_sensor", "number", "switch", "select", "button", "event"}
)


class ProtocolError(ValueError):
    """Raised when a message does not follow the protocol."""


@dataclass
class Descriptor:
    """A capability announced by an agent; absent fields mean the HA default."""

    key: str
    platform: str
    name: str
    category: str | None = None
    icon: str | None = None
    unit: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    min: float | None = None
    max: float | None = None
    step: float | None = None
    mode: str | None = None
    options: list[str] | None = None
    event_types: list[str] | None = None
    available: bool = True
    hidden: bool = False


@dataclass
class Hello:
    """First message an agent sends after connecting."""

    protocol: int
    device: dict[str, Any]
    capabilities: list[Descriptor]
    state: dict[str, Any]
    skipped: list[str] = field(default_factory=list)


_DESCRIPTOR_FIELDS = {f.name for f in fields(Descriptor)}


def parse_message(raw: str) -> dict[str, Any]:
    """Decode a text frame and check it is an object with a string `type`."""
    try:
        msg = json.loads(raw)
    except (TypeError, ValueError) as err:
        raise ProtocolError(f"invalid JSON: {err}") from err
    if not isinstance(msg, dict):
        raise ProtocolError("message must be a JSON object")
    if not isinstance(msg.get("type"), str) or not msg["type"]:
        raise ProtocolError("message has no 'type'")
    return msg


def parse_descriptor(raw: dict[str, Any]) -> Descriptor:
    """Build a Descriptor, ignoring unknown fields (forward compatibility)."""
    if not isinstance(raw, dict):
        raise ProtocolError("descriptor must be an object")
    for required in ("key", "platform"):
        if not isinstance(raw.get(required), str) or not raw[required]:
            raise ProtocolError(f"descriptor has no '{required}'")
    data = {k: v for k, v in raw.items() if k in _DESCRIPTOR_FIELDS}
    data.setdefault("name", raw["key"])
    return Descriptor(**data)


def parse_capabilities(
    raw_list: Any,
) -> tuple[list[Descriptor], list[str]]:
    """Split raw descriptors into known ones and the keys of skipped ones."""
    if not isinstance(raw_list, list):
        raise ProtocolError("'capabilities' must be a list")
    capabilities: list[Descriptor] = []
    skipped: list[str] = []
    for raw in raw_list:
        descriptor = parse_descriptor(raw)
        if descriptor.platform in KNOWN_PLATFORMS:
            capabilities.append(descriptor)
        else:
            skipped.append(descriptor.key)
    return capabilities, skipped


def parse_hello(msg: dict[str, Any]) -> Hello:
    """Validate a `hello` message and build a Hello."""
    if msg.get("type") != "hello":
        raise ProtocolError("expected a 'hello' message")
    protocol = msg.get("protocol")
    if not isinstance(protocol, int):
        raise ProtocolError("hello has no integer 'protocol'")
    device = msg.get("device")
    if not isinstance(device, dict) or not device.get("id"):
        raise ProtocolError("hello has no device with an 'id'")
    state = msg.get("state", {})
    if not isinstance(state, dict):
        raise ProtocolError("'state' must be an object")
    capabilities, skipped = parse_capabilities(msg.get("capabilities", []))
    return Hello(
        protocol=protocol,
        device=device,
        capabilities=capabilities,
        state=state,
        skipped=skipped,
    )


def command(id: int, name: str, **fields_: Any) -> str:  # noqa: A002
    """Serialize a HA -> agent command frame."""
    return json.dumps(
        {"type": "command", "id": id, "name": name, **fields_},
        separators=(",", ":"),
    )
