import asyncio
import time
import unittest

import inventario_casa as ic

RESPOSTA = {"jsonrpc": "2.0", "id": 1, "result": {"messages": [
    {"role": "assistant", "content": {"type": "text", "text": "Static Context:\n- names: Luz Escritório\n  domain: light\n"}}]}}


class TestExtrair(unittest.TestCase):
    def test_junta_os_textos(self):
        r = {"result": {"messages": [{"content": {"type": "text", "text": "a"}}, {"content": {"type": "text", "text": " b "}}]}}
        self.assertEqual(ic.extrair_texto(r), "a\nb")

    def test_ignora_o_que_nao_e_texto(self):
        r = {"result": {"messages": [{"content": {"type": "image", "data": "x"}}, {"content": {"type": "text", "text": "a"}}]}}
        self.assertEqual(ic.extrair_texto(r), "a")

    def test_erro_jsonrpc_vira_vazio(self):
        self.assertEqual(ic.extrair_texto({"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "x"}}), "")

    def test_formato_inesperado_vira_vazio(self):
        for r in (None, [], "x", {"result": "x"}, {"result": {"messages": "x"}}, {"result": {"messages": [None, 3]}}):
            self.assertEqual(ic.extrair_texto(r), "")


FRASE_GRUPO = ("When a user asks to turn on all devices of a specific type, ask the user to specify an area, "
               "unless there is only one device of that type.")


class TestSemFraseDeGrupo(unittest.TestCase):
    def test_tira_a_frase_de_grupo(self):
        t = f"Primeira linha.\n{FRASE_GRUPO}\nTerceira linha."
        r = ic.sem_frase_de_grupo(t)
        self.assertNotIn("specify an area", r)
        self.assertIn("Primeira linha.", r)
        self.assertIn("Terceira linha.", r)
        self.assertEqual(len(r.splitlines()), 2)

    def test_sem_a_frase_fica_igual(self):
        self.assertEqual(ic.sem_frase_de_grupo("a\nb"), "a\nb")

    def test_vazio(self):
        self.assertEqual(ic.sem_frase_de_grupo(""), "")
        self.assertIsNone(ic.sem_frase_de_grupo(None))


class TestBuscar(unittest.TestCase):
    def test_sucesso(self):
        async def post(url, token, corpo):
            self.assertEqual(corpo["method"], "prompts/get")
            self.assertEqual(corpo["params"], {"name": "Assist"})
            return RESPOSTA
        texto, erro = asyncio.run(ic.buscar("http://x/mcp", "t", post=post))
        self.assertIn("Luz Escritório", texto)
        self.assertIsNone(erro)

    def test_lento_estoura_o_prazo_sem_atrasar_a_conversa(self):
        async def post(url, token, corpo):
            await asyncio.sleep(5)
            return RESPOSTA
        t0 = time.monotonic()
        texto, erro = asyncio.run(ic.buscar("http://x/mcp", "t", timeout=0.2, post=post))
        self.assertEqual(texto, "")
        self.assertEqual(erro, "TimeoutError")
        self.assertLess(time.monotonic() - t0, 1.0)

    def test_falha_de_rede(self):
        async def post(url, token, corpo):
            raise OSError("sem rede")
        self.assertEqual(asyncio.run(ic.buscar("http://x/mcp", "t", post=post)), ("", "OSError"))


class TestTimersDoHA(unittest.TestCase):
    """14/10: o prompt do Assist termina com "This device is not able to start timers." (o HA não tem timers neste
    "dispositivo"); no satélite a ferramenta timer existe e o Gemini obedeceu à frase: "não posso iniciar timers"."""

    def test_remove_so_a_frase_de_timers(self):
        texto = "Linha A\nThis device is not able to start timers.\nLinha B"
        self.assertEqual(ic.sem_frase_de_timers(texto), "Linha A\nLinha B")

    def test_sem_a_frase_nada_muda_e_o_vazio_segue_vazio(self):
        self.assertEqual(ic.sem_frase_de_timers("Linha A\nLinha B"), "Linha A\nLinha B")
        self.assertEqual(ic.sem_frase_de_timers(""), "")

    def test_frase_no_fim_sem_quebra_e_com_ponto_opcional(self):
        self.assertEqual(ic.sem_frase_de_timers("A\nThis device is not able to start timers"), "A")


if __name__ == "__main__":
    unittest.main()
