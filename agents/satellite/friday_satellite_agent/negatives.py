"""Training baseline for the satellite's voice verifier (spec 2026-11-23-voice-training-baseline-negatives).

The program asks for it on loopback (`POST /v1/local/negatives`) when this satellite has too little ambient audio of its
own. The agent fetches the pack from the VM over the OTA channel (same bearer, same pinned certificate), unpacks it
into the program's `_baseline` folder and hands the folder to the user that owns the voices. The program deletes the
folder when the training ends. Never logs the token.
"""
from __future__ import annotations

import io
import logging
import os
import re
import shutil
import tarfile
import tempfile

LOG = logging.getLogger("friday_satellite_agent.negatives")
DEST = "/home/jose/vozes/_baseline"
NAME = re.compile(r"^sat-[0-9a-f]{6}__[\w.-]+\.wav$")
MAX_FILE = 24 << 20
MAX_TOTAL = 100 << 20


class Refused(Exception):
    pass


def unpack(data: bytes, dest: str, owner: tuple[int, int] | None = None) -> dict:
    """Extracts only flat `.wav` regular files with the expected names, within the size caps; anything else (paths,
    links, devices, odd names) makes the whole pack refused and nothing is left behind."""
    parent = os.path.dirname(dest)
    if not os.path.isdir(parent):  # a new satellite has no voices folder before its first enrolment
        os.makedirs(parent, mode=0o700)
        if owner:
            os.chown(parent, *owner)
    tmp = tempfile.mkdtemp(prefix=".baseline-", dir=parent)
    try:
        total = n = 0
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
            for m in tar.getmembers():
                if not (m.isfile() and NAME.match(m.name) and 0 < m.size <= MAX_FILE):
                    raise Refused(f"member {m.name[:60]!r} not allowed")
                total += m.size
                if total > MAX_TOTAL:
                    raise Refused("pack too large")
                src = tar.extractfile(m)
                with open(os.path.join(tmp, m.name), "wb") as f:
                    shutil.copyfileobj(src, f)
                n += 1
        if owner:
            for name in os.listdir(tmp):
                os.chown(os.path.join(tmp, name), *owner)
            os.chown(tmp, *owner)
        os.chmod(tmp, 0o700)
        shutil.rmtree(dest, ignore_errors=True)
        os.replace(tmp, dest)
        return {"ok": True, "clips": n, "bytes": total}
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


async def fetch(updater, seconds: float, dest: str = DEST, owner: tuple[int, int] | None = None) -> dict:
    """{"ok": True, "clips": n} or {"ok": False, "error": "..."}; never raises (the training goes on without it)."""
    try:
        s, token = updater.settings(), updater.token()
        if not (s["url"] and s["fingerprint"] and token):
            return {"ok": False, "error": "no_credentials"}
        import aiohttp
        url = f"{s['url']}/v1/training/negatives"
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600)) as http:
            async with http.get(url, params={"sat_id": updater.sat_id, "seconds": str(int(seconds))},
                                headers={"Authorization": f"Bearer {token}"},
                                ssl=updater.http._ssl(s["fingerprint"])) as r:
                if r.status == 204:
                    shutil.rmtree(dest, ignore_errors=True)
                    return {"ok": True, "clips": 0, "bytes": 0}
                if r.status != 200:
                    return {"ok": False, "error": f"http {r.status}"}
                data = await r.read()
        return unpack(data, dest, owner)
    except Refused as err:
        LOG.warning("training baseline refused: %s", err)
        return {"ok": False, "error": "refused"}
    except Exception as err:  # noqa: BLE001 - offline VM, cut download, full disk
        LOG.warning("training baseline failed: %s", type(err).__name__)
        return {"ok": False, "error": type(err).__name__}
