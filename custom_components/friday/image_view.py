"""Provisioning images the server builds: admin-only download and the list the dashboard card shows."""

from __future__ import annotations

import asyncio
import os
import re

from aiohttp import web

from homeassistant.components.http import HomeAssistantView

from .const import DOMAIN

IMAGE_DIR = "/share/friday/imagens"  # the server VM writes here (a test overrides it in hass.data[DOMAIN]["image_dir"])
NAME_RE = re.compile(r"friday-satellite-v\d+\.\d+\.\d+\.img\.xz(\.sha256)?")
IMAGE_RE = re.compile(r"friday-satellite-(v(\d+)\.(\d+)\.(\d+))\.img\.xz")
IMAGE_URL = "/api/friday/image"


def valid_name(name: str) -> bool:
    """Plain basename of an image or its checksum, nothing path-like."""
    return bool(NAME_RE.fullmatch(name))


def version_key(tag: str) -> tuple[int, int, int]:
    """`vX.Y.Z` (or `X.Y.Z`) as numbers, so v0.10.0 sorts after v0.9.1."""
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", tag)
    if not m:
        raise ValueError(f"bad tag: {tag!r}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def download_url(files: list[str] | None) -> str | None:
    """The URL of the image itself among the files the server reports (the checksum and manifest are not it)."""
    name = next((f for f in files or [] if isinstance(f, str) and IMAGE_RE.fullmatch(f)), None)
    return f"{IMAGE_URL}/{name}" if name else None


def _folder(hass) -> str:
    return hass.data.get(DOMAIN, {}).get("image_dir") or IMAGE_DIR


def _sha256(path: str) -> str | None:
    try:
        with open(path + ".sha256", encoding="utf-8") as f:
            first = f.read().split()
    except OSError:
        return None
    return first[0] if first and re.fullmatch(r"[0-9a-fA-F]{64}", first[0]) else None


def list_images(folder: str) -> list[dict]:
    """`[{file, tag, size, mtime, sha256}]`, newest version first. Blocking: run it in the executor."""
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    out = []
    for name in names:
        m = IMAGE_RE.fullmatch(name)
        if not m:
            continue
        path = os.path.join(folder, name)
        try:
            st = os.stat(path)
        except OSError:
            continue
        out.append({"file": name, "tag": m.group(1), "size": st.st_size, "mtime": st.st_mtime,
                    "sha256": _sha256(path)})
    return sorted(out, key=lambda i: version_key(i["tag"]), reverse=True)


def _is_admin(request: web.Request) -> bool:
    user = request.get("hass_user")
    return bool(user and user.is_admin)


class FridayImageView(HomeAssistantView):
    """Streams one image (or its checksum) to administrators only."""

    url = IMAGE_URL + "/{name}"
    name = "api:friday:image"
    requires_auth = True

    async def get(self, request: web.Request, name: str) -> web.StreamResponse:
        if not _is_admin(request):
            return web.Response(status=403)
        if not valid_name(name):
            return web.Response(status=404)
        path = os.path.join(_folder(request.app["hass"]), name)
        if not await asyncio.to_thread(os.path.isfile, path):
            return web.Response(status=404)
        return web.FileResponse(path, headers={"Content-Disposition": f'attachment; filename="{name}"'})


class FridayImageListView(HomeAssistantView):
    """The images available, for the dashboard card (version, date, size)."""

    url = IMAGE_URL
    name = "api:friday:images"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        if not _is_admin(request):
            return web.Response(status=403)
        images = await asyncio.to_thread(list_images, _folder(request.app["hass"]))
        return self.json({"images": images})
