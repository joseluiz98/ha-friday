"""Vistas de memória da voz (spec 2026-10-21 memória compartilhada §5.4–5.6). Só a stdlib.

O Lucid Dreaming (programa `cataloger`, na VM) escreve `voice-briefing.md` e `brain-capabilities.md`. Aqui a voz
os lê na abertura de cada conversa, atrás do modo `off|ab|on` (arquivo lido a cada conexão). Vista ausente, velha
(mais de 48 h), vazia ou ilegível = a voz segue exatamente como antes."""
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

VOICE_BRIEFING = os.environ.get("FRIDAY_VOICE_BRIEFING", "/srv/friday/state/cataloger/out/voice-briefing.md")
BRAIN_CAPABILITIES = os.environ.get("FRIDAY_BRAIN_CAPABILITIES", "/srv/friday/state/cataloger/out/brain-capabilities.md")
MODE_FILE = os.environ.get("FRIDAY_MEMORY_VIEWS_MODE", "/srv/friday/state/memory-views-mode")
AB_FILE = os.environ.get("FRIDAY_MEMORY_VIEWS_AB", "/srv/friday/state/memory-views-ab.ndjson")
BRIEFING_MAX, CAPABILITIES_MAX = 1200, 400

BRAIN_PREFIX = ("Encaminha um pedido ao cérebro do assistente (agente OpenClaw com memória persistente, "
                "acesso ao homelab e capacidade de pesquisa e execução). ")
BRAIN_DESCRIPTION_DEFAULT = BRAIN_PREFIX + ("Use para perguntas sobre o homelab, lembrar ou recuperar informações, "
                                            "raciocínio mais longo e tarefas.")
_BRT = timezone(timedelta(hours=-3))


def _cut(text, max_chars):
    text = text.strip()
    if len(text) <= max_chars:
        return text
    if text[max_chars].isspace():
        return text[:max_chars].rstrip()
    corte = text[:max_chars]
    m = list(re.finditer(r"\s", corte))
    return corte[:m[-1].start()].rstrip() if m else corte


def read_view(path, now, max_chars, max_age_h=48):
    """Texto da vista, ou "" se faltar, estiver vazia, velha (> max_age_h) ou ilegível."""
    try:
        if now - os.path.getmtime(path) > max_age_h * 3600:
            return ""
        with open(path, encoding="utf-8") as f:
            return _cut(f.read(), max_chars)
    except (OSError, ValueError):
        return ""


def briefing_context(text):
    if not text:
        return ""
    return ("Fatos recentes da casa que o cérebro guardou de conversas anteriores "
            "(podem não se aplicar a esta conversa):\n" + text)


def brain_description(capabilities):
    """Sem capacidades: o texto de sempre. Com elas, substituem a lista escrita à mão (não somam)."""
    if not capabilities:
        return BRAIN_DESCRIPTION_DEFAULT
    return BRAIN_PREFIX + capabilities


def read_mode(path):
    try:
        with open(path, encoding="utf-8") as f:
            modo = f.read().strip()
    except (OSError, ValueError):
        return "off"
    return modo if modo in ("off", "ab", "on") else "off"


def choose_arm(mode, rnd):
    if mode == "on":
        return "on"
    if mode == "ab":
        return "on" if rnd < 0.5 else "off"
    return "off"


class AbLog:
    """Uma linha JSON por evento (start, usage, end). Erro de I/O nunca derruba a conversa."""

    def __init__(self, path=AB_FILE):
        self._path = path

    def _write(self, event, session, arm, **extra):
        linha = {"event": event, "ts": datetime.now(_BRT).isoformat(timespec="seconds"), "session": session,
                 "arm": arm, **extra}
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(linha, ensure_ascii=False) + "\n")
        except OSError as e:
            sys.stderr.write(f"memory_views: não gravou {event}: {type(e).__name__}\n")

    def start(self, session, arm, satellite):
        self._write("start", session, arm, satellite=satellite)

    def usage(self, session, arm, prompt_tokens, reasoning_tokens):
        self._write("usage", session, arm, prompt_tokens=prompt_tokens, reasoning_tokens=reasoning_tokens)

    def end(self, session, arm):
        self._write("end", session, arm)
