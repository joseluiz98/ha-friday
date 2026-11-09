"""Usage ledger: one NDJSON line per model call, tokens only (never cost). Spec 2026-10-23."""
import json
import os
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TOKEN_FIELDS = ("input_text", "input_audio", "cache_read_text", "cache_read_audio",
                "output_text", "output_audio", "reasoning")
SOURCES = {"voice", "brain", "lucid"}
BRT = ZoneInfo("America/Sao_Paulo")
STATE_DIR = Path(os.environ.get("FRIDAY_COST_STATE", "/srv/friday/state/cost"))


def make_record(ts, id, source, origin, model, session, satellite, tokens):
    if ts.tzinfo is None:
        raise ValueError("ts must be timezone-aware")
    if source not in SOURCES:
        raise ValueError(f"invalid source: {source}")
    out = {}
    for f in TOKEN_FIELDS:
        v = int(tokens.get(f, 0) or 0)
        if v < 0:
            raise ValueError(f"negative tokens: {f}")
        out[f] = v
    return {"ts": ts.isoformat(), "id": id, "source": source, "origin": origin, "model": model,
            "session": session, "satellite": satellite, "tokens": out}


def append(records, state_dir=STATE_DIR):
    state_dir = Path(state_dir)
    by_day = defaultdict(list)
    for r in records:
        day = datetime.fromisoformat(r["ts"]).astimezone(BRT).date()
        by_day[day].append(r)
    n = 0
    usage = state_dir / "usage"
    for day, recs in by_day.items():
        usage.mkdir(parents=True, exist_ok=True)
        data = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs)
        with open(usage / f"{day.isoformat()}.ndjson", "a", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        n += len(recs)
    return n


def read(first, last, state_dir=STATE_DIR):
    seen, out, skipped = set(), [], 0
    day = first
    while day <= last:
        f = Path(state_dir) / "usage" / f"{day.isoformat()}.ndjson"
        if f.exists():
            with open(f, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    try:
                        r = json.loads(line)
                        r["id"], r["ts"], r["tokens"]
                    except (ValueError, KeyError, TypeError):
                        skipped += 1
                        continue
                    if r["id"] in seen:
                        continue
                    seen.add(r["id"])
                    out.append(r)
        day += timedelta(days=1)
    return out, skipped
