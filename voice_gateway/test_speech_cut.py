"""cd /srv/friday/app && python3 -m unittest test_speech_cut -v"""
import unittest
from types import SimpleNamespace as NS

import speech_cut as sc


def msg(tool_call=None, usage_metadata=None, **campos):
    base = dict(model_turn=None, output_transcription=None, input_transcription=None, grounding_metadata=None,
                interaction_status=None, interrupted=None, generation_complete=None, turn_complete=None)
    base.update(campos)
    return NS(server_content=NS(**base), tool_call=tool_call, usage_metadata=usage_metadata, go_away=None,
              tool_call_cancellation=None)


def audio():
    return msg(model_turn=NS(parts=[NS(inline_data=NS(data=b"\x00\x00"), text=None)]))


class TestSpeechCut(unittest.TestCase):
    def setUp(self):
        self.c = sc.SpeechCut()

    def test_without_a_cut_everything_passes(self):
        m = audio()
        self.assertIs(self.c.filter(m), m)
        self.assertIsNotNone(m.server_content.model_turn)

    def test_cut_drops_the_rest_of_the_generation(self):
        self.c.filter(audio())
        self.assertTrue(self.c.cut())
        self.assertIsNone(self.c.filter(audio()))
        self.assertIsNone(self.c.filter(msg(output_transcription=NS(text="resto do recado"))))

    def test_the_end_of_the_cut_generation_passes_and_disarms(self):
        self.c.filter(audio())
        self.c.cut()
        fim = msg(interrupted=True)
        self.assertIs(self.c.filter(fim), fim)
        nova = audio()
        self.assertIs(self.c.filter(nova), nova)  # a resposta nova à pessoa toca
        self.assertIsNotNone(nova.server_content.model_turn)

    def test_generation_complete_also_ends_the_cut(self):
        self.c.filter(audio())
        self.c.cut()
        self.c.filter(msg(generation_complete=True))
        self.assertIsNotNone(self.c.filter(audio()))

    def test_no_cut_when_nothing_is_being_generated(self):
        self.c.filter(audio())
        self.c.filter(msg(turn_complete=True))  # a resposta já saiu toda; a Pi ainda tocava o que tinha
        self.assertFalse(self.c.cut())
        self.assertIsNotNone(self.c.filter(audio()))

    def test_other_fields_still_reach_pipecat(self):
        self.c.filter(audio())
        self.c.cut()
        m = msg(model_turn=NS(parts=[]), input_transcription=NS(text="oi"))
        self.assertIs(self.c.filter(m), m)
        self.assertIsNone(m.server_content.model_turn)
        t = msg(tool_call=NS(function_calls=[]))
        self.assertIs(self.c.filter(t), t)

    def test_end_bundled_with_audio_passes_without_the_audio(self):
        self.c.filter(audio())
        self.c.cut()
        m = msg(model_turn=NS(parts=[NS(inline_data=NS(data=b"\x00"), text=None)]), turn_complete=True)
        self.assertIs(self.c.filter(m), m)
        self.assertIsNone(m.server_content.model_turn)
        self.assertFalse(self.c.ativo)

    def test_message_without_server_content_passes(self):
        m = NS(server_content=None, tool_call=NS(function_calls=[]))
        self.assertIs(self.c.filter(m), m)


class TestFiacaoNoBot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import os
        cls.src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.py")).read()

    def test_every_gemini_message_goes_through_the_cut(self):
        i = self.src.index("async def _mensagem_vigiada(message):")
        trecho = self.src[i:i + 200]
        self.assertIn("message = corte.filter(message)", trecho)
        self.assertIn("if message is None:\n            return", trecho)

    def test_pi_message_arms_the_cut_and_interrupts(self):
        i = self.src.index('msg.type == "cortar_fala"')
        trecho = self.src[i:i + 400]
        self.assertIn("corte.cut()", trecho)
        self.assertIn("InterruptionWorkerFrame()", trecho)


class TestTurnComplete(unittest.TestCase):
    def test_turn_complete_true(self):
        self.assertTrue(sc.turn_complete(msg(turn_complete=True)))

    def test_turn_complete_false_without_flag(self):
        self.assertFalse(sc.turn_complete(msg()))
        self.assertFalse(sc.turn_complete(msg(generation_complete=True)))

    def test_turn_complete_false_without_server_content(self):
        self.assertFalse(sc.turn_complete(NS(server_content=None)))
        self.assertFalse(sc.turn_complete(NS()))

    def test_bot_tells_the_satellite_after_the_original_handler(self):
        import os
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.py")).read()
        i = src.index("async def _mensagem_vigiada(message):")
        trecho = src[i:i + 1100]
        self.assertLess(trecho.index("await _mensagem_original(message)"),
                        trecho.index('await avisar_pi({"evento": "resposta_completa"})'))
        self.assertIn("speech_cut.turn_complete(message)", trecho)


if __name__ == "__main__":
    unittest.main()
