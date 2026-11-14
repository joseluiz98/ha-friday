"""Admin-only download of the provisioning images the server publishes."""

import os

import pytest
from homeassistant.setup import async_setup_component

from custom_components.friday import image_view
from custom_components.friday.const import DOMAIN

from .test_connection import fast_backoff  # noqa: F401

BODY = b"xz-bytes" * 10


@pytest.fixture
def image_dir(hass, tmp_path):
    folder = tmp_path / "imagens"
    folder.mkdir()
    for tag, sha in (("v0.9.0", "a" * 64), ("v0.10.0", "b" * 64), ("v0.2.1", None)):
        (folder / f"friday-satellite-{tag}.img.xz").write_bytes(BODY)
        if sha:
            (folder / f"friday-satellite-{tag}.img.xz.sha256").write_text(f"{sha}  friday-satellite-{tag}.img.xz\n")
        (folder / f"friday-satellite-{tag}.json").write_text("{}")
    (folder / "notes.txt").write_text("x")
    hass.data.setdefault(DOMAIN, {})["image_dir"] = str(folder)
    return folder


@pytest.fixture
async def view_ready(hass, image_dir):
    assert await async_setup_component(hass, DOMAIN, {})  # async_setup registers the views


async def test_admin_downloads_image(hass, hass_client, view_ready):
    client = await hass_client()
    r = await client.get("/api/friday/image/friday-satellite-v0.9.0.img.xz")
    assert r.status == 200
    assert await r.read() == BODY
    r = await client.get("/api/friday/image/friday-satellite-v0.9.0.img.xz.sha256")
    assert r.status == 200
    assert (await r.text()).startswith("a" * 64)


async def test_non_admin_forbidden(hass, hass_client, hass_admin_user, view_ready):
    hass_admin_user.groups = []  # no longer an administrator
    client = await hass_client()
    r = await client.get("/api/friday/image/friday-satellite-v0.9.0.img.xz")
    assert r.status == 403
    assert (await client.get("/api/friday/image")).status == 403


async def test_unauthenticated_rejected(hass, hass_client_no_auth, view_ready):
    client = await hass_client_no_auth()
    assert (await client.get("/api/friday/image/friday-satellite-v0.9.0.img.xz")).status == 401


@pytest.mark.parametrize("name", [
    "..%2Fx", "friday-satellite-v1.img.xz", "foo.img.xz", "friday-satellite-v0.9.0.json",
    "friday-satellite-v0.9.0.img.xz.sha256x", "friday-satellite-v9.9.9.img.xz"])
async def test_traversal_and_unknown_names_rejected(hass, hass_client, view_ready, name):
    client = await hass_client()
    r = await client.get(f"/api/friday/image/{name}")
    assert r.status in (400, 404)  # HA core's security filter already answers 400 to encoded traversal
    if not name.startswith(".."):
        assert r.status == 404


def test_valid_name():
    assert image_view.valid_name("friday-satellite-v0.10.2.img.xz")
    assert image_view.valid_name("friday-satellite-v0.10.2.img.xz.sha256")
    for bad in ("../friday-satellite-v0.1.0.img.xz", "friday-satellite-v1.img.xz", "friday-satellite-v0.1.0.img.xz\n",
                "x/friday-satellite-v0.1.0.img.xz", "friday-satellite-v0.1.0.json"):
        assert not image_view.valid_name(bad)


def test_list_images_newest_first_with_sha(image_dir):
    items = image_view.list_images(str(image_dir))
    assert [i["tag"] for i in items] == ["v0.10.0", "v0.9.0", "v0.2.1"]  # numeric, not textual
    assert items[0]["file"] == "friday-satellite-v0.10.0.img.xz"
    assert items[0]["sha256"] == "b" * 64 and items[0]["size"] == len(BODY)
    assert items[2]["sha256"] is None
    assert isinstance(items[0]["mtime"], float)


def test_list_images_missing_folder(tmp_path):
    assert image_view.list_images(str(tmp_path / "nope")) == []


def test_download_url():
    files = ["friday-satellite-v0.9.0.img.xz.sha256", "friday-satellite-v0.9.0.json", "friday-satellite-v0.9.0.img.xz"]
    assert image_view.download_url(files) == "/api/friday/image/friday-satellite-v0.9.0.img.xz"
    assert image_view.download_url([]) is None and image_view.download_url(None) is None


async def test_list_view_returns_images(hass, hass_client, view_ready):
    client = await hass_client()
    r = await client.get("/api/friday/image")
    assert r.status == 200
    data = await r.json()
    assert [i["tag"] for i in data["images"]] == ["v0.10.0", "v0.9.0", "v0.2.1"]


async def test_image_latest_sensor_gets_download_url(hass):
    from homeassistant.helpers import entity_registry as er
    from .test_connection import FakeAgent, wait_for
    from .test_setup import hello, setup

    caps = [{"key": "image_latest", "platform": "sensor", "name": "Provisioning image"}]
    state = {"image_latest": "v0.9.0", "image_latest_attributes": {
        "built_at": "2026-11-12T10:00:00+00:00", "status": "ok", "error": "",
        "files": ["friday-satellite-v0.9.0.img.xz", "friday-satellite-v0.9.0.img.xz.sha256"]}}
    agent = FakeAgent(hello(caps, state))
    entry = await setup(hass, agent)
    await wait_for(lambda: hass.states.get("sensor.friday_dev1_image_latest") is not None)
    st = hass.states.get("sensor.friday_dev1_image_latest")
    assert st.state == "v0.9.0"
    assert st.attributes["download_url"] == "/api/friday/image/friday-satellite-v0.9.0.img.xz"
    assert st.attributes["status"] == "ok"
    await hass.config_entries.async_unload(entry.entry_id)
    await agent.stop()


async def test_a_symlink_in_the_folder_is_not_served(hass, hass_client, view_ready, image_dir, tmp_path):
    secret = tmp_path / "secret"
    secret.write_text("x")
    import os
    os.symlink(secret, os.path.join(hass.data[DOMAIN]["image_dir"], "friday-satellite-v0.8.0.img.xz"))
    client = await hass_client()
    assert (await client.get("/api/friday/image/friday-satellite-v0.8.0.img.xz")).status == 404
