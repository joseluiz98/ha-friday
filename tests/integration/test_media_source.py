import pytest
from unittest.mock import patch

from homeassistant.components.media_source import MediaSourceItem
from homeassistant.components.media_player import BrowseError
from homeassistant.components.media_source import async_browse_media, async_resolve_media
from homeassistant.setup import async_setup_component

from .test_connection import fast_backoff  # noqa: F401
from .test_recordings import RecAgent, device_id


async def test_browse_tree_and_resolve(hass):
    assert await async_setup_component(hass, "media_source", {})
    agent = RecAgent()
    entry, dev = await device_id(hass, agent)
    root = await async_browse_media(hass, "media-source://friday")
    assert [c.identifier for c in root.children] == [dev]
    node = await async_browse_media(hass, f"media-source://friday/{dev}")
    assert [c.title for c in node.children] == ["03/11/2026"]
    with patch("custom_components.friday.media_source.day_details", return_value={
            "sem_voz_20261103_094322.wav": {"word": "friday", "cause": "no_voice"}}):
        day = await async_browse_media(hass, f"media-source://friday/{dev}/20261103")
    assert day.children[0].title == "09:43:22 · friday · rejected (no voice)"
    assert day.children[1].title == "10:02:15 · conversation · friday"
    assert day.children[0].can_play
    play = await async_resolve_media(
        hass, f"media-source://friday/{dev}/20261103/20261103_100215.wav", None)
    assert play.url.startswith(f"/api/friday/recording/{dev}/20261103_100215.wav")
    assert play.mime_type == "audio/wav"
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


async def test_browse_offline(hass):
    assert await async_setup_component(hass, "media_source", {})
    agent = RecAgent()
    entry, dev = await device_id(hass, agent)
    await agent.stop()
    with pytest.raises(BrowseError):
        await async_browse_media(hass, f"media-source://friday/{dev}")
    await hass.config_entries.async_unload(entry.entry_id)
