"""Porta de status da VM (spec 2026-10-18 §4.10). cd /opt/friday/status && python3 -m unittest -q"""
import json, os, sqlite3, tempfile, threading, unittest, urllib.error, urllib.request

import friday_status as fs

TOKEN = "t" * 40


class Deps:
    def __init__(self, log="", ready=True, activity=None):
        self.log, self._ready, self.restarts, self._activity = log, ready, 0, activity

    def ready(self): return self._ready
    def last_dream(self): return "2026-10-20T05:00:00.000Z"
    def restarted(self): return False
    def warm_brain(self): return {"key": "on", "patched": True, "at": "2026-10-20T01:00:00-03:00"}
    def lucid(self): return {"date": "2026-10-20", "status": "ok"}
    def memory_views(self): return {"mode": "ab", "sessions": {"off": 3, "on": 2}, "since": "2026-10-22T09:00:00-03:00"}
    def memory_watcher(self): return {"degraded": False, "since": None, "reason": None}
    def meminfo(self): return "MemTotal: 11534336 kB\nMemAvailable: 6291456 kB\n"
    def log_tail(self): return self.log
    def activity(self): return self._activity
    def restart(self): self.restarts += 1


class Base(unittest.TestCase):
    def sobe(self, deps):
        self.srv = fs.make_server(0, TOKEN, deps)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def chamar(self, metodo, rota, chave=TOKEN):
        req = urllib.request.Request(self.base + rota, method=metodo, data=b"" if metodo == "POST" else None)
        if chave is not None:
            req.add_header("Authorization", f"Bearer {chave}")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()


class TestServidor(Base):
    def test_sem_chave_401(self):
        self.sobe(Deps())
        self.assertEqual(self.chamar("GET", "/v1/status", chave=None), (401, '{"error": "unauthorized"}'))

    def test_chave_errada_401(self):
        self.sobe(Deps())
        self.assertEqual(self.chamar("GET", "/v1/status", chave="x" * 40), (401, '{"error": "unauthorized"}'))
        self.assertEqual(self.chamar("POST", "/v1/restart", chave="x" * 40)[0], 401)

    def test_status_ok(self):
        self.sobe(Deps())
        codigo, corpo = self.chamar("GET", "/v1/status")
        self.assertEqual(codigo, 200)
        self.assertEqual(corpo, '{"ready": true, "last_dream": "2026-10-20T05:00:00.000Z", "restarted_in_process": false, '
                                '"warm_brain": {"key": "on", "patched": true, "at": "2026-10-20T01:00:00-03:00"}, '
                                '"memory": {"total_mb": 11264, "available_mb": 6144}, "openclaw_version": "2026.9.9", '
                                '"lucid": {"date": "2026-10-20", "status": "ok"}, "memory_views": {"mode": "ab", '
                                '"sessions": {"off": 3, "on": 2}, "since": "2026-10-22T09:00:00-03:00"}, '
                                '"memory_watcher": {"degraded": false, "since": null, "reason": null}}')

    def test_status_traz_lucid_e_memory_views(self):
        self.sobe(Deps())
        d = json.loads(self.chamar("GET", "/v1/status")[1])
        self.assertEqual(d["lucid"], {"date": "2026-10-20", "status": "ok"})
        self.assertEqual(d["memory_views"], {"mode": "ab", "sessions": {"off": 3, "on": 2},
                                             "since": "2026-10-22T09:00:00-03:00"})

    def test_restart_com_conversa_aberta_409(self):
        d = Deps(log="x\n... cliente conectado\n"); self.sobe(d)
        self.assertEqual(self.chamar("POST", "/v1/restart"), (409, '{"error": "conversation_open"}'))
        self.assertEqual(d.restarts, 0)

    def test_restart_sem_log_recusa_409(self):
        # sem como provar que não há conversa aberta, não reinicia (falha fechada)
        d = Deps(log=None); self.sobe(d)
        self.assertEqual(self.chamar("POST", "/v1/restart"), (409, '{"error": "log_unavailable"}'))
        self.assertEqual(d.restarts, 0)

    def test_restart_sem_conversa_202(self):
        d = Deps(log="cliente conectado\ncliente desconectado\n"); self.sobe(d)
        self.assertEqual(self.chamar("POST", "/v1/restart"), (202, '{"restarting": true}'))
        self.assertEqual(d.restarts, 1)

    def test_restart_com_duas_sessoes_e_uma_fechada_409(self):
        """Minor 13: o log dizia "fechada" com a 2ª sessão ainda aberta."""
        import time
        d = Deps(log="cliente conectado\ncliente conectado\ncliente desconectado\n",
                 activity={"updated_at": time.time(), "sessions": ["voz-2"], "brain_turns": 0}); self.sobe(d)
        self.assertEqual(self.chamar("POST", "/v1/restart"), (409, '{"error": "conversation_open"}'))
        self.assertEqual(d.restarts, 0)

    def test_restart_com_turno_do_cerebro_409(self):
        import time
        d = Deps(activity={"updated_at": time.time(), "sessions": [], "brain_turns": 1}); self.sobe(d)
        self.assertEqual(self.chamar("POST", "/v1/restart"), (409, '{"error": "conversation_open"}'))

    def test_restart_depois_de_crash_202(self):
        """Minor 13: crash no meio da sessão segurava o reinício por até 6 h (log com "conectado" sem par)."""
        d = Deps(log="cliente conectado\n",
                 activity={"updated_at": 1.0, "sessions": ["voz-1"], "brain_turns": 1}); self.sobe(d)
        self.assertEqual(self.chamar("POST", "/v1/restart"), (202, '{"restarting": true}'))
        self.assertEqual(d.restarts, 1)

    def test_rota_desconhecida_404(self):
        self.sobe(Deps())
        self.assertEqual(self.chamar("GET", "/v1/outra"), (404, '{"error": "not_found"}'))


class TestLucidEVistas(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def _w(self, nome, texto):
        p = os.path.join(self.dir, nome)
        with open(p, "w", encoding="utf-8") as f:
            f.write(texto)
        return p

    def test_lucid_last_ausente_none(self):
        self.assertIsNone(fs.lucid_last(os.path.join(self.dir, "nao.ndjson")))

    def test_lucid_last_devolve_a_ultima(self):
        p = self._w("r.ndjson", '{"date": "2026-10-19", "status": "ok"}\n{"date": "2026-10-20", "status": "run_failed"}\n')
        self.assertEqual(fs.lucid_last(p), {"date": "2026-10-20", "status": "run_failed"})

    def test_lucid_last_ignora_linha_quebrada_e_vazio(self):
        p = self._w("r.ndjson", '{"date": "2026-10-19"}\n{quebrada\n\n')
        self.assertEqual(fs.lucid_last(p), {"date": "2026-10-19"})
        self.assertIsNone(fs.lucid_last(self._w("v.ndjson", "")))

    def test_memory_views_modo_ausente_ou_invalido_e_off(self):
        ab = os.path.join(self.dir, "ab.ndjson")
        r = fs.memory_views(ab, os.path.join(self.dir, "modo"))
        self.assertEqual(r, {"mode": "off", "sessions": {"off": 0, "on": 0}, "since": None})
        self.assertEqual(fs.memory_views(ab, self._w("modo", "xyz\n"))["mode"], "off")
        self.assertEqual(fs.memory_views(ab, self._w("modo2", " ab\n"))["mode"], "ab")

    def test_memory_views_conta_start_por_braco(self):
        linhas = [
            {"event": "start", "ts": "2026-10-22T09:00:00-03:00", "session": "a", "arm": "off"},
            {"event": "usage", "ts": "2026-10-22T09:00:05-03:00", "session": "a", "arm": "off"},
            {"event": "start", "ts": "2026-10-22T10:00:00-03:00", "session": "b", "arm": "on"},
            {"event": "start", "ts": "2026-10-22T11:00:00-03:00", "session": "c", "arm": "on"},
            {"event": "end", "ts": "2026-10-22T11:05:00-03:00", "session": "c", "arm": "on"},
        ]
        ab = self._w("ab.ndjson", "\n".join(json.dumps(l) for l in linhas) + "\n{quebrada\n")
        r = fs.memory_views(ab, self._w("modo", "on"))
        self.assertEqual(r, {"mode": "on", "sessions": {"off": 1, "on": 2}, "since": "2026-10-22T09:00:00-03:00"})


class TestFuncoesPuras(unittest.TestCase):
    def test_last_dream_de_um_sqlite(self):
        db = os.path.join(tempfile.mkdtemp(), "o.sqlite")
        con = sqlite3.connect(db)
        con.execute("create table plugin_state_entries(plugin_id text, namespace text, value_json text)")
        con.execute("insert into plugin_state_entries values (?,?,?)", ("memory-core", "short-term-meta",
                    '{"key":"phase","value":{"updatedAt":"2026-10-20T05:00:00.000Z"}}'))
        con.commit(); con.close()
        self.assertEqual(fs.last_dream(db), "2026-10-20T05:00:00.000Z")

    def test_last_dream_sem_banco_none(self):
        self.assertIsNone(fs.last_dream("/nao/existe/o.sqlite"))

    def test_conversation_open_casos(self):
        self.assertFalse(fs.conversation_open(""))
        self.assertFalse(fs.conversation_open("a\ncliente desconectado\nb\n"))
        self.assertTrue(fs.conversation_open("cliente desconectado\ncliente conectado\n"))

T1, T2, T3 = "2026-11-17T08:00:00.000Z", "2026-11-17T20:31:32.857Z", "2026-11-18T12:22:49.101Z"
AVISO = "memory watcher unavailable; memory will refresh on search: FsSafeError: root path changed"


def L(date, subsystem, msg):
    """Linha do log do gateway no formato real: "0" é uma string JSON com o subsistema."""
    return json.dumps({"0": json.dumps({"subsystem": subsystem}), "1": msg, "_meta": {"date": date}})


def partida(date):
    return L(date, "gateway", "starting...")


class TestMemoryWatcher(unittest.TestCase):
    def test_memory_watcher_sem_log_e_none(self):
        saved = {"started": "S", "since": "X", "reason": "r"}
        self.assertEqual(fs.memory_watcher(None, saved), (None, saved))

    def test_memory_watcher_saudavel(self):
        self.assertEqual(fs.memory_watcher([partida(T1)], None),
                         ({"degraded": False, "since": None, "reason": None},
                          {"started": T1, "since": None, "reason": None}))

    def test_memory_watcher_degradado_depois_da_partida(self):
        r, novo = fs.memory_watcher([partida(T1), L(T2, "memory", AVISO)], None)
        self.assertTrue(r["degraded"])
        self.assertEqual(r["since"], T2)
        self.assertTrue(r["reason"].startswith("memory watcher unavailable"))
        self.assertEqual(novo, {"started": T1, "since": T2, "reason": r["reason"]})

    def test_memory_watcher_aviso_da_vida_anterior_nao_conta(self):
        r, _ = fs.memory_watcher([L(T1, "memory", AVISO), partida(T2)], None)
        self.assertFalse(r["degraded"])

    def test_memory_watcher_outro_subsistema_nao_conta(self):
        r, _ = fs.memory_watcher([partida(T1), L(T2, "gateway/ws", AVISO)], None)
        self.assertFalse(r["degraded"])

    def test_memory_watcher_trava_sem_linha_de_partida(self):
        saved = {"started": T1, "since": T2, "reason": "r"}
        r, novo = fs.memory_watcher([L(T3, "gateway", "outra coisa")], saved)
        self.assertEqual(r, {"degraded": True, "since": T2, "reason": "r"})
        self.assertEqual(novo, saved)

    def test_memory_watcher_partida_nova_destrava(self):
        saved = {"started": T1, "since": T2, "reason": "r"}
        r, novo = fs.memory_watcher([partida(T3)], saved)
        self.assertFalse(r["degraded"])
        self.assertEqual(novo["started"], T3)

    def test_memory_watcher_trava_com_started_none(self):
        saved = {"started": None, "since": T2, "reason": "r"}
        r, _ = fs.memory_watcher([L(T3, "gateway", "outra coisa")], saved)
        self.assertEqual(r, {"degraded": True, "since": T2, "reason": "r"})

    def test_watcher_lines_from_so_100_e_101_sao_leitura(self):
        self.assertEqual(fs._watcher_lines_from(100, "a\nb\n"), ["a", "b"])
        self.assertEqual(fs._watcher_lines_from(101, ""), [])
        for rc in (0, 1, 2, 3, 125, 126, 127):
            self.assertIsNone(fs._watcher_lines_from(rc, ""), rc)

    def test_watcher_cmd_classifica_no_container(self):
        """Roda o comando num shell local com um /tmp/openclaw falso (só o shell e o grep, sem docker)."""
        import subprocess
        d = tempfile.mkdtemp()
        cmd = fs.WATCHER_CMD.replace("/tmp/openclaw", d)
        rodar = lambda: subprocess.run(["sh", "-c", cmd], stdout=subprocess.PIPE, text=True).returncode
        self.assertEqual(rodar(), 3)                       # sem arquivo de log
        with open(os.path.join(d, "openclaw-1.log"), "w") as f:
            f.write("nada de interesse\n")
        self.assertEqual(rodar(), 101)                     # leu, nada casou
        with open(os.path.join(d, "openclaw-1.log"), "a") as f:
            f.write("memory watcher unavailable\n")
        self.assertEqual(rodar(), 100)                     # casou

    def test_memory_watcher_ignora_lixo(self):
        lixo = ["nao json", '{"0": "No change", "_meta": {"date": "2026-11-18T00:00:00Z"}}', "{}"]
        for saved in (None, {"started": None, "since": None, "reason": None}):
            r, _ = fs.memory_watcher(lixo + [partida(T1)], saved)
            self.assertFalse(r["degraded"])

    def test_memory_watcher_reason_cortado(self):
        r, _ = fs.memory_watcher([partida(T1), L(T2, "memory", "memory watcher unavailable " + "x" * 300)], None)
        self.assertEqual(len(r["reason"]), 160)

    def test_memory_watcher_capacidade_e_polling_contam(self):
        for msg in ("memory watcher capacity exhausted: 8192", "memory watcher using fallback polling"):
            r, _ = fs.memory_watcher([partida(T1), L(T2, "memory", msg)], None)
            self.assertTrue(r["degraded"], msg)


if __name__ == "__main__":
    unittest.main()
