"""Announcement queue and playback state of the maestro (spec 2026-11-20-satellite-announcements, "Behavior").
cd satellite/program && TMPDIR=$(mktemp -d) python3 -m unittest -q test_maestro_anuncio"""
import copy
import unittest
from maestro import ANUNCIO, Ajustes, Maestro

A1, A2 = "abcd1234", "efgh5678"
TOCAR = [("volume_alerta", True), ("luz", "falando"), ("anuncio_tocar", A1)]
T = {"id": "t1", "nome": "arroz", "duracao_s": 600, "termino": 0.0}


class Base(unittest.TestCase):
    def setUp(self):
        self.m = Maestro(Ajustes())


class TestAnuncio(Base):
    def test_idle_plays(self):
        self.assertEqual(self.m.anuncio(A1, 0.0), ("vou_tocar", TOCAR))
        self.assertEqual(self.m.estado, "anuncio")
        self.assertEqual(ANUNCIO, "anuncio")

    def test_dnd_is_dropped(self):
        self.m.evento("nao_perturbe", 0.0, True)
        self.assertEqual(self.m.anuncio(A1, 1.0), ("calado", []))
        self.assertEqual(self.m.publico()["fila_anuncios"], 0)
        self.assertEqual(self.m.estado, "parada")

    def test_muted_plays(self):
        self.m.evento("mudo", 0.0, True)
        resultado, acoes = self.m.anuncio(A1, 1.0)
        self.assertEqual(resultado, "vou_tocar")
        self.assertIn(("anuncio_tocar", A1), acoes)

    def test_boot_holds(self):
        m = Maestro(Ajustes(), mudo_inicial=None)
        self.assertEqual(m.anuncio(A1, 0.0), ("na_fila", []))
        self.assertEqual(m.evento("tique", 5.0), [])  # still booting: held
        m.evento("mudo", 6.0, False)
        acoes = m.evento("tique", 10.0)
        self.assertIn(("anuncio_tocar", A1), acoes)
        self.assertEqual(m.estado, "anuncio")

    def test_open_conversation_waits_then_plays(self):
        self.m.evento("palavra", 0.0)
        self.assertEqual(self.m.anuncio(A1, 0.5), ("na_fila", []))
        self.assertEqual(self.m.publico()["fila_anuncios"], 1)
        self.m.evento("encerrar", 10.0)
        self.assertEqual(self.m.estado, "parada")
        self.assertIn(("anuncio_tocar", A1), self.m.evento("tique", 10.2))
        self.assertEqual(self.m.publico()["fila_anuncios"], 0)

    def test_busy_flag_queues(self):
        self.assertEqual(self.m.anuncio(A1, 0.0, ocupado=True), ("na_fila", []))
        self.assertIn(("anuncio_tocar", A1), self.m.evento("tique", 0.5))

    def test_expires_after_300s(self):
        m = Maestro(Ajustes(), mudo_inicial=None)
        m.anuncio(A1, 0.0)
        self.assertEqual(m.evento("tique", 300.0), [])  # exactly at the limit: still held
        acoes = m.evento("tique", 301.0)
        self.assertIn(("anuncio_apagar", A1), acoes)
        self.assertIn(("registrar_anuncio", {"id": A1, "resultado": "expirado"}), acoes)
        self.assertNotIn(("anuncio_tocar", A1), acoes)
        self.assertEqual(m.publico()["fila_anuncios"], 0)

    def test_expires_when_conversation_runs_long(self):
        self.m.evento("palavra", 0.0)
        self.m.anuncio(A1, 0.0)
        self.m.evento("encerrar", 301.0)
        acoes = self.m.evento("tique", 301.5)
        self.assertEqual(acoes, [("anuncio_apagar", A1), ("registrar_anuncio", {"id": A1, "resultado": "expirado"})])
        self.assertEqual(self.m.estado, "parada")

    def test_dnd_while_queued_drops(self):
        self.m.evento("palavra", 0.0)
        self.m.anuncio(A1, 0.5)
        self.m.evento("nao_perturbe", 1.0, True)
        self.m.evento("encerrar", 5.0)
        acoes = self.m.evento("tique", 5.5)
        self.assertIn(("anuncio_apagar", A1), acoes)
        self.assertIn(("registrar_anuncio", {"id": A1, "resultado": "calado"}), acoes)
        self.assertNotIn(("anuncio_tocar", A1), acoes)

    def test_two_in_order(self):
        self.assertEqual(self.m.anuncio(A1, 0.0)[0], "vou_tocar")
        self.assertEqual(self.m.anuncio(A2, 1.0), ("na_fila", []))
        self.assertEqual(self.m.evento("tique", 2.0), [])  # playing blocks the next
        acoes = self.m.evento("anuncio_fim", 5.0, A1)
        self.assertEqual(acoes, [("volume_alerta", False), ("luz", "parada"), ("anuncio_apagar", A1),
                                 ("volume_alerta", True), ("luz", "falando"), ("anuncio_tocar", A2)])
        self.assertEqual(self.m.estado, "anuncio")
        acoes = self.m.evento("anuncio_fim", 8.0, A2)
        self.assertEqual(acoes, [("volume_alerta", False), ("luz", "parada"), ("anuncio_apagar", A2)])
        self.assertEqual(self.m.estado, "parada")

    def test_fim_back_to_rest_light_in_dnd_not_needed_but_rest_reused(self):
        self.m.anuncio(A1, 0.0)
        self.m.evento("nao_perturbe", 1.0, True)  # while playing: it finishes
        self.assertEqual(self.m.estado, "anuncio")
        acoes = self.m.evento("anuncio_fim", 3.0, A1)
        self.assertIn(("luz", "nao_perturbe"), acoes)

    def test_stale_fim_is_ignored(self):
        self.assertEqual(self.m.evento("anuncio_fim", 1.0, A1), [])
        self.m.anuncio(A1, 2.0)
        self.assertEqual(self.m.evento("anuncio_fim", 3.0, A2), [])
        self.assertEqual(self.m.estado, "anuncio")

    def test_alarm_and_delivery_go_first(self):
        self.m.evento("palavra", 0.0)
        self.m.anuncio(A1, 0.5)
        self.m.campainha("e1", 0.6)
        self.m.evento("timer_venceu", 0.7, T)  # closes the open conversation and rings; the others keep waiting
        self.assertEqual(self.m.estado, "alarme")
        self.m.evento("botao", 5.0)  # stops the alarm
        acoes = self.m.evento("tique", 5.2)
        self.assertIn(("conectar", {"entrega": "e1"}), acoes)  # delivery before the announcement
        self.assertNotIn(("anuncio_tocar", A1), acoes)
        self.m.evento("encerrar", 20.0)
        self.assertIn(("anuncio_tocar", A1), self.m.evento("tique", 20.2))

    def test_queued_alarm_plays_before_announcement(self):
        self.m.anuncio(A1, 0.0)
        self.m.evento("timer_venceu", 1.0, T)  # arrives while an announcement plays: queued, not lost
        self.assertEqual(self.m.estado, "anuncio")
        self.m.anuncio(A2, 1.5)
        acoes = self.m.evento("anuncio_fim", 3.0, A1)
        self.assertIn(("alarme", "tocar"), acoes)
        self.assertNotIn(("anuncio_tocar", A2), acoes)
        self.assertEqual(self.m.estado, "alarme")

    def test_alarm_active_queues(self):
        self.m.evento("timer_venceu", 0.0, T)
        self.assertEqual(self.m.anuncio(A1, 0.5), ("na_fila", []))

    def test_wake_word_cuts_and_opens(self):
        self.m.anuncio(A1, 0.0)
        acoes = self.m.evento("palavra", 1.0, {"wake_word": "friday"})
        self.assertEqual(acoes[:3], [("anuncio_parar", A1), ("volume_alerta", False), ("anuncio_apagar", A1)])
        self.assertIn(("conectar", {"wake_word": "friday"}), acoes)
        self.assertEqual(self.m.estado, "conectando")
        self.assertEqual(self.m._abertura["como_abriu"], "palavra")

    def test_wake_word_ignored_when_muted(self):
        self.m.anuncio(A1, 0.0)
        self.m.evento("mudo", 0.5, True)
        self.assertEqual(self.m.evento("palavra", 1.0, {"wake_word": "friday"}), [])
        self.assertEqual(self.m.estado, "anuncio")

    def test_button_stops(self):
        self.m.anuncio(A1, 0.0)
        for cor in (None, "verde", "vermelho"):
            m = Maestro(Ajustes())
            m.anuncio(A1, 0.0)
            acoes = m.evento("botao", 1.0, cor)
            self.assertEqual(acoes, [("anuncio_parar", A1), ("volume_alerta", False), ("luz", "parada"),
                                     ("anuncio_apagar", A1)])
            self.assertEqual(m.estado, "parada")
            self.assertIsNone(m._abertura)

    def test_room_speech_ignored(self):
        self.m.anuncio(A1, 0.0)
        for nome in ("fala_comecou", "fala_parou", "ferramenta", "ela_comecou", "encerrar", "cerebro_pendente"):
            self.assertEqual(self.m.evento(nome, 1.0, 1), [], nome)
        self.assertEqual(self.m.estado, "anuncio")

    def test_tick_does_nothing_while_playing(self):
        self.m.anuncio(A1, 0.0)
        self.assertEqual(self.m.evento("tique", 400.0), [])
        self.assertEqual(self.m.estado, "anuncio")

    def test_dnd_sound_waits_for_the_end(self):
        self.m.anuncio(A1, 0.0)
        acoes = self.m.evento("nao_perturbe", 1.0, True)
        self.assertNotIn(("som", "nao_perturbe_ligou"), acoes)
        fim = self.m.evento("anuncio_fim", 3.0, A1)
        self.assertIn(("som", "nao_perturbe_ligou"), fim)

    def test_wake_word_would_act_matches_event(self):
        for mudo in (False, True, None):
            m = Maestro(Ajustes(), mudo_inicial=False)
            m.anuncio(A1, 0.0)
            m.mudo = mudo
            previsto = m.palavra_agiria({"wake_word": "friday"})
            real = bool(copy.deepcopy(m).evento("palavra", 1.0, {"wake_word": "friday"}))
            self.assertEqual(previsto, real)
            self.assertEqual(previsto, mudo is False)

    def test_public_state_has_the_queue_size(self):
        self.assertEqual(self.m.publico()["fila_anuncios"], 0)
        self.m.anuncio(A1, 0.0)
        self.m.anuncio(A2, 0.0)
        self.assertEqual(self.m.publico()["fila_anuncios"], 1)


if __name__ == "__main__":
    unittest.main()
