"""Testes do modo em segundo plano da voz (lógica pura, roda sem o Pipecat).

    cd /srv/friday/app && python3 -m unittest test_segundo_plano
"""

import asyncio
import unittest

import segundo_plano as sp


class RelogioFalso:
    def __init__(self):
        self.agora = 100.0

    def __call__(self):
        return self.agora


class TestJanela(unittest.TestCase):
    def test_resposta_dentro_da_janela_volta_direto(self):
        async def cenario():
            async def cerebro():
                await asyncio.sleep(0.01)
                return "pronto"

            tarefa = asyncio.create_task(cerebro())
            return await sp.esperar_com_janela(tarefa, 1.0)

        self.assertEqual(asyncio.run(cenario()), (True, "pronto"))

    def test_estouro_da_janela_nao_cancela_o_cerebro(self):
        async def cenario():
            async def cerebro():
                await asyncio.sleep(0.2)
                return "atrasado"

            tarefa = asyncio.create_task(cerebro())
            primeira = await sp.esperar_com_janela(tarefa, 0.01)
            depois = await tarefa
            return primeira, depois

        self.assertEqual(asyncio.run(cenario()), ((False, None), "atrasado"))


class TestSilencio(unittest.TestCase):
    def test_voz_falando_nao_e_silencio(self):
        relogio = RelogioFalso()
        estado = sp.EstadoDaConversa(relogio)
        estado.voz_comecou()
        relogio.agora += 10
        self.assertFalse(estado.silencio(2.0))

    def test_atividade_recente_nao_e_silencio(self):
        relogio = RelogioFalso()
        estado = sp.EstadoDaConversa(relogio)
        estado.jose_falou()
        relogio.agora += 1.0
        self.assertFalse(estado.silencio(2.0))

    def test_silencio_depois_do_minimo(self):
        relogio = RelogioFalso()
        estado = sp.EstadoDaConversa(relogio)
        estado.voz_comecou()
        estado.voz_parou()
        relogio.agora += 2.5
        self.assertTrue(estado.silencio(2.0))

    def test_esperar_silencio_devolve_true_quando_cala(self):
        relogio = RelogioFalso()
        estado = sp.EstadoDaConversa(relogio)
        estado.voz_comecou()
        passos = []

        async def dormir(_):
            passos.append(1)
            if len(passos) == 2:
                estado.voz_parou()
            relogio.agora += 1.0

        self.assertTrue(asyncio.run(sp.esperar_silencio(estado, 2.0, 0.3, dormir)))

    def test_esperar_silencio_devolve_false_se_a_conversa_fecha(self):
        relogio = RelogioFalso()
        estado = sp.EstadoDaConversa(relogio)
        estado.voz_comecou()

        async def dormir(_):
            estado.aberta = False

        self.assertFalse(asyncio.run(sp.esperar_silencio(estado, 2.0, 0.3, dormir)))


class TestManterAberta(unittest.TestCase):
    def test_so_mantem_com_pedido_pendente(self):
        estado = sp.EstadoDaConversa(RelogioFalso())
        self.assertFalse(estado.manter_aberta())
        estado.pendentes = 1
        self.assertTrue(estado.manter_aberta())


class TestTextos(unittest.TestCase):
    def test_texto_para_gemini_leva_pedido_e_resposta(self):
        t = sp.texto_para_gemini("ver a lavadora", "Foi o lembrete das 3h.")
        self.assertTrue(t.startswith("[Resposta do cérebro"))
        self.assertIn("ver a lavadora", t)
        self.assertIn("Foi o lembrete das 3h.", t)

    def test_push_limpa_markdown_e_preserva_entity_id(self):
        t = sp.texto_push("briefing", "**Pronto:** `automation.briefing_matinal_diario` desligada.")
        self.assertNotIn("**", t)
        self.assertNotIn("`", t)
        self.assertIn("automation.briefing_matinal_diario", t)
        self.assertIn("briefing", t)

    def test_push_longo_e_cortado_com_aviso(self):
        t = sp.texto_push("x", "a" * 2000, limite=300)
        self.assertLessEqual(len(t), 300)
        self.assertTrue(t.endswith("(resposta completa no app da Sexta-feira)"))


if __name__ == "__main__":
    unittest.main()
