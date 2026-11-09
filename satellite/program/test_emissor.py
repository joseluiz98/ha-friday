"""cd /homeassistant/tools/satelite/programa && python3 -m unittest test_emissor -v"""
import time, unittest
import emissor


class TestEmissor(unittest.TestCase):
    def test_emitir_nunca_levanta_sem_agente(self):
        e = emissor.Emissor("http://127.0.0.1:1", "senha")  # porta fechada
        t = time.monotonic()
        for _ in range(20):
            e.emitir("conversa", {"estado": "aberta"})
        self.assertLess((time.monotonic() - t) / 20, 0.05)

    def test_emitir_nunca_levanta_com_dados_estranhos(self):
        e = emissor.Emissor("http://127.0.0.1:1", "s")
        e.emitir("config", {"x": object()})
        e.emitir(None, None)

    def test_fila_descarta_o_mais_velho(self):
        e = emissor.Emissor("http://127.0.0.1:1", "s", iniciar_thread=False)
        for i in range(60):
            e.emitir("conversa", {"n": i})
        self.assertEqual(len(e._fila), 50)
        self.assertEqual(e._fila[0][1]["n"], 10)
        self.assertEqual(e._fila[-1][1]["n"], 59)

    def test_emitir_desligado_com_url_nula(self):
        e = emissor.Emissor(None, "s", iniciar_thread=False)
        e.emitir("conversa", {"estado": "aberta"})
        self.assertEqual(len(e._fila), 0)

    def test_envia_post_com_bearer(self):
        vistos = []
        e = emissor.Emissor("http://127.0.0.1:8701", "segredo", enviar=lambda url, corpo, token: vistos.append((url, corpo, token)))
        e.emitir("conversa", {"estado": "fechada"})
        for _ in range(100):
            if vistos:
                break
            time.sleep(0.02)
        self.assertEqual(vistos[0][0], "http://127.0.0.1:8701/v1/local/event")
        self.assertEqual(vistos[0][2], "segredo")
        self.assertEqual(vistos[0][1]["tipo"], "conversa")
        self.assertEqual(vistos[0][1]["dados"], {"estado": "fechada"})


if __name__ == "__main__":
    unittest.main()
