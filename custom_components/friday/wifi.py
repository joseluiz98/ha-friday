"""Change a satellite's Wi-Fi through its agent. The password is passed through, never stored or logged."""

from __future__ import annotations

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

WIFI_COMMAND = "wifi_set"
WIFI_TIMEOUT_S = 130  # the agent waits up to 60 s for the new network, then rolls back


def supports_wifi(connection) -> bool:
    hello = connection.hello
    return bool(connection.connected and hello is not None
                and any(d.key == WIFI_COMMAND for d in hello.capabilities))


async def async_set_wifi(connection, ssid: str, password: str) -> None:
    """Send `wifi_set`; raises HomeAssistantError with a safe message (never the password)."""
    if not supports_wifi(connection):
        raise ServiceValidationError("This device cannot change its Wi-Fi (agent offline or no NetworkManager).")
    try:
        await connection.send(WIFI_COMMAND, timeout=WIFI_TIMEOUT_S, args={"ssid": ssid, "password": password})
    except HomeAssistantError as err:
        reason = str(err)
        if reason == "rollback":
            raise HomeAssistantError(
                "The new Wi-Fi network did not work; the previous network was restored.") from None
        raise HomeAssistantError(f"Wi-Fi change failed: {reason}") from None
