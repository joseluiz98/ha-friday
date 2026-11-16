"""Shared OTA constants (values come from the OTA spec; names are used by server and satellite).

FRIDAY_OTA_SOAK_S and FRIDAY_OTA_RETRY_DELAYS ("60,120,180,240") shorten the alpha soak and the retry schedule for
the live rehearsal only (plan 2026-11-10 Task 21); production never sets them."""
import os


def _delays(raw: str | None) -> tuple[int, ...] | None:
    try:
        out = tuple(int(x) for x in (raw or "").split(",") if x.strip())
    except ValueError:
        return None
    return out if out and all(d > 0 for d in out) else None


POLL_GITHUB_S = 600
POLL_SATELLITE_S = 120
SOAK_S = int(os.environ.get("FRIDAY_OTA_SOAK_S") or 172800)  # 48 h in alpha before a pre-release is promoted
RETRY_DELAYS_S = _delays(os.environ.get("FRIDAY_OTA_RETRY_DELAYS")) or (900, 3600, 21600, 86400)
PUSH_WINDOW = ("07:30", "22:00")
HEALTH_TIMEOUT_S = 180
# The container switch: OpenClaw migrates its databases on the first start of a new version (69 s on 16/11).
CONTAINER_HEALTH_TIMEOUT_S = 600
HEAVY_IDLE_S = 1800
KEEP_RELEASES = 2
KEEP_IMAGES = 2
SNAPSHOT_KEEP_S = 86400
COMPAT_STABLE_RELEASES = 3
UPDATE_PROTOCOL = 1
