import pytest


@pytest.fixture(autouse=True)
def _allow_sockets(socket_enabled):
    """The install-script tests run a local HTTP server; the HA plugin blocks sockets."""
    yield
