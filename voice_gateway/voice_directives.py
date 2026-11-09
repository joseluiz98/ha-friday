"""Diretrizes do USER.md que chegam à voz, com teto (23/10).
Vista curada pelo Lucid (voice-directives.md, teto VIEW_MAX) + diretrizes novas desde a geração dela (delta, teto
DELTA_MAX_ITEMS/DELTA_MAX_CHARS, mais recentes primeiro), lidas no arquivo inteiro. Sem vista, todas as ativas.
Lógica quase pura (teste: test_voice_directives.py)."""
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import preferencias_voz as pv

VIEW = os.environ.get("FRIDAY_VOICE_DIRECTIVES", "/srv/friday/state/cataloger/out/voice-directives.md")
EXCLUDED = os.environ.get("FRIDAY_VOICE_DIRECTIVES_EXCLUDED",
                          "/srv/friday/state/cataloger/out/voice-directives-excluded.json")
VIEW_MAX = 1600
DELTA_MAX_ITEMS = 5
DELTA_MAX_CHARS = 600
_BRT = timezone(timedelta(hours=-3))


@dataclass
class Result:
    context: str = ""
    delivered: list = field(default_factory=list)
    overflow: list = field(default_factory=list)
    missing: list = field(default_factory=list)


def _view_lines(view_text: str) -> list:
    lines, total = [], 0
    for linha in view_text.splitlines():
        if not linha.startswith("- "):
            continue
        t = linha[2:].strip()
        if total + len(t) > VIEW_MAX:
            break
        lines.append(t)
        total += len(t)
    return lines


def build(user_md: str, view_text, view_date, excluded: list) -> Result:
    active = pv.all_active(user_md)
    if view_text is None:
        todas = [t for _, t in active]
        return Result(context=pv.contexto(todas), delivered=todas)
    view_lines = _view_lines(view_text)
    excl = set(excluded)
    since = view_date or ""
    cand = [(o, i, t) for i, (o, t) in enumerate(active)
            if o >= since and t not in view_lines and t not in excl]
    cand.sort(key=lambda x: (x[0], x[1]), reverse=True)
    delta, overflow, chars, full = [], [], 0, False
    for _, _, t in cand:
        if not full and len(delta) < DELTA_MAX_ITEMS and chars + len(t) <= DELTA_MAX_CHARS:
            delta.append(t)
            chars += len(t)
        else:
            full = True
            overflow.append(t)
    known = set(view_lines) | set(delta) | set(overflow) | excl
    missing = [t for _, t in active if t not in known]
    delivered = view_lines + delta
    return Result(context=pv.contexto(delivered), delivered=delivered, overflow=overflow, missing=missing)


def load(now_ts=None) -> Result:
    """Nunca levanta exceção. Vista ausente/ilegível = comportamento antigo (todas as ativas)."""
    try:
        try:
            with open(pv.CAMINHO, encoding="utf-8") as f:
                user_md = f.read()
        except OSError:
            user_md = ""
        view_text = view_date = None
        try:
            with open(VIEW, encoding="utf-8") as f:
                view_text = f.read()
            ts = now_ts if now_ts is not None else os.path.getmtime(VIEW)
            view_date = datetime.fromtimestamp(ts, _BRT).strftime("%Y-%m-%d")
        except (OSError, ValueError):
            view_text = view_date = None
        excluded = []
        try:
            with open(EXCLUDED, encoding="utf-8") as f:
                ex = json.load(f).get("excluded", [])
            if isinstance(ex, list):
                excluded = [e for e in ex if isinstance(e, str)]
        except (OSError, ValueError, AttributeError):
            excluded = []
        return build(user_md, view_text, view_date, excluded)
    except Exception:
        return Result()
