"""Updates from descriptors: the OTA entities of the satellite and the server."""

from __future__ import annotations

from typing import Any

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.exceptions import HomeAssistantError

from .entity import FridayEntity, setup_platform

SUMMARY_MAX = 255  # HA's limit for release_summary; async_release_notes has the full text


async def async_setup_entry(hass, entry, async_add_entities):
    setup_platform(hass, entry, async_add_entities, "update", FridayUpdate)


class FridayUpdate(FridayEntity, UpdateEntity):
    """State is `{installed, latest, in_progress, title, release_url, summary}` under the descriptor key."""

    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS | UpdateEntityFeature.RELEASE_NOTES
    )

    def _field(self, name: str) -> Any:
        value = self._value
        return value.get(name) if isinstance(value, dict) else None

    @property
    def installed_version(self) -> str | None:
        return self._field("installed")

    @property
    def latest_version(self) -> str | None:
        return self._field("latest")

    @property
    def in_progress(self) -> bool:
        return bool(self._field("in_progress"))

    @property
    def title(self) -> str | None:
        return self._field("title")

    @property
    def release_url(self) -> str | None:
        return self._field("release_url")

    @property
    def release_summary(self) -> str | None:
        summary = self._field("summary")
        return summary[:SUMMARY_MAX] if isinstance(summary, str) else None

    async def async_release_notes(self) -> str | None:
        summary = self._field("summary")
        return summary if isinstance(summary, str) else None

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        if version is not None and version != self.latest_version:
            raise HomeAssistantError("only_latest")
        await self._connection.send("ota_install", key=self.descriptor.key)
