"""Voice usage ledger writer (container copy of the minimal part of /opt/friday/cost/ledger.py).
Same line format; tokens only. Never raises: a ledger failure must not stop the voice."""
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from loguru import logger

TOKEN_FIELDS = ("input_text", "input_audio", "cache_read_text", "cache_read_audio",
                "output_text", "output_audio", "reasoning")
BRT = ZoneInfo("America/Sao_Paulo")
STATE_DIR = Path(os.environ.get("FRIDAY_COST_STATE", "/srv/friday/state/cost"))


def voice_tokens(prompt, completion, cache_read, reasoning, input_audio, output_audio, cache_read_audio):
    """Gemini reports gross: prompt already contains audio and cache. Each field clamped to >= 0."""
    prompt, completion = prompt or 0, completion or 0
    cr, r, ia, oa, cra = (cache_read or 0), (reasoning or 0), (input_audio or 0), (output_audio or 0), (cache_read_audio or 0)
    cache_text = cr - cra
    t = {"cache_read_audio": cra, "cache_read_text": cache_text, "input_audio": ia - cra,
         "input_text": prompt - ia - cache_text, "output_audio": oa, "output_text": completion - oa, "reasoning": r}
    return {f: max(0, int(t[f])) for f in TOKEN_FIELDS}


def append_voice(ts, session, counter, satellite, model, tokens, state_dir=STATE_DIR):
    try:
        rec = {"ts": ts.isoformat(), "id": f"voice:{session}:{counter}", "source": "voice", "origin": "voice",
               "model": model, "session": session, "satellite": satellite,
               "tokens": {f: max(0, int(tokens.get(f, 0) or 0)) for f in TOKEN_FIELDS}}
        day = ts.astimezone(BRT).date().isoformat()
        usage = Path(state_dir) / "usage"
        usage.mkdir(parents=True, exist_ok=True)
        with open(usage / f"{day}.ndjson", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:
        logger.exception("falha ao gravar o uso da voz no registro de custo")
