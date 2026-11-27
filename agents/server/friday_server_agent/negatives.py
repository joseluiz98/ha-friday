"""Training baseline for a satellite's voice verifier (spec 2026-11-23-voice-training-baseline-negatives).

`GET /v1/training/negatives?sat_id=sat-xxxxxx&seconds=900` (the satellite's own OTA bearer, same TLS port) answers a tar
of negatives taken from the OTHER satellites the VM knows at that moment: their ambient recordings spread over the days
they have (an equal share per satellite) and the false activations they were taught (`vozes/_negativos`). Nothing from
`vozes/<person>`: a person's voice never leaves the satellite that recorded it. A new satellite trains on day one with
what the house already knows; the tar names carry the source (`<sat>__ambiente_...`, `<sat>__falsa_...`).
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import tarfile
import time
from typing import Callable

from aiohttp import web

LOG = logging.getLogger("friday_server_agent.negatives")
INCOMING = os.environ.get("FRIDAY_WAKEWORD_INCOMING", "/srv/wakeword-data/incoming")
SAT_ID = re.compile(r"^sat-[0-9a-f]{6}$")
AMBIENT = re.compile(r"^ambiente_\d{8}_\d{6}[\w.-]*\.wav$")
FALSE_NAME = re.compile(r"^[\w.-]+\.wav$")
BYTES_PER_S = 32000          # 16 kHz mono int16
HEADER = 44
SETTLE_S = 600               # a file touched in the last 10 min may still be arriving (rsync)
MAX_SECONDS = 1800           # one request never asks for more than 30 min of ambient
HARD_PER_SAT = 150           # false activations (a few seconds each) per source satellite
MAX_BYTES = 90 << 20


def _spread(items: list, n: int) -> list:
    """`n` items equally spaced (the sorted name is chronological)."""
    if n <= 0 or not items:
        return []
    n = min(n, len(items))
    step = len(items) / n
    return [items[int(i * step + step / 2)] for i in range(n)]


def _clip_s(path: str) -> float:
    try:
        return max(os.path.getsize(path) - HEADER, 0) / BYTES_PER_S
    except OSError:
        return 0.0


def _settled(path: str, now: float) -> bool:
    try:
        return now - os.path.getmtime(path) >= SETTLE_S
    except OSError:
        return False


def _ls(folder: str, name: re.Pattern, now: float) -> list[str]:
    try:
        names = sorted(n for n in os.listdir(folder) if name.match(n))
    except OSError:
        return []
    out = []
    for n in names:
        p = os.path.join(folder, n)
        if os.path.isfile(p) and not os.path.islink(p) and _settled(p, now):
            out.append(p)
    return out


def select(for_sat: str, seconds: float, incoming: str = INCOMING, now: float | None = None) -> list[tuple[str, str]]:
    """[(absolute path, name inside the tar)] from every satellite except `for_sat`. Ambient seconds are split equally
    among the sources that have any; false activations come whole (capped per source)."""
    now = time.time() if now is None else now
    seconds = max(0.0, min(float(seconds), MAX_SECONDS))
    try:
        sats = sorted(d for d in os.listdir(incoming) if SAT_ID.match(d) and d != for_sat
                      and os.path.isdir(os.path.join(incoming, d)))
    except OSError:
        return []
    ambient = {s: _ls(os.path.join(incoming, s, "gravacoes", "ambiente"), AMBIENT, now) for s in sats}
    sources = [s for s in sats if ambient[s]]
    out: list[tuple[str, str]] = []
    for s in sources:
        files = ambient[s]
        mean = max(sum(_clip_s(f) for f in files) / len(files), 1.0)
        for f in _spread(files, max(1, round(seconds / len(sources) / mean))):
            out.append((f, f"{s}__{os.path.basename(f)}"))
    for s in sats:
        for f in _spread(_ls(os.path.join(incoming, s, "vozes", "_negativos"), FALSE_NAME, now), HARD_PER_SAT):
            out.append((f, f"{s}__falsa_{os.path.basename(f)}"))
    return out


def build(for_sat: str, seconds: float, incoming: str = INCOMING, now: float | None = None) -> tuple[bytes, int]:
    """(tar bytes, number of clips), at most MAX_BYTES (what does not fit is left out, never cut)."""
    buf, n, total = io.BytesIO(), 0, 0
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for path, name in select(for_sat, seconds, incoming, now):
            try:
                size = os.path.getsize(path)
                if total + size > MAX_BYTES:
                    continue
                info = tarfile.TarInfo(name)
                info.size, info.mtime, info.mode = size, int(os.path.getmtime(path)), 0o644
                with open(path, "rb") as f:
                    tar.addfile(info, f)
            except OSError:
                continue
            total += size
            n += 1
    return buf.getvalue(), n


def add_routes(app: web.Application, check: Callable[[str, str], bool], incoming: str = INCOMING) -> None:
    """`check(sat_id, bearer)` is the OTA token check (`orch.tokens.check`)."""

    async def negatives(request: web.Request) -> web.StreamResponse:
        sat = request.query.get("sat_id", "")
        h = request.headers.get("Authorization", "")
        if not SAT_ID.match(sat) or not check(sat, h[7:] if h.startswith("Bearer ") else ""):
            return web.Response(status=401)
        try:
            seconds = float(request.query.get("seconds", "900"))
        except ValueError:
            return web.json_response({"ok": False, "error": "seconds"}, status=400)
        body, n = await asyncio.to_thread(build, sat, seconds, incoming)
        LOG.info("training negatives for %s: %d clips, %d bytes", sat, n, len(body))
        if n == 0:
            return web.Response(status=204)
        return web.Response(body=body, headers={"Content-Type": "application/x-tar", "X-Clips": str(n)})

    app.router.add_get("/v1/training/negatives", negatives)
