"""Testes do maestro para timers e entregas (spec 2026-10-12 §2 e §3):
cd /homeassistant/tools/satelite/programa && python3 -m unittest test_maestro_alertas -v"""
import unittest
from maestro import Ajustes, Maestro

T = {"id": "t1", "nome": "arroz", "duracao_s": 600, "termino": 0.0}
T2 = {"id": "t2", "nome": None, "duracao_s": 60, "termino": 0.0}


def nomes(acoes):
    return [a[0] if a[1] is None or isinstance(a[1], dict) else f"{a[0]}:{a[1]}"
            for a in acoes if a[0] not in ("registrar", "registrar_timer")]


def achar(acoes, tipo):
    return next(a[1] for a in acoes if a[0] == tipo)


class Base(unittest.TestCase):
    def setUp(self):
        self.m = Maestro(Ajustes(volta_alarme_s=1.65, rampa_s=3.0, voltas_antes_do_nome=3, alarme_max_s=300.0))

    def alarme(self, t=0.0, timer=T):
        return self.m.evento("timer_venceu", t, timer)


class TestAlarme(Base):
    def test_timer_vencido_toca_alarme_no_volume_de_alertas(self):
        self.assertEqual(nomes(self.alarme()), ["volume_alerta:True", "alarme:tocar", "luz:alarme"])
        self.assertEqual(self.m.estado, "alarme")

    def test_expiry_opens_no_conversation(self):  # 24/11: o timer só toca; nenhuma sessão abre no vencimento
        self.alarme()
        for t in (1.5, 2.0, 5.0, 7.0):
            self.assertNotIn("conectar", nomes(self.m.evento("tique", t)))
        self.assertEqual(self.m.estado, "alarme")

    def test_notice_comes_after_ramp_plus_three_loops(self):
        self.alarme()  # rampa 3 s + 3 voltas de 1,65 s = 7,95 s
        self.assertEqual(self.m.evento("tique", 7.9), [])
        self.assertEqual(self.m.evento("tique", 8.0), [("aviso_timer", T)])
        self.assertEqual(self.m.evento("tique", 9.0), [])  # uma vez só

    def test_speech_during_alarm_does_not_stop_it(self):  # a TV não encerra o timer
        self.alarme()
        self.m.evento("fala_comecou", 2.0)
        self.m.evento("fala_parou", 6.0)
        self.m.evento("fala_comecou", 20.0)
        acoes = self.m.evento("tique", 60.0)
        self.assertNotIn("registrar_timer", [a[0] for a in acoes])
        self.assertEqual(self.m.estado, "alarme")

    def test_not_answered_after_300s_still_notifies(self):
        self.alarme()
        acoes = self.m.evento("tique", 300.0)
        self.assertEqual(nomes(acoes), ["alarme:parar", "volume_alerta:False", "luz:parada", "aviso_nao_atendido"])
        self.assertEqual(achar(acoes, "aviso_nao_atendido"), T)
        self.assertEqual(achar(acoes, "registrar_timer")["como_parou"], "nao_atendido")
        self.assertEqual(self.m.estado, "parada")

    def test_notice_plays_even_with_the_microphone_muted(self):
        self.m.evento("mudo", 0.0, True)
        self.alarme()
        self.assertEqual(self.m.evento("tique", 8.0), [("aviso_timer", T)])

    def test_botao_para_o_alarme(self):
        self.alarme()
        acoes = self.m.evento("botao", 1.0)
        self.assertEqual(nomes(acoes), ["alarme:parar", "volume_alerta:False", "luz:parada"])
        self.assertEqual(achar(acoes, "registrar_timer")["como_parou"], "botao")

    def test_palavra_para_o_alarme_e_abre_conversa_normal(self):
        self.alarme()
        acoes = self.m.evento("palavra", 1.0)
        self.assertEqual(nomes(acoes), ["alarme:parar", "volume_alerta:False", "luz:parada", "som:inicio",
                                        "luz:ouvindo", "conectar"])
        self.assertIsNone(achar(acoes, "conectar"))

    def test_timer_no_meio_de_conversa_encerra_a_conversa(self):
        self.m.evento("palavra", 0.0)
        self.m.evento("conectou", 1.0)
        acoes = self.alarme(2.0)
        self.assertEqual(nomes(acoes), ["luz:parada", "desconectar", "volume_alerta:True", "alarme:tocar",
                                        "luz:alarme"])

    def test_segundo_timer_espera_o_primeiro_parar(self):
        self.alarme()
        self.assertEqual(self.alarme(1.0, T2), [])
        self.m.evento("botao", 2.0)
        acoes = self.m.evento("tique", 2.2)
        self.assertEqual(nomes(acoes), ["volume_alerta:True", "alarme:tocar", "luz:alarme"])

    def test_queued_timers_each_get_one_notice(self):
        self.alarme()
        self.alarme(1.0, T2)
        self.assertEqual(self.m.evento("tique", 8.0), [("aviso_timer", T)])
        self.m.evento("botao", 9.0)
        self.m.evento("tique", 9.2)  # o segundo começa a tocar
        self.assertEqual(self.m.evento("tique", 9.2 + 7.9), [])
        self.assertEqual(self.m.evento("tique", 9.2 + 8.0), [("aviso_timer", T2)])
        self.assertEqual(self.m.evento("tique", 9.2 + 9.0), [])

    def test_nao_perturbe_nao_cala_timer(self):
        self.m.evento("nao_perturbe", 0.0, True)
        self.assertIn("alarme:tocar", nomes(self.alarme()))

    def test_mudo_no_alarme_nao_corta_e_nao_escreve_luz(self):
        self.alarme()
        self.assertEqual(self.m.evento("mudo", 1.0, True), [])
        self.assertEqual(self.m.estado, "alarme")

    def test_publico_mostra_o_alarme(self):
        self.alarme()
        self.assertEqual(self.m.publico()["alarme"], "arroz")


class TestEntrega(Base):
    def test_nao_perturbe_responde_calado(self):
        self.m.evento("nao_perturbe", 0.0, True)
        self.assertEqual(self.m.campainha("e1", 0.0), ("calado", []))

    def test_parada_abre_com_aviso_de_lembrete_no_volume_de_alertas(self):
        r, acoes = self.m.campainha("e1", 0.0)
        self.assertEqual(r, "vou_abrir")
        self.assertEqual(nomes(acoes), ["volume_alerta:True", "som:lembrete", "luz:falando", "conectar",
                                        "microfone:False"])  # M4; 19/11: fechado desde o conectar
        self.assertEqual(achar(acoes, "conectar"), {"entrega": "e1"})

    def test_fim_da_entrega_volta_ao_volume_normal(self):
        self.m.campainha("e1", 0.0)
        self.m.evento("conectou", 1.0)
        self.m.evento("ela_comecou", 2.0)
        self.m.evento("ela_parou", 4.0)
        acoes = self.m.evento("tique", 4.0 + 8.0)
        self.assertEqual(nomes(acoes)[-1], "volume_alerta:False")

    def test_espera_inicial_da_entrega_nao_e_de_4_s(self):
        self.m.campainha("e1", 0.0)
        self.m.evento("conectou", 1.0)
        self.assertEqual(self.m.evento("tique", 5.0), [])

    def test_conversa_aberta_entra_na_fila_e_abre_depois(self):
        self.m.evento("palavra", 0.0)
        self.assertEqual(self.m.campainha("e1", 0.5), ("na_fila", []))
        self.m.evento("encerrar", 10.0)
        acoes = self.m.evento("tique", 10.2)
        self.assertEqual(achar(acoes, "conectar"), {"entrega": "e1"})

    def test_fila_vencida_e_descartada(self):
        self.m.evento("palavra", 0.0)
        self.m.campainha("e1", 0.5)
        self.m.evento("encerrar", 100.0)
        self.assertEqual(self.m.evento("tique", 100.2), [])

    def test_alarme_tocando_poe_entrega_na_fila(self):
        self.alarme()
        self.assertEqual(self.m.campainha("e1", 0.5)[0], "na_fila")

    def test_mudo_desconhecido_na_partida_poe_na_fila(self):
        m = Maestro(Ajustes(), mudo_inicial=None)
        self.assertEqual(m.campainha("e1", 0.0)[0], "na_fila")

    def test_mudo_fisico_abre_mas_sem_escrever_luz(self):
        self.m.evento("mudo", 0.0, True)
        r, acoes = self.m.campainha("e1", 0.0)
        self.assertEqual(r, "vou_abrir")
        self.assertNotIn("luz:ouvindo", nomes(acoes))

    def test_botao_na_entrega_avisa_o_bot(self):
        self.m.campainha("e1", 0.0)
        self.m.evento("conectou", 1.0)
        acoes = self.m.evento("botao", 3.0)
        self.assertEqual(nomes(acoes)[0], "avisar_bot:atendida")
        self.assertEqual(self.m.estado, "cortada")

    def test_vermelho_na_entrega_avisa_atendida_e_cancela(self):
        self.m.campainha("e1", 0.0)
        self.m.evento("conectou", 1.0)
        acoes = self.m.evento("botao", 3.0, "vermelho")
        n = nomes(acoes)
        self.assertEqual(n[0], "avisar_bot:atendida")
        self.assertEqual(n[1], "cancelar_conversa")
        self.assertEqual(self.m.estado, "parada")


TM = {"id": "t9", "nome": "acordar", "duracao_s": 60, "termino": 0.0, "music": "Legião Urbana"}


class TestAlarmeMusical(Base):
    """Spec 2026-10-21 R11: o alarme com música toca a campainha, pede a música à VM e, se ela não começar em 10 s,
    segue como um alarme normal. O alarme nunca falha por causa da música."""

    def test_musical_alarm_requests_music(self):
        acoes = self.alarme(0.0, TM)
        self.assertIn(("alarme", "tocar"), acoes)
        self.assertEqual(achar(acoes, "request_music"), {"timer_id": "t9", "query": "Legião Urbana"})
        self.assertEqual(self.m.evento("tique", 1.6), [])  # nenhum aviso antes de 10 s
        self.assertEqual(self.m.evento("tique", 9.9), [])
        self.assertEqual(self.m.estado, "alarme")

    def test_music_started_ends_alarm(self):
        self.alarme(0.0, TM)
        acoes = self.m.evento("music_started", 4.0, "t9")
        self.assertIn(("volume_alerta", False), acoes)
        self.assertIn(("alarme", "parar"), acoes)
        r = next(a[1] for a in acoes if a[0] == "registrar_timer")
        self.assertEqual(r["como_parou"], "music")
        self.assertIn(("music_ramp", None), acoes)
        self.assertEqual(self.m.estado, "parada")

    def test_music_started_for_another_timer_is_ignored(self):
        self.alarme(0.0, TM)
        self.assertEqual(self.m.evento("music_started", 4.0, "outro"), [])
        self.assertEqual(self.m.estado, "alarme")

    def test_music_timeout_falls_back(self):
        self.alarme(0.0, TM)
        self.assertEqual(self.m.evento("tique", 10.1), [("aviso_timer", TM)])  # o caminho normal do timer
        self.assertEqual(self.m.estado, "alarme")

    def test_music_failed_falls_back(self):
        self.alarme(0.0, TM)
        self.assertEqual(self.m.evento("music_failed", 3.0, "t9"), [])
        self.assertEqual(self.m.evento("tique", 3.1), [])
        self.assertEqual(self.m.evento("tique", 8.0), [("aviso_timer", TM)])

    def test_music_started_after_fallback_is_ignored(self):
        self.alarme(0.0, TM)
        self.m.evento("tique", 10.1)
        self.assertEqual(self.m.evento("music_started", 11.0, "t9"), [])

    def test_plain_timer_unchanged(self):
        acoes = self.alarme(0.0, T)
        self.assertEqual(nomes(acoes), ["volume_alerta:True", "alarme:tocar", "luz:alarme"])
        self.assertTrue(all(a[0] != "request_music" for a in acoes))
        self.assertEqual(self.m.evento("tique", 1.5), [])  # 24/11: nada de conversa na primeira volta

    def test_musical_alarm_with_mute_still_asks_for_music(self):
        self.m.evento("mudo", 0.0, True)
        acoes = self.alarme(0.0, TM)
        self.assertIn("request_music", [a[0] for a in acoes])
        self.assertEqual(self.m.evento("tique", 10.1), [("aviso_timer", TM)])  # sem a música, o alarme segue e avisa

    def test_is_musical_alarm_exposed(self):
        self.assertFalse(self.m.musical_alarm)
        self.alarme(0.0, TM)
        self.assertTrue(self.m.musical_alarm)


if __name__ == "__main__":
    unittest.main()
