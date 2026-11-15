"""Agent core shared by the server and satellite agents.

AgentCore and PROTOCOL_VERSION load on first use: the satellite's root helper imports `friday_agent_core.ota` with
the system Python, which has no aiohttp (14/11: an eager import here failed every activation).
"""

from . import diagnostics

__all__ = ["AgentCore", "PROTOCOL_VERSION", "diagnostics"]


def __getattr__(name):
    if name in ("AgentCore", "PROTOCOL_VERSION"):
        from . import server
        return getattr(server, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
