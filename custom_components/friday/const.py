"""Constants for the F.R.I.D.A.Y. integration."""

from __future__ import annotations

DOMAIN = "friday"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_TOKEN = "token"
CONF_AREA_ID = "area_id"

DEFAULT_PORT_SATELLITE = 8701
DEFAULT_PORT_SERVER = 8801

WS_PATH = "/v1/ws"

DEVICE_KIND_SERVER = "server"
DEVICE_KIND_SATELLITE = "satellite"

ORIGINS = ("voice", "button", "ha", "agent", "system")
ORIGIN_HA = "ha"

CONF_PROXMOX_URL = "proxmox_url"
CONF_PROXMOX_NODE = "proxmox_node"
CONF_PROXMOX_VMID = "proxmox_vmid"
CONF_PROXMOX_TOKEN_ID = "proxmox_token_id"
CONF_PROXMOX_TOKEN_SECRET = "proxmox_token_secret"
PROXMOX_FIELDS = (
    CONF_PROXMOX_URL, CONF_PROXMOX_NODE, CONF_PROXMOX_VMID,
    CONF_PROXMOX_TOKEN_ID, CONF_PROXMOX_TOKEN_SECRET,
)
DEFAULT_PROXMOX_URL = "https://192.0.2.252:8006"
DEFAULT_PROXMOX_NODE = "hypervisor"

EVENT_CHANGE = "friday_change"
# Entities that only mirror a reading are not logged as changes.
READ_ONLY_PLATFORMS = ("sensor", "binary_sensor")
