"""Proxmox snapshot client for the server VM (stdlib only).

The API token is read from a file by the caller and travels only in the Authorization header: it is
never put in an exception message or a log line.
"""
from __future__ import annotations

import json
import logging
import os
import re
import ssl
import time
import urllib.parse
import urllib.request

from friday_agent_core.ota.constants import SNAPSHOT_KEEP_S

log = logging.getLogger(__name__)

CA_PATH = "/srv/friday/secrets/proxmox_ca.pem"
TOKEN_PATH = "/srv/friday/secrets/proxmox_snapshot"
TIMEOUT_S = 30
PREFIX = "friday-ota-"
_ctx = None


class ProxmoxError(RuntimeError):
    pass


def load_token(path: str = TOKEN_PATH) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def _default_ctx() -> ssl.SSLContext:
    global _ctx
    if _ctx is None:
        if os.path.exists(CA_PATH):
            _ctx = ssl.create_default_context(cafile=CA_PATH)
        else:
            log.warning("proxmox: no CA file, TLS certificate is not verified (LAN)")
            _ctx = ssl._create_unverified_context()
    return _ctx


def _request(method: str, url: str, token: str, data: dict | None = None, ctx=None) -> dict:
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method, headers={
        "Authorization": f"PVEAPIToken={token}",
        "User-Agent": "friday-server-agent",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT_S, context=ctx or _default_ctx()) as resp:
        return json.load(resp)


class ProxmoxSnapshots:
    def __init__(self, api: str, vmid: str, token: str, request=_request, clock=time.time, sleep=time.sleep):
        self.api = api.rstrip("/")
        self.vmid = str(vmid)
        self._token = token
        self._request = request
        self._clock = clock
        self._sleep = sleep

    def _call(self, method: str, path: str, data: dict | None = None):
        try:
            return self._request(method, f"{self.api}/{path}", self._token, data=data)["data"]
        except Exception as e:
            # Only the exception type and the endpoint: the message could echo the token.
            code = getattr(e, "code", None)
            raise ProxmoxError(f"proxmox {method} {path.split('?')[0]}: {type(e).__name__}"
                               + (f" {code}" if isinstance(code, int) else "")) from None

    def snapshot_name(self, tag: str) -> str:
        clean = re.sub(r"[^A-Za-z0-9_-]", "", tag.replace(".", ""))
        return f"{PREFIX}{clean}-{int(self._clock())}"

    def _snap(self, name: str = "") -> str:
        base = f"qemu/{self.vmid}/snapshot"
        return f"{base}/{urllib.parse.quote(name, safe='')}" if name else base

    def create(self, name: str) -> str:
        return self._call("POST", self._snap(),
                          {"snapname": name, "vmstate": "0", "description": "friday-ota"})

    def rollback(self, name: str) -> str:
        return self._call("POST", f"{self._snap(name)}/rollback")

    def delete(self, name: str) -> str:
        return self._call("DELETE", self._snap(name))

    def list(self) -> list[dict]:
        return self._call("GET", self._snap())

    def wait(self, upid: str, timeout: float = 900) -> None:
        deadline = self._clock() + timeout
        path = f"tasks/{urllib.parse.quote(upid, safe='')}/status"
        while True:
            st = self._call("GET", path)
            if st.get("status") == "stopped":
                if st.get("exitstatus") != "OK":
                    raise ProxmoxError(f"proxmox task failed: {str(st.get('exitstatus'))[:200]}")
                return
            if self._clock() >= deadline:
                raise ProxmoxError("proxmox task timed out")
            self._sleep(2)

    def prune(self, keep_s: float = SNAPSHOT_KEEP_S) -> list[str]:
        now = self._clock()
        gone = []
        for s in self.list():
            name = s.get("name", "")
            snaptime = s.get("snaptime")
            if name.startswith(PREFIX) and snaptime is not None and now - snaptime > keep_s:
                self.wait(self.delete(name))  # Proxmox locks the VM per task: one delete at a time
                gone.append(name)
        return gone
