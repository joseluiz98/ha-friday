"""Constants for the F.R.I.D.A.Y. integration."""

from __future__ import annotations

DOMAIN = "friday"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_TOKEN = "token"

DEFAULT_PORT_SATELLITE = 8701
DEFAULT_PORT_SERVER = 8801

WS_PATH = "/v1/ws"

DEVICE_KIND_SERVER = "server"
DEVICE_KIND_SATELLITE = "satellite"

ORIGINS = ("voice", "button", "ha", "agent", "system")
ORIGIN_HA = "ha"
