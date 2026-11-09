"""Client for the satellite program's local port (GET /status, GET/PUT /config, /wakewords)."""

from __future__ import annotations

import aiohttp


class ProgramError(Exception):
    def __init__(self, status: int, message: str = "") -> None:
        super().__init__(message or str(status))
        self.status = status


class ProgramClient:
    def __init__(self, token: str, base: str = "http://127.0.0.1:8700") -> None:
        self._base = base.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}

    async def _call(self, method: str, path: str, body: dict | None = None):
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.request(method, self._base + path, json=body, headers=self._headers) as r:
                if r.status >= 400:
                    try:
                        msg = (await r.json()).get("erro", "")
                    except Exception:  # noqa: BLE001
                        msg = ""
                    raise ProgramError(r.status, msg)
                return await r.json()

    async def status(self) -> dict | None:
        try:
            return await self._call("GET", "/status")
        except (aiohttp.ClientError, OSError, ProgramError, TimeoutError):
            return None

    async def get_config(self) -> dict:
        return await self._call("GET", "/config")

    async def put_config(self, changes: dict) -> dict:
        return await self._call("PUT", "/config", changes)

    async def wakewords(self) -> list[dict]:
        return await self._call("GET", "/wakewords")

    async def set_wakeword(self, name: str, **fields) -> dict:
        return await self._call("PUT", f"/wakewords/{name}", fields)

    async def command(self, body: dict) -> dict:
        """POST /command: provisioning commands (play_phrases, ring, wake_word_test, open_presentation)."""
        return await self._call("POST", "/command", body)
