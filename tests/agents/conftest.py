import pytest


@pytest.fixture(autouse=True)
def _allow_sockets(socket_enabled):
    """Agent tests run a real local aiohttp server; the HA plugin blocks sockets."""
    yield
