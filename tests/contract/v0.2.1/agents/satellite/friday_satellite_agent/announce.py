"""Optional zeroconf announcement: any failure is logged once and never stops the agent."""

from __future__ import annotations

import asyncio
import logging
import socket

LOG = logging.getLogger("friday_satellite_agent")

CHECK_S = 30


def lan_ip() -> str:
    """Address of the default-route interface (UDP connect sends no packet)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    finally:
        s.close()


def announce(sat_id: str, port: int, state: str = "paired"):
    try:
        from zeroconf import IPVersion, ServiceInfo, Zeroconf

        info = ServiceInfo(
            "_friday._tcp.local.", f"{sat_id}._friday._tcp.local.",
            addresses=[socket.inet_aton(lan_ip())], port=port,
            properties={"id": sat_id, "kind": "satellite", "port": str(port), "state": state},
            server=f"{socket.gethostname()}.local.")
        zc = Zeroconf(ip_version=IPVersion.V4Only)
        zc.register_service(info)
        return zc
    except Exception as err:  # noqa: BLE001 - discovery is optional
        LOG.warning("zeroconf announce disabled: %s", type(err).__name__)
        return None


class Keeper:
    """Keeps the announcement alive: the first boot may start the agent before the Wi-Fi is up, and the address can
    change later. Waits for a route, announces, and announces again when the address changes (06/11)."""

    def __init__(self, sat_id: str, port: int, state: str, box: list, announce_fn=announce, ip_fn=lan_ip,
                 sleep=asyncio.sleep) -> None:
        self._args = (sat_id, port, state)
        self._box, self._announce, self._ip_fn, self._sleep = box, announce_fn, ip_fn, sleep
        self.ip: str | None = None

    def close(self) -> None:
        while self._box:
            self._box.pop().close()

    async def step(self) -> None:
        try:
            ip = self._ip_fn()
        except OSError:
            return  # no route yet: what is announced (if anything) stays until there is a new address
        if ip.startswith("127.") or (ip == self.ip and self._box):
            return
        await asyncio.to_thread(self.close)
        zc = await asyncio.to_thread(self._announce, *self._args)
        if zc:
            self._box.append(zc)
            self.ip = ip

    async def run(self) -> None:
        while True:
            await self.step()
            await self._sleep(CHECK_S)
