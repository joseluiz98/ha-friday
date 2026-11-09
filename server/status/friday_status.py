#!/usr/bin/env python3
"""Porta de status da VM da F.R.I.D.A.Y. (spec 2026-10-18 §4.10). Só a stdlib.

GET  /v1/status   estado da F.R.I.D.A.Y. (pronta, último sonho, reinício no mesmo processo, cérebro quente, RAM,
                  `lucid` = última linha do runs.ndjson do Lucid Dreaming ou null, `memory_views` = modo
                  off|ab|on e quantas conversas cada braço do A/B já teve; spec 2026-10-21)
POST /v1/restart  reinicia o container, recusando (409) se houver conversa aberta ou turno do cérebro a caminho
                  (arquivo de atividade do bot; sem ele, o log do container)
Toda rota exige `Authorization: Bearer <chave>`; a chave vem de FRIDAY_STATUS_TOKEN_FILE e nunca é registrada."""
import hmac
import json
import os
import re
import sqlite3
import ssl
import subprocess
import sys
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OPENCLAW_VERSION = "2026.9.9"
COMPOSE = "/opt/friday/image/docker-compose.yml"


# ---- funções puras ----------------------------------------------------------------------------------------------

def last_dream(db_path):
    """Maior value.updatedAt das linhas com key == "phase" (mesma consulta do openclaw_sonho_check.py), ou None."""
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        try:
            carimbos = []
            for (valor,) in con.execute(
                "select value_json from plugin_state_entries "
                "where plugin_id = 'memory-core' and namespace = 'short-term-meta'"
            ):
                dado = json.loads(valor)
                if dado.get("key") == "phase":
                    carimbos.append((dado.get("value") or {}).get("updatedAt"))
        finally:
            con.close()
        carimbos = [c for c in carimbos if c]
        return max(carimbos) if carimbos else None
    except (sqlite3.Error, ValueError, OSError, AttributeError, TypeError):
        return None


def memory_from_meminfo(text):
    """MemTotal e MemAvailable (kB em /proc/meminfo) em MB."""
    valores = {}
    for linha in text.splitlines():
        m = re.match(r"(MemTotal|MemAvailable):\s+(\d+)\s*kB", linha)
        if m:
            valores[m.group(1)] = int(m.group(2))
    return {"total_mb": valores.get("MemTotal", 0) // 1024, "available_mb": valores.get("MemAvailable", 0) // 1024}


def conversation_open(log_text):
    """Última linha com "cliente conectado" ou "cliente desconectado": aberta só se for a primeira.
    ("cliente desconectado" não contém "cliente conectado".)"""
    aberta = False
    for linha in log_text.splitlines():
        if "cliente desconectado" in linha:
            aberta = False
        elif "cliente conectado" in linha:
            aberta = True
    return aberta


ACTIVITY_STALE_S = 90


def busy_from_activity(activity, now):
    """Minor 13 de 19/10: o bot grava /srv/friday/state/activity.json (conversas abertas, turnos do cérebro a
    caminho, renovação a cada 20 s). Ocupado com conversa OU turno do cérebro; renovação velha = bot fora = livre.
    None se o arquivo faltar ou vier quebrado (aí vale a regra do log). Mesma regra do agente do HA
    (collectors.activity_view/is_busy)."""
    if not isinstance(activity, dict):
        return None
    try:
        idade = now - float(activity["updated_at"])
        sessoes = len(activity.get("sessions") or [])
        cerebro = int(activity.get("brain_turns") or 0)
    except (KeyError, TypeError, ValueError):
        return None
    if idade > ACTIVITY_STALE_S:
        return False
    return sessoes > 0 or cerebro > 0


def warm_brain(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def lucid_last(path):
    """Última linha JSON válida do runs.ndjson do Lucid Dreaming, ou None."""
    try:
        with open(path, encoding="utf-8") as f:
            linhas = f.read().splitlines()
    except OSError:
        return None
    for linha in reversed(linhas):
        linha = linha.strip()
        if not linha:
            continue
        try:
            dado = json.loads(linha)
        except ValueError:
            continue
        if isinstance(dado, dict):
            return dado
    return None


def memory_views(ab_path, mode_path):
    """Modo das vistas de memória (off|ab|on; qualquer outra coisa é off) e conversas por braço (eventos start)."""
    try:
        with open(mode_path, encoding="utf-8") as f:
            modo = f.read().strip()
    except OSError:
        modo = "off"
    if modo not in ("off", "ab", "on"):
        modo = "off"
    sessoes, desde = {"off": 0, "on": 0}, None
    try:
        with open(ab_path, encoding="utf-8") as f:
            for linha in f:
                try:
                    dado = json.loads(linha)
                except ValueError:
                    continue
                if not isinstance(dado, dict) or dado.get("event") != "start":
                    continue
                if dado.get("arm") in sessoes:
                    sessoes[dado["arm"]] += 1
                if desde is None:
                    desde = dado.get("ts")
    except OSError:
        pass
    return {"mode": modo, "sessions": sessoes, "since": desde}


# ---- servidor ---------------------------------------------------------------------------------------------------

def _json(handler, codigo, dado):
    corpo = json.dumps(dado).encode()
    handler.send_response(codigo)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(corpo)))
    handler.end_headers()
    handler.wfile.write(corpo)


def make_server(port, token, deps):
    esperado = (token or "").encode()

    class Handler(BaseHTTPRequestHandler):
        server_version = "friday-status"

        def log_message(self, fmt, *args):  # nunca registra cabeçalhos; só método e rota
            sys.stderr.write("%s %s\n" % (self.command, self.path.split("?")[0]))

        def _autorizado(self):
            cab = self.headers.get("Authorization") or ""
            if not esperado or not cab.startswith("Bearer "):
                return False
            return hmac.compare_digest(cab[len("Bearer "):].encode(), esperado)

        def _tratar(self):
            if not self._autorizado():          # antes de qualquer rota
                return _json(self, 401, {"error": "unauthorized"})
            rota = self.path.split("?")[0]
            if self.command == "GET" and rota == "/v1/status":
                return _json(self, 200, {
                    "ready": bool(deps.ready()),
                    "last_dream": deps.last_dream(),
                    "restarted_in_process": bool(deps.restarted()),
                    "warm_brain": deps.warm_brain(),
                    "memory": memory_from_meminfo(deps.meminfo()),
                    "openclaw_version": OPENCLAW_VERSION,
                    "lucid": deps.lucid(),
                    "memory_views": deps.memory_views(),
                })
            if self.command == "POST" and rota == "/v1/restart":
                ocupado = busy_from_activity(deps.activity(), time.time())
                if ocupado is not None:
                    if ocupado:
                        return _json(self, 409, {"error": "conversation_open"})
                    deps.restart()
                    return _json(self, 202, {"restarting": True})
                log = deps.log_tail()
                if log is None:  # sem o log não há prova de que a conversa acabou: falha fechada
                    return _json(self, 409, {"error": "log_unavailable"})
                if conversation_open(log):
                    return _json(self, 409, {"error": "conversation_open"})
                deps.restart()
                return _json(self, 202, {"restarting": True})
            return _json(self, 404, {"error": "not_found"})

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = _tratar

    return ThreadingHTTPServer(("0.0.0.0", port), Handler)


# ---- implementações reais ---------------------------------------------------------------------------------------

class RealDeps:
    def __init__(self):
        self.db = os.environ.get("FRIDAY_STATE_DB", "/srv/friday/home/.openclaw/state/openclaw.sqlite")
        self.mark = os.environ.get("FRIDAY_RESTART_MARK", "/srv/friday/state/restarted-in-process")
        self.warm = os.environ.get("FRIDAY_WARM_BRAIN", "/srv/friday/state/warm-brain.json")
        self.lucid_runs = os.environ.get("FRIDAY_LUCID_RUNS", "/srv/friday/state/cataloger/state/runs.ndjson")
        self.views_ab = os.environ.get("FRIDAY_MEMORY_VIEWS_AB", "/srv/friday/state/memory-views-ab.ndjson")
        self.views_mode = os.environ.get("FRIDAY_MEMORY_VIEWS_MODE", "/srv/friday/state/memory-views-mode")
        self.activity_path = os.environ.get("FRIDAY_ACTIVITY_FILE", "/srv/friday/state/activity.json")

    def activity(self):
        try:
            with open(self.activity_path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def ready(self):
        try:
            with urllib.request.urlopen("https://127.0.0.1:18789/readyz", timeout=3,
                                        context=ssl._create_unverified_context()) as r:
                return bool(json.load(r).get("ready"))
        except Exception:
            return False

    def last_dream(self):
        return last_dream(self.db)

    def restarted(self):
        return os.path.exists(self.mark)

    def warm_brain(self):
        return warm_brain(self.warm)

    def lucid(self):
        return lucid_last(self.lucid_runs)

    def memory_views(self):
        return memory_views(self.views_ab, self.views_mode)

    def meminfo(self):
        try:
            with open("/proc/meminfo") as f:
                return f.read()
        except OSError:
            return ""

    def log_tail(self):
        try:
            r = subprocess.run(["docker", "logs", "--since", "6h", "friday"], stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, timeout=15, text=True, errors="replace")
            return r.stdout if r.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            return None

    def restart(self):
        subprocess.Popen(["docker", "compose", "-f", COMPOSE, "restart", "friday"], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def main():
    arq = os.environ.get("FRIDAY_STATUS_TOKEN_FILE", "/srv/friday/secrets/status_token")
    try:
        with open(arq) as f:
            token = f.read().strip()
    except OSError:
        token = ""
    if not token:
        print(f"falta a chave de status em {arq}", file=sys.stderr)
        return 1
    porta = int(os.environ.get("FRIDAY_STATUS_PORT", "8800"))
    make_server(porta, token, RealDeps()).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
