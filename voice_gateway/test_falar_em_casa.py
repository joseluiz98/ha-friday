import unittest
"""Script da skill falar-em-casa contra um /deliver simulado (spec 2026-10-12 §3):
cd /srv/friday/app && python3 -m unittest test_falar_em_casa -v"""
import http.server, json, os, subprocess, tempfile, threading, unittest

SCRIPT = os.environ.get("FALAR_SCRIPT", "/srv/friday/home/.openclaw/workspace/friday/skills/falar-em-casa/entregar.sh")


class Falso(http.server.BaseHTTPRequestHandler):
    recebidos = []

    def do_POST(self):
        corpo = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Falso.recebidos.append((self.path, self.headers.get("Authorization"), corpo))
        ok = self.headers.get("Authorization") == "Bearer segredo"
        self.send_response(200 if ok else 401)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"resultado": "entregue"} if ok else {"erro": "não autorizado"}).encode())

    def log_message(self, *a):
        pass


@unittest.skipUnless(os.path.exists(SCRIPT), "needs the production brain workspace (runs on the VM: scripts/test-all.sh)")
class TestScript(unittest.TestCase):
    def setUp(self):
        Falso.recebidos.clear()
        self.srv = http.server.HTTPServer(("127.0.0.1", 0), Falso)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.d = tempfile.mkdtemp()
        self.token = os.path.join(self.d, "token")
        with open(self.token, "w") as f:
            f.write("segredo\n")

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def rodar(self, *args, token=None):
        env = dict(os.environ, FALAR_URL=f"http://127.0.0.1:{self.srv.server_port}/deliver",
                   FALAR_TOKEN=token or self.token)
        return subprocess.run(["sh", SCRIPT, *args], capture_output=True, text=True, env=env, timeout=30)

    def test_manda_json_com_aspas_e_acentos_sem_quebrar(self):
        r = self.rodar("jose", "lembrete", 'Tirar o "bolo" do forno às 15h')
        self.assertEqual(json.loads(r.stdout), {"resultado": "entregue"})
        _, auth, corpo = Falso.recebidos[0]
        self.assertEqual(auth, "Bearer segredo")
        self.assertEqual(corpo, {"destino": "jose", "tipo": "lembrete", "texto": 'Tirar o "bolo" do forno às 15h'})

    def test_token_nao_aparece_na_saida(self):
        r = self.rodar("jose", "aviso", "x")
        self.assertNotIn("segredo", r.stdout + r.stderr)

    def test_add_on_fora_do_ar_vira_erro_em_json(self):
        env = dict(os.environ, FALAR_URL="http://127.0.0.1:9/deliver", FALAR_TOKEN=self.token)
        r = subprocess.run(["sh", SCRIPT, "jose", "aviso", "x"], capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(json.loads(r.stdout)["resultado"], "erro")

    def test_uso_errado(self):
        r = self.rodar("jose")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(Falso.recebidos, [])
