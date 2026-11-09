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
                                '"sessions": {"off": 3, "on": 2}, "since": "2026-10-22T09:00:00-03:00"}}')

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


if __name__ == "__main__":
    unittest.main()
