"""Optional zeroconf announcement: any failure is logged once and never stops the agent."""

from __future__ import annotations

import logging
import socket

LOG = logging.getLogger("friday_satellite_agent")


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
