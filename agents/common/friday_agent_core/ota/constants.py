"""Shared OTA constants (values come from the OTA spec; names are used by server and satellite)."""

POLL_GITHUB_S = 600
POLL_SATELLITE_S = 120
SOAK_S = 172800  # 48 h in alpha before a pre-release is promoted to stable
RETRY_DELAYS_S = (900, 3600, 21600, 86400)
PUSH_WINDOW = ("07:30", "22:00")
HEALTH_TIMEOUT_S = 180
HEAVY_IDLE_S = 1800
KEEP_RELEASES = 2
KEEP_IMAGES = 2
SNAPSHOT_KEEP_S = 86400
COMPAT_STABLE_RELEASES = 3
UPDATE_PROTOCOL = 1
