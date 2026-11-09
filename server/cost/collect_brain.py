"""Copy the brain's (OpenClaw) Gemini usage from its SQLite (read-only) into the usage ledger."""
import glob
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import ledger

DEFAULT_GLOB = "/srv/friday/home/.openclaw/agents/*/agent/openclaw-agent.sqlite"
CURSOR_FILE = "brain_cursor.json"


def classify(session_key):
    k = session_key
    for marker in (":openai-user:voz-medicao-", ":openai-user:diag-", ":openai-user:auditoria-claude-",
                   ":teste-", ":openai-user:teste-"):
        if marker in k:
            return "test"
    if ":openai-user:voz-" in k:
        return "voice_request"
    if ":cron:" in k:
        return "automation"
    if k.endswith(":main"):
        return "main_chat"
    if ":ios-" in k:
        return "app"
    return "other"


def _decode(event_json, event_zstd):
    if event_json is not None:
        return json.loads(event_json)
    import zstandard
    return json.loads(zstandard.ZstdDecompressor().decompress(event_zstd).decode("utf-8"))


def _agent_of(path):
    return Path(path).parent.parent.name


def collect(db_glob=DEFAULT_GLOB, state_dir=ledger.STATE_DIR):
    state_dir = Path(state_dir)
    cur_path = state_dir / CURSOR_FILE
    try:
        cursors = json.loads(cur_path.read_text())
    except (OSError, ValueError):
        cursors = {}
    result = {}
    for path in sorted(glob.glob(db_glob)):
        agent = _agent_of(path)
        con = None
        try:
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
            max_rowid = con.execute("select coalesce(max(rowid),0) from transcript_events").fetchone()[0]
            cursor = cursors.get(path, 0)
            if cursor > max_rowid:  # rowids reused after deletions: re-read; ids dedupe on read
                cursor = 0
            rows = con.execute(
                "select e.rowid, e.event_json, e.event_zstd, w.session_key from transcript_events e "
                "left join session_windows w on w.session_id = e.session_id "
                "where e.rowid > ? order by e.rowid", (cursor,)).fetchall()
            con.close()
        except sqlite3.Error as exc:
            if con is not None:
                con.close()
            print(f"warning: cannot read {agent}: {exc}", file=sys.stderr)
            continue
        records, last = [], cursor
        for rowid, ej, ez, session_key in rows:
            last = rowid
            try:
                e = _decode(ej, ez)
            except Exception as exc:  # corrupt event: skip, keep going
                print(f"warning: {agent} rowid {rowid}: {type(exc).__name__}", file=sys.stderr)
                continue
            m = e.get("message") or {}
            u = m.get("usage")
            if m.get("role") != "assistant" or m.get("provider") != "google" or not u:
                continue
            ts = datetime.fromtimestamp(m["timestamp"] / 1000, tz=timezone.utc)
            sk = session_key or ""
            records.append(ledger.make_record(
                ts, f"brain:{agent}:{e['id']}", "brain", classify(sk), m.get("model", ""), sk, None,
                {"input_text": int(u.get("input", 0)) + int(u.get("cacheWrite", 0)),
                 "cache_read_text": int(u.get("cacheRead", 0)),
                 "output_text": int(u.get("output", 0))}))
        ledger.append(records, state_dir)
        cursors[path] = last
        state_dir.mkdir(parents=True, exist_ok=True)
        tmp = cur_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cursors))
        tmp.replace(cur_path)
        result[agent] = len(records)
    return result


def main():
    res = collect()
    print(" ".join(f"{a}={n}" for a, n in res.items()) or "no-databases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
