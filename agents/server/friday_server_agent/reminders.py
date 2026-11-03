"""Brain reminders that ring at home: OpenClaw cron jobs whose completion goes to the house channel (/reminder).

Read-only (sqlite `mode=ro`). Published as the hidden `reminders` state; the integration shows the next one on
each satellite. A reminder is never edited from here.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone

DB = os.environ.get("FRIDAY_OPENCLAW_DB", "/srv/friday/home/.openclaw/state/openclaw.sqlite")
MAX_ITEMS = 10
DESCRIPTOR = {"key": "reminders", "platform": "sensor", "name": "Reminders", "hidden": True, "available": True}


def pending(rows, now_ms: int) -> list[dict]:
    """rows = (job_json, state_json); upcoming home reminders sorted by time."""
    out = []
    for job_json, state_json in rows:
        try:
            job, st = json.loads(job_json), json.loads(state_json or "{}")
        except (TypeError, ValueError):
            continue
        dest = ((job.get("delivery") or {}).get("completionDestination") or {}).get("to") or ""
        nxt = st.get("nextRunAtMs")
        if not job.get("enabled") or not dest.endswith("/reminder") or not isinstance(nxt, (int, float)) or nxt < now_ms:
            continue
        out.append({"at": datetime.fromtimestamp(nxt / 1000, timezone.utc).isoformat(),
                    "name": job.get("name") or job.get("displayName")})
    return sorted(out, key=lambda r: r["at"])[:MAX_ITEMS]


def sample(db: str = DB) -> dict:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    try:
        rows = con.execute("select job_json, state_json from cron_jobs").fetchall()
    finally:
        con.close()
    return {"reminders": pending(rows, int(datetime.now(timezone.utc).timestamp() * 1000))}
