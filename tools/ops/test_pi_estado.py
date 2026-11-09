"""pi_estado.py funciona antes e depois da virada (spec 2026-10-18 §4.11): tenta /status e cai para /estado se a Pi
ainda for a versão antiga. cd /homeassistant/tools/satelite && python3 -m unittest -q test_pi_estado"""
import contextlib, http.server, io, json, os, tempfile, threading, unittest, urllib.error

import pi_estado

ESTADO = {"estado": "parada", "timers": [], "cadastro": None, "alarme": None}
SENHA = "s" * 40


def servidor(rotas):
    """Servidor que só conhece `rotas`; as outras dão 404. Registra os caminhos pedidos."""
    pedidos = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            pedidos.append(self.path)
            if self.path in rotas and self.headers.get("Authorization") == f"Bearer {SENHA}":
                corpo = json.dumps(ESTADO).encode()
                self.send_response(200); self.send_header("Content-Length", str(len(corpo))); self.end_headers()
                self.wfile.write(corpo)
            else:
                self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, pedidos


class TestPiEstado(unittest.TestCase):
    def sobe(self, rotas):
        srv, pedidos = servidor(rotas)
        self.addCleanup(srv.server_close); self.addCleanup(srv.shutdown)
        return f"http://127.0.0.1:{srv.server_address[1]}", pedidos

    def test_pi_antiga_so_conhece_estado(self):
        base, pedidos = self.sobe({"/estado"})
        self.assertEqual(pi_estado._get(base, SENHA, "/status"), ESTADO)
        self.assertEqual(pedidos, ["/status", "/estado"])

    def test_pi_nova_so_conhece_state(self):
        base, pedidos = self.sobe({"/status"})
        self.assertEqual(pi_estado._get(base, SENHA, "/status"), ESTADO)
        self.assertEqual(pedidos, ["/status"])

    def test_outro_erro_nao_cai_para_estado(self):
        base, pedidos = self.sobe(set())      # 404 nos dois: o erro sobe
        with self.assertRaises(urllib.error.HTTPError):
            pi_estado._get(base, SENHA, "/status")

    def test_main_imprime_resumo_sem_a_senha(self):
        d = tempfile.mkdtemp(); arq = os.path.join(d, "satellites.json")
        json.dump({"sat-0a1b2c": {"comodo": "x", "senha": SENHA, "ip": "192.0.2.15"}}, open(arq, "w"))
        antes = (pi_estado.SATELITES, pi_estado._get)
        pi_estado.SATELITES = arq
        pi_estado._get = lambda base, senha, rota: ESTADO
        self.addCleanup(lambda: (setattr(pi_estado, "SATELITES", antes[0]), setattr(pi_estado, "_get", antes[1])))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            pi_estado.main(False)
        saida = buf.getvalue()
        self.assertEqual(json.loads(saida)["estado"], "parada")
        self.assertNotIn(SENHA, saida)

    def test_satelites_so_no_caminho_de_producao(self):
        """Desde a Task 13 (19/10) a pasta da POC está vazia: o cadastro mora só em /share/friday."""
        self.assertEqual(pi_estado.SATELITES, "/share/friday/satellites.json")
        self.assertNotIn("openclaw-poc", open(pi_estado.__file__).read())


if __name__ == "__main__":
    unittest.main()
