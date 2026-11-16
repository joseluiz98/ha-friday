"""Logs of a failed server update, kept OFF the VM (FINDING 9, 16/11).

A container failure restores the Proxmox snapshot, and the whole disk goes back: the journal and the docker logs of the
failed try were gone. Before the restore, the installer writes them under the HAOS share
(`/share/logs/friday/ota/<tag>-<YYYYmmdd-HHMMSS>/`, generic logs folder), keeping the newest `KEEP`. Never raises.
"""

from __future__ import annotations

import logging
import os
import shutil
import time

LOG = logging.getLogger("friday_server_agent.ota")
ROOT = "/mnt/haos/share/logs/friday/ota"
KEEP = 10


def save(tag: str, files: dict[str, str], root: str = ROOT, clock=time.time, keep: int = KEEP) -> str | None:
    """Writes `files` ({name: text}) into a new folder; returns its path, or None when the share is unavailable."""
    try:
        os.makedirs(root, exist_ok=True)
        name = f"{tag}-{time.strftime('%Y%m%d-%H%M%S', time.localtime(clock()))}"
        dest = os.path.join(root, name)
        os.makedirs(dest, exist_ok=True)
        for fname, text in files.items():
            with open(os.path.join(dest, os.path.basename(fname)), "w", encoding="utf-8") as f:
                f.write(text or "")
        _prune(root, keep)
        return dest
    except OSError as err:
        LOG.warning("OTA: could not keep the failure logs: %s", type(err).__name__)
        return None


def _prune(root: str, keep: int) -> None:
    dirs = sorted((d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))),
                  key=lambda d: d[-15:])  # the YYYYmmdd-HHMMSS suffix: oldest first, whatever the tag
    for d in dirs[:-keep] if len(dirs) > keep else []:
        shutil.rmtree(os.path.join(root, d), ignore_errors=True)
