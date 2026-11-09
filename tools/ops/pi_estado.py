#!/usr/bin/env python3
"""Estado do satélite pela porta local (GET :8700/status), sem nunca imprimir a senha.
Uso: python3 -I pi_estado.py [--exige-parada]   (sai com 3 se não estiver parada com 0 timers).
Funciona antes e depois da virada para a VM (spec 2026-10-18 §4.11): a Pi antiga só conhece /estado, a nova /status.
O cadastro dos satélites mora em /share/friday/satellites.json (desde 19/10, a pasta da POC está vazia)."""
import json, os, sys, urllib.error, urllib.request

SATELITES = "/share/friday/satellites.json"


def _get(base, senha, rota):
    """GET <base><rota> com a senha da porta; se a Pi ainda for a versão antiga (404 em /status), tenta /estado."""
    def pedir(r):
        req = urllib.request.Request(base + r, headers={"Authorization": f"Bearer {senha}"})
        return json.load(urllib.request.urlopen(req, timeout=5))
    try:
        return pedir(rota)
    except urllib.error.HTTPError as e:
        if e.code == 404 and rota == "/status":
            return pedir("/estado")
        raise


def main(exige):
    sats = json.load(open(SATELITES))
    sid = sorted(sats)[0]
    e = _get(f"http://{sats[sid]['ip']}:8700", sats[sid]["senha"], "/status")
    resumo = {"satelite": sid, "estado": e.get("estado"), "timers": len(e.get("timers") or []),
              "cadastro": e.get("cadastro"), "alarme": e.get("alarme")}
    print(json.dumps(resumo, ensure_ascii=False))
    if exige and (resumo["estado"] != "parada" or resumo["timers"] or resumo["alarme"]):
        print("satélite ocupado: esperar", file=sys.stderr)
        sys.exit(3)


if __name__ == "__main__":
    main("--exige-parada" in sys.argv)
