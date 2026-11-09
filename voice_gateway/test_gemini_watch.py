"""cd /srv/friday/app && python3 -m unittest test_gemini_watch -v"""
import unittest
from types import SimpleNamespace as NS

import gemini_watch as gw


class Relogio:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def msg(**sc):
    return NS(server_content=NS(**sc) if sc else None, tool_call=None, tool_call_cancellation=None,
              go_away=None, usage_metadata=None)


class TestResumo(unittest.TestCase):
    def test_turn_complete_vazio(self):
        self.assertEqual(gw.summarize_server_message(msg(turn_complete=True)), {"turn_complete": True})

    def test_audio_conta_partes_sem_bytes(self):
        partes = [NS(inline_data=NS(data=b"\x00" * 3200), text=None), NS(inline_data=None, text="oi")]
        r = gw.summarize_server_message(msg(model_turn=NS(parts=partes)))
        self.assertEqual(r, {"parts": 2, "audio_parts": 1, "text": "oi"})
        self.assertNotIn("\x00", str(r))

    def test_fala_transcrita_e_interrupcao(self):
        r = gw.summarize_server_message(msg(output_transcription=NS(text="Olha"), interrupted=True))
        self.assertEqual(r, {"said": "Olha", "interrupted": True})

    def test_status_de_interacao(self):
        self.assertEqual(gw.summarize_server_message(msg(interaction_status="IDLE")),
                         {"interaction_status": "IDLE"})

    def test_mensagem_sem_conteudo_conhecido(self):
        self.assertEqual(gw.summarize_server_message(msg()), {"other": True})

    def test_tool_call_e_go_away(self):
        m = msg()
        m.tool_call = NS(function_calls=[NS(name="perguntar_ao_cerebro")])
        m.go_away = NS(time_left="10s")
        self.assertEqual(gw.summarize_server_message(m), {"tool_call": ["perguntar_ao_cerebro"], "go_away": True})


class TestVigia(unittest.TestCase):
    def setUp(self):
        self.r = Relogio()
        self.v = gw.ResultWatch(clock=self.r)

    def test_fechada_antes_de_abrir(self):
        self.assertFalse(self.v.active())
        self.assertFalse(self.v.silent_too_long())
        self.assertIsNone(self.v.speech_started())

    def test_fala_fecha_a_janela_e_devolve_a_latencia(self):
        self.v.open()
        self.r.t += 0.9
        self.assertTrue(self.v.active())
        self.assertAlmostEqual(self.v.speech_started(), 0.9)
        self.assertFalse(self.v.active())
        self.r.t += 10
        self.assertFalse(self.v.silent_too_long())

    def test_silencio_de_8s_avisa(self):
        self.v.open()
        self.r.t += 7.9
        self.assertFalse(self.v.silent_too_long())
        self.r.t += 0.1
        self.assertTrue(self.v.silent_too_long())

    def test_janela_vence_em_15s(self):
        self.v.open()
        self.r.t += 14.9
        self.assertTrue(self.v.active())
        self.r.t += 0.1
        self.assertFalse(self.v.active())


if __name__ == "__main__":
    unittest.main()
