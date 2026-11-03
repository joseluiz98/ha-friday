"""Tests for the pure wire-protocol module."""

import json

import pytest

from custom_components.friday.protocol import (
    KNOWN_PLATFORMS,
    PROTOCOL_VERSION,
    ProtocolError,
    command,
    parse_hello,
    parse_message,
)


def _hello(capabilities):
    return {
        "type": "hello",
        "protocol": 1,
        "device": {"id": "sat-1", "kind": "satellite", "name": "Office"},
        "capabilities": capabilities,
        "state": {"volume": 5},
    }


def test_parse_hello_builds_descriptors():
    hello = parse_hello(
        _hello(
            [
                {"key": "volume", "platform": "number", "name": "Volume", "min": 1, "max": 10},
                {"key": "mute", "platform": "switch", "name": "Mute", "hidden": True},
            ]
        )
    )
    assert PROTOCOL_VERSION == 1
    assert hello.protocol == 1
    assert hello.device["id"] == "sat-1"
    assert hello.state == {"volume": 5}
    assert len(hello.capabilities) == 2
    assert hello.capabilities[0].key == "volume"
    assert hello.capabilities[0].max == 10
    assert hello.capabilities[0].available is True
    assert hello.capabilities[0].hidden is False
    assert hello.capabilities[1].hidden is True
    assert hello.skipped == []


def test_unknown_platform_is_skipped():
    hello = parse_hello(
        _hello(
            [
                {"key": "volume", "platform": "number", "name": "Volume"},
                {"key": "x", "platform": "text", "name": "X"},
            ]
        )
    )
    assert [c.key for c in hello.capabilities] == ["volume"]
    assert "x" in hello.skipped
    assert "text" not in KNOWN_PLATFORMS


def test_parse_message_rejects_missing_type():
    with pytest.raises(ProtocolError):
        parse_message(json.dumps({"changes": {}}))
    with pytest.raises(ProtocolError):
        parse_message("not json")
    with pytest.raises(ProtocolError):
        parse_message(json.dumps([1, 2]))


def test_parse_message_returns_dict():
    assert parse_message('{"type":"state","changes":{"a":1}}')["changes"] == {"a": 1}


def test_command_serializes_id_and_fields():
    assert json.loads(command(7, "set", key="volume", value=50)) == {
        "type": "command",
        "id": 7,
        "name": "set",
        "key": "volume",
        "value": 50,
    }
