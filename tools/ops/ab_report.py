#!/usr/bin/env python3
"""Relatório do A/B das vistas de memória da voz (spec 2026-10-21 §5.6). Só a stdlib.

Cruza o `memory-views-ab.ndjson` da VM (braço, início e fim de cada conversa, tokens por turno) com a
`espera_resposta` do journal da Pi e compara o braço `on` (com as vistas) com o `off`.
Uso: python3 /config/tools/friday_memoria/ab_report.py"""
import json
import re
import statistics
import subprocess
import sys
from datetime import datetime

MIN_SESSIONS = 30
MAX_MEDIAN_ESPERA_S, MAX_P90_ESPERA_S, MAX_REASONING_RATIO = 0.3, 0.5, 0.15
VM = open("/config/.friday/vm").read().strip()
PI_KEY, PI = "/data/.ssh/id_ed25519", "jose@" + open("/config/.friday/mk1").read().strip()


def median(xs):
    return statistics.median(xs) if xs else None


def p90(xs):
    if not xs:
        return None
    if len(xs) < 2:
        return xs[0]
    return statistics.quantiles(xs, n=10)[8]


def parse_ab(lines):
    sessoes = {}
    for linha in lines:
        try:
            d = json.loads(linha)
            ts = datetime.fromisoformat(d["ts"])
            sid, event = d["session"], d["event"]
        except (ValueError, KeyError, TypeError):
            continue
        s = sessoes.setdefault(sid, {"arm": d.get("arm"), "start": None, "end": None, "last": ts,
                                     "reasoning": [], "prompt": []})
        s["last"] = max(s["last"], ts)
        if event == "start":
            s["start"] = ts
            s["arm"] = d.get("arm", s["arm"])
        elif event == "end":
            s["end"] = ts
        elif event == "usage":
            if isinstance(d.get("prompt_tokens"), (int, float)):
                s["prompt"].append(d["prompt_tokens"])
            if isinstance(d.get("reasoning_tokens"), (int, float)):
                s["reasoning"].append(d["reasoning_tokens"])
    return {k: v for k, v in sessoes.items() if v["start"] is not None}


_ESPERA = re.compile(r"^(\S+)\s.*?(\{\"espera\".*\})\s*$")


def parse_espera(lines):
    saida = []
    for linha in lines:
        m = _ESPERA.match(linha.strip())
        if not m:
            continue
        try:
            ts = datetime.fromisoformat(m.group(1))
            valor = json.loads(m.group(2))["espera"]["espera_resposta"]
        except (ValueError, KeyError, TypeError):
            continue
        if isinstance(valor, (int, float)):
            saida.append((ts, float(valor)))
    return saida


def join(sessions, esperas):
    bracos = {a: {"espera": [], "reasoning": [], "prompt": [], "n": 0} for a in ("off", "on")}
    for s in sessions.values():
        b = bracos.get(s["arm"])
        if b is None:
            continue
        b["n"] += 1
        b["reasoning"] += s["reasoning"]
        b["prompt"] += s["prompt"]
        fim = s["end"] or s.get("last") or s["start"]
        b["espera"] += [v for ts, v in esperas if s["start"] <= ts <= fim]
    return bracos


def _fmt(x):
    return f"{x:.2f}".replace(".", ",")


def verdict(off, on):
    if off["n"] < MIN_SESSIONS or on["n"] < MIN_SESSIONS:
        return {"pass": None, "reasons": [f"amostra insuficiente: off {off['n']}, on {on['n']} "
                                         f"conversas (mínimo {MIN_SESSIONS} por braço)"]}
    if not off["espera"] or not on["espera"] or not off["reasoning"] or not on["reasoning"]:
        return {"pass": None, "reasons": ["amostra insuficiente: faltam medidas de espera ou de raciocínio"]}
    razoes = []
    dm = median(on["espera"]) - median(off["espera"])
    if dm > MAX_MEDIAN_ESPERA_S:
        razoes.append(f"mediana da espera {_fmt(dm)} s maior no braço com vistas (limite 0,3 s)")
    d90 = p90(on["espera"]) - p90(off["espera"])
    if d90 > MAX_P90_ESPERA_S:
        razoes.append(f"p90 da espera {_fmt(d90)} s maior no braço com vistas (limite 0,5 s)")
    base = median(off["reasoning"])
    if base and median(on["reasoning"]) > base * (1 + MAX_REASONING_RATIO):
        razoes.append(f"mediana do raciocínio {median(on['reasoning']):.0f} contra {base:.0f} tokens "
                      f"(limite +15%)")
    return {"pass": not razoes, "reasons": razoes}


def _ssh(args, timeout=60):
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"{args[0]} falhou ({r.returncode}): {r.stderr.strip()[:200]}")
    return r.stdout


def main():
    ab_linhas = _ssh(["ssh", VM, "sudo cat /srv/friday/state/memory-views-ab.ndjson 2>/dev/null || true"]).splitlines()
    sessoes = parse_ab(ab_linhas)
    esperas = []
    if sessoes:
        desde = min(s["start"] for s in sessoes.values()).strftime("%Y-%m-%d %H:%M:%S")
        diario = _ssh(["ssh", "-i", PI_KEY, "-o", "BatchMode=yes", PI, "journalctl", "-u", "friday-satelite",
                       "--since", f"'{desde}'", "-o", "short-iso-precise", "--no-pager"], timeout=120)
        esperas = parse_espera([l for l in diario.splitlines() if '"espera"' in l])
    bracos = join(sessoes, esperas)
    print(f"{'braço':<6}{'conversas':>10}{'esperas':>9}{'espera med':>12}{'espera p90':>12}{'raciocínio med':>16}{'prompt med':>12}")
    for nome in ("off", "on"):
        b = bracos[nome]
        f = lambda x, d=2: "-" if x is None else f"{x:.{d}f}".replace(".", ",")
        print(f"{nome:<6}{b['n']:>10}{len(b['espera']):>9}{f(median(b['espera'])):>12}{f(p90(b['espera'])):>12}"
              f"{f(median(b['reasoning']), 0):>16}{f(median(b['prompt']), 0):>12}")
    v = verdict(bracos["off"], bracos["on"])
    if v["pass"] is None:
        print("Veredito: amostra insuficiente. " + "; ".join(v["reasons"]))
    elif v["pass"]:
        print("Veredito: APROVADO. As vistas não pioraram a espera nem o raciocínio dentro dos limites.")
    else:
        print("Veredito: REPROVADO. " + "; ".join(v["reasons"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
