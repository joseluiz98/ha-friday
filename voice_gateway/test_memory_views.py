"""Testes das vistas de memória da voz (spec 2026-10-21 §5.4–5.6). cd /srv/friday/app && python -m unittest -q test_memory_views"""
import io
import json
import os
import tempfile
import time
import unittest
from contextlib import redirect_stderr

import memory_views as mv

# Copiado de BRAIN_FUNCTION (bot.py) em 21/10: sem vistas, a descrição tem de ficar idêntica.
BRAIN_DESCRIPTION_ATUAL = (
    "Encaminha um pedido ao cérebro do assistente (agente OpenClaw com memória persistente, "
    "acesso ao homelab e capacidade de pesquisa e execução). Use para perguntas sobre o homelab, "
    "lembrar ou recuperar informações, raciocínio mais longo e tarefas."
)
PREFIXO = ("Encaminha um pedido ao cérebro do assistente (agente OpenClaw com memória persistente, acesso ao "
           "homelab e capacidade de pesquisa e execução). ")


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name

    def arq(self, nome, texto, idade_h=0.0):
        p = os.path.join(self.dir, nome)
        with open(p, "w", encoding="utf-8") as f:
            f.write(texto)
        t = time.time() - idade_h * 3600
        os.utime(p, (t, t))
        return p


class TestReadView(Base):
    def test_vista_ausente_velha_vazia(self):
        agora = time.time()
        self.assertEqual(mv.read_view(os.path.join(self.dir, "nao.md"), agora, 1200), "")
        self.assertEqual(mv.read_view(self.arq("velha.md", "fato", idade_h=49), agora, 1200), "")
        self.assertEqual(mv.read_view(self.arq("vazia.md", "  \n\n"), agora, 1200), "")

    def test_ilegivel_nao_levanta(self):
        p = os.path.join(self.dir, "bin.md")
        with open(p, "wb") as f:
            f.write(b"\xff\xfe\x00ruim")
        self.assertIsInstance(mv.read_view(p, time.time(), 1200), str)
        self.assertEqual(mv.read_view(self.dir, time.time(), 1200), "")  # uma pasta

    def test_dentro_de_48h_vale(self):
        self.assertEqual(mv.read_view(self.arq("ok.md", " fato um \n", idade_h=47), time.time(), 1200), "fato um")

    def test_corta_no_teto(self):
        texto = " ".join(["palavra"] * 300)  # 2.399 caracteres
        saida = mv.read_view(self.arq("g.md", texto[:1500] + " fim"), time.time(), 1200)
        self.assertLessEqual(len(saida), 1200)
        self.assertEqual(saida.split()[-1], "palavra")
        self.assertTrue(saida.startswith("palavra palavra"))

    def test_corta_quando_o_limite_cai_numa_palavra(self):
        saida = mv.read_view(self.arq("g2.md", "um dois três"), time.time(), 7)
        self.assertEqual(saida, "um dois")
        saida = mv.read_view(self.arq("g3.md", "um dois três"), time.time(), 10)
        self.assertEqual(saida, "um dois")


class TestTextos(unittest.TestCase):
    def test_briefing_context(self):
        self.assertEqual(mv.briefing_context(""), "")
        self.assertEqual(mv.briefing_context("- fato"),
                         "Fatos recentes da casa que o cérebro guardou de conversas anteriores "
                         "(podem não se aplicar a esta conversa):\n- fato")

    def test_description_sem_capacidades_e_identica(self):
        self.assertEqual(mv.brain_description(""), BRAIN_DESCRIPTION_ATUAL)

    def test_description_com_capacidades(self):
        d = mv.brain_description("O cérebro consegue pesquisar na web.")
        self.assertTrue(d.startswith(PREFIXO))
        self.assertTrue(d.endswith("O cérebro consegue pesquisar na web."))
        self.assertNotIn("Use para perguntas", d)  # substitui a lista escrita à mão, sem somar


class TestModo(Base):
    def test_modo_e_braco(self):
        self.assertEqual(mv.read_mode(os.path.join(self.dir, "nao")), "off")
        self.assertEqual(mv.read_mode(self.arq("m1", "xyz")), "off")
        self.assertEqual(mv.read_mode(self.arq("m2", " ab\n")), "ab")
        self.assertEqual(mv.read_mode(self.arq("m3", "on")), "on")
        self.assertEqual(mv.read_mode(self.arq("m4", "")), "off")
        self.assertEqual(mv.choose_arm("ab", 0.2), "on")
        self.assertEqual(mv.choose_arm("ab", 0.7), "off")
        self.assertEqual(mv.choose_arm("ab", 0.5), "off")
        self.assertEqual(mv.choose_arm("off", 0.1), "off")
        self.assertEqual(mv.choose_arm("on", 0.9), "on")
        self.assertEqual(mv.choose_arm("lixo", 0.1), "off")


class TestAbLog(Base):
    def test_ablog_grava_tres_eventos(self):
        p = os.path.join(self.dir, "ab.ndjson")
        log = mv.AbLog(p)
        log.start("voz-1", "on", "sat-0a1b2c")
        log.usage("voz-1", "on", 9000, 120)
        log.end("voz-1", "on")
        linhas = [json.loads(l) for l in open(p, encoding="utf-8")]
        self.assertEqual([l["event"] for l in linhas], ["start", "usage", "end"])
        self.assertTrue(all(l["arm"] == "on" and l["session"] == "voz-1" for l in linhas))
        self.assertTrue(linhas[0]["ts"].endswith("-03:00"))
        self.assertEqual((linhas[1]["prompt_tokens"], linhas[1]["reasoning_tokens"]), (9000, 120))
        self.assertEqual(linhas[0]["satellite"], "sat-0a1b2c")

    def test_erro_de_io_nao_derruba(self):
        log = mv.AbLog(os.path.join(self.dir, "pasta", "inexistente", "ab.ndjson"))  # pai não existe e não é criado
        err = io.StringIO()
        with redirect_stderr(err):
            log.start("s", "off", None)
            log.usage("s", "off", 1, 0)
            log.end("s", "off")
        self.assertEqual(len(err.getvalue().strip().splitlines()), 3)
        self.assertNotIn("Traceback", err.getvalue())


if __name__ == "__main__":
    unittest.main()
