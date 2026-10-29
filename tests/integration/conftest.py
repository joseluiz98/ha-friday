import pytest


@pytest.fixture(autouse=True)
def _allow_sockets(socket_enabled):
    """Connection tests talk to a real local aiohttp websocket server."""
    yield
