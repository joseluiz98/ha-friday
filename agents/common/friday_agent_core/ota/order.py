"""Server-side install order and the satellite/server protocol gate."""
from .manifest import COMPONENT_ORDER, Manifest


def server_plan(changed: list[str]) -> list[str]:
    return [c for c in COMPONENT_ORDER if c in changed and c != "satellite"]


def satellite_ready(target: Manifest, server_protocol_installed: int) -> bool:
    """The satellite offer only goes out once the installed server speaks the protocol the release requires."""
    return server_protocol_installed >= target.requires.get("satellite", {}).get("server", 1)


def accepts(server: Manifest, satellite_protocol: int) -> bool:
    return satellite_protocol >= server.min_protocol.get("satellite", 1)
