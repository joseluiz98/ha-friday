"""Optional zeroconf announcement: any failure is logged once and never stops the agent."""

from __future__ import annotations

import asyncio
import logging
import socket

from . import hardware

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


def addresses(run=hardware._run) -> dict[str, str]:
    """IPv4 of the connected cable ("wired") and Wi-Fi ("wifi") interfaces; a missing key = no address there.
    Empty when nmcli is unavailable (the caller then falls back to the default route)."""
    try:
        devices = hardware.parse_devices(run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"]))
        out: dict[str, str] = {}
        for key, kind in (("wired", "ethernet"), ("wifi", "wifi")):
            dev = next((d for d, t, st in devices if t == kind and st == "connected"), None)
            if dev is None:
                continue
            ip = run(["nmcli", "-g", "IP4.ADDRESS", "device", "show", dev]).split("/")[0].strip()
            if ip:
                out[key] = ip
        return out
    except Exception:  # noqa: BLE001 - no nmcli (development box) or a transient failure
        return {}


def announce(sat_id: str, port: int, state: str = "paired", addrs: dict[str, str] | None = None):
    try:
        from zeroconf import IPVersion, ServiceInfo, Zeroconf

        addrs = addrs or {}
        # preferred first: the cable, then the Wi-Fi; no declared address = the default route (old behaviour)
        ordered = [addrs[k] for k in ("wired", "wifi") if k in addrs] or [lan_ip()]
        props = {"id": sat_id, "kind": "satellite", "port": str(port), "state": state}
        props.update({k: addrs[k] for k in ("wired", "wifi") if k in addrs})
        info = ServiceInfo(
            "_friday._tcp.local.", f"{sat_id}._friday._tcp.local.",
            addresses=[socket.inet_aton(ip) for ip in ordered], port=port,
            properties=props, server=f"{socket.gethostname()}.local.")
        zc = Zeroconf(ip_version=IPVersion.V4Only)
        zc.register_service(info)
        return zc
    except Exception as err:  # noqa: BLE001 - discovery is optional
        LOG.warning("zeroconf announce disabled: %s", type(err).__name__)
        return None


class Keeper:
    """Keeps the announcement alive: the first boot may start the agent before the Wi-Fi is up, and the addresses can
    change later. Waits for a route, announces, and announces again when the cable or Wi-Fi address changes (06/11,
    21/11)."""

    def __init__(self, sat_id: str, port: int, state: str, box: list, announce_fn=announce, ip_fn=lan_ip,
                 sleep=asyncio.sleep, addr_fn=addresses) -> None:
        self._args = (sat_id, port, state)
        self._box, self._announce, self._ip_fn, self._addr_fn, self._sleep = box, announce_fn, ip_fn, addr_fn, sleep
        self.addrs: dict[str, str] | None = None

    def close(self) -> None:
        while self._box:
            self._box.pop().close()

    def _current(self) -> tuple[dict[str, str], dict[str, str]] | None:
        """(announced addresses, comparison key) or None when there is nothing usable yet."""
        addrs = {k: v for k, v in self._addr_fn().items() if not v.startswith("127.")}
        if addrs:
            return addrs, addrs
        try:
            ip = self._ip_fn()
        except OSError:
            return None  # no route yet: what is announced (if anything) stays until there is a new address
        if ip.startswith("127."):
            return None
        return {}, {"route": ip}

    async def step(self) -> None:
        current = self._current()
        if current is None:
            return
        addrs, key = current
        if key == self.addrs and self._box:
            return
        await asyncio.to_thread(self.close)
        zc = await asyncio.to_thread(self._announce, *self._args, addrs)
        if zc:
            self._box.append(zc)
            self.addrs = key

    async def run(self) -> None:
        while True:
            await self.step()
            await self._sleep(CHECK_S)
