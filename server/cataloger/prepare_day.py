#!/usr/bin/env python3
"""Preparo do dia do Lucid Dreaming (spec 2026-10-21 memória compartilhada).

Lê as conversas de voz de um dia de Brasília, grava a entrada do agente `cataloger`
(inbox/voice-day.md e inbox/brain-skills.md), guarda o backup da memória da `friday` e mantém o
estado em state/lucid.json. Só stdlib. Determinístico: nenhuma chamada a modelo.

Uso:
  prepare_day.py --next          imprime "AAAA-MM-DD N" do próximo dia preparado, ou nada
  prepare_day.py --done AAAA-MM-DD
"""
import ast
import json
import os
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Constantes: o ambiente com o mesmo nome sobrescreve (usado nos testes).
_DEFAULTS = {
    "NDJSON_DIR": "/mnt/haos/share/claude-voice/conversations",
    "CATALOGER_WS": "/srv/friday/state/cataloger",
    "FRIDAY_WS": "/srv/friday/home/.openclaw/workspace/friday",
    "OC": "/usr/local/bin/oc",
}
TZ = ZoneInfo("America/Sao_Paulo")
MAX_DAYS = 3
KEEP_BACKUPS = 14


def cfg(name):
    return os.environ.get(name) or _DEFAULTS[name]


def _hoje():
    # TODAY (AAAA-MM-DD) só existe para os testes; em produção vale o relógio de Brasília.
    forcado = os.environ.get("TODAY")
    if forcado:
        return date.fromisoformat(forcado)
    return datetime.now(TZ).date()


def _tools_names(tools):
    """tools pode ser lista ou string com o repr de uma lista Python (nunca eval)."""
    if isinstance(tools, str):
        try:
            tools = ast.literal_eval(tools)
        except (ValueError, SyntaxError):
            return []
    if not isinstance(tools, (list, tuple)):
        return []
    nomes = []
    for t in tools:
        if isinstance(t, dict) and t.get("name"):
            nomes.append(str(t["name"]))
        elif isinstance(t, str) and t:
            nomes.append(t)
    return nomes


def _parse_ts(ts):
    if not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def format_turn(rec, tz):
    """`HH:MM [canal] pessoa: <prompt> | Friday: <response>[ | ferramentas: a, b]`; None se não for turno válido."""
    if not isinstance(rec, dict) or rec.get("mode") != "friday":
        return None
    dt = _parse_ts(rec.get("ts"))
    if dt is None:
        return None
    hhmm = dt.astimezone(tz).strftime("%H:%M")
    canal = "satélite (escritório)" if "satelite" in str(rec.get("sessionKey") or "") else "página/outro"
    prompt = str(rec.get("prompt") or "").strip() or "(aberta pela Pi, sem fala)"
    resposta = str(rec.get("response") or "").strip()
    linha = f"{hhmm} [{canal}] pessoa: {prompt} | Friday: {resposta}"
    nomes = _tools_names(rec.get("tools"))
    if nomes:
        linha += " | ferramentas: " + ", ".join(nomes)
    return linha


def voice_lines(day, ndjson_dir):
    """Turnos do dia de Brasília: junta o arquivo UTC do dia e o do dia seguinte."""
    ndjson_dir = Path(ndjson_dir)
    achados = []
    for d in (day, day + timedelta(days=1)):
        arq = ndjson_dir / f"{d.isoformat()}.ndjson"
        try:
            texto = arq.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for bruto in texto.splitlines():
            bruto = bruto.strip()
            if not bruto:
                continue
            try:
                rec = json.loads(bruto)
            except ValueError:
                continue
            dt = _parse_ts(rec.get("ts")) if isinstance(rec, dict) else None
            if dt is None or dt.astimezone(TZ).date() != day:
                continue
            linha = format_turn(rec, TZ)
            if linha:
                achados.append((dt, linha))
    achados.sort(key=lambda x: x[0])
    return [l for _, l in achados]


def pending_days(state, today):
    """Até 3 dias, o mais antigo primeiro: pendentes + (last_done, ontem]."""
    ontem = today - timedelta(days=1)
    dias = set()
    for p in state.get("pending") or []:
        try:
            dias.add(date.fromisoformat(p))
        except (TypeError, ValueError):
            pass
    last = state.get("last_done")
    if last:
        d = date.fromisoformat(last) + timedelta(days=1)
        while d <= ontem:
            dias.add(d)
            d += timedelta(days=1)
    else:
        dias.add(ontem)
    return sorted(dias)[:MAX_DAYS]


def skills_text(skills_json):
    linhas = []
    for s in (skills_json or {}).get("skills", []):
        nome = str(s.get("name", "")).strip()
        if not nome:
            continue
        desc = " ".join(str(s.get("description", "")).split())
        linhas.append(f"- {nome}: {desc}")
    return "\n".join(linhas)


def backup(friday_ws, backups_dir, day, keep=KEEP_BACKUPS):
    friday_ws, backups_dir = Path(friday_ws), Path(backups_dir)
    destino = backups_dir / day.strftime("%Y%m%d")
    destino.mkdir(parents=True, exist_ok=True)
    for nome in ("MEMORY.md", "USER.md"):
        origem = friday_ws / nome
        if origem.exists():
            shutil.copy2(origem, destino / nome)
    pastas = sorted(p for p in backups_dir.iterdir() if p.is_dir())
    for velha in pastas[:-keep] if len(pastas) > keep else []:
        shutil.rmtree(velha, ignore_errors=True)
    return destino


def _state_path():
    return Path(cfg("CATALOGER_WS")) / "state" / "lucid.json"


def load_state():
    try:
        st = json.loads(_state_path().read_text(encoding="utf-8"))
        if isinstance(st, dict):
            st.setdefault("last_done", None)
            st.setdefault("pending", [])
            return st
    except (OSError, ValueError):
        pass
    return {"last_done": None, "pending": []}


def save_state(st):
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def mark_done(day_iso):
    st = load_state()
    if st["last_done"] is None or day_iso > st["last_done"]:
        st["last_done"] = day_iso
    st["pending"] = sorted({p for p in st["pending"] if p != day_iso})
    save_state(st)


def _mark_pending(day_iso):
    st = load_state()
    if day_iso not in st["pending"]:
        st["pending"] = sorted(st["pending"] + [day_iso])
    save_state(st)


def _write_skills(cat):
    texto = ""
    try:
        r = subprocess.run([cfg("OC"), "skills", "list", "--agent", "friday", "--eligible", "--json"],
                           capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            texto = skills_text(json.loads(r.stdout))
    except (OSError, ValueError, subprocess.SubprocessError):
        texto = ""
    (cat / "inbox" / "brain-skills.md").write_text(texto + ("\n" if texto else ""), encoding="utf-8")


def cmd_next():
    ndjson = Path(cfg("NDJSON_DIR"))
    cat = Path(cfg("CATALOGER_WS"))
    for _ in range(MAX_DAYS):
        dias = pending_days(load_state(), _hoje())
        if not dias:
            return 0
        dia = dias[0]
        if not ndjson.is_dir():  # Samba fora: nada é escrito, o dia fica pendente
            _mark_pending(dia.isoformat())
            return 0
        linhas = voice_lines(dia, ndjson)
        if not linhas:
            mark_done(dia.isoformat())
            continue
        (cat / "inbox").mkdir(parents=True, exist_ok=True)
        cab = f"# Conversas de voz de {dia.isoformat()} (horário de Brasília, said_by=unknown)\n\n"
        (cat / "inbox" / "voice-day.md").write_text(cab + "\n".join(linhas) + "\n", encoding="utf-8")
        _write_skills(cat)
        backup(cfg("FRIDAY_WS"), cat / "backups", dia)
        print(f"{dia.isoformat()} {len(linhas)}")
        return 0
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--next"]:
        return cmd_next()
    if len(argv) == 2 and argv[0] == "--done":
        date.fromisoformat(argv[1])
        mark_done(argv[1])
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
