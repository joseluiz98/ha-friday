"""voice-settings.json written by Home Assistant (Task 15, spec 2026-10-29): read on every conversation.
cd /srv/friday/app && python3 -m unittest test_voice_settings -v"""
import json, os, tempfile, unittest

import voice_settings as vs


class TestVoiceSettings(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.arq = os.path.join(self.dir.name, "voice-settings.json")

    def tearDown(self):
        self.dir.cleanup()

    def escrever(self, d):
        with open(self.arq, "w") as f:
            f.write(d if isinstance(d, str) else json.dumps(d))

    def test_voice_settings_read_per_conversation(self):
        self.assertEqual(vs.ler(self.arq), {})  # missing file: nothing overrides the env defaults
        self.escrever({"voice": "Kore", "model": "models/gemini-3.9-live"})
        self.assertEqual(vs.ler(self.arq)["voice"], "Kore")
        self.escrever({"voice": "Puck"})  # next conversation sees the new file, no cache
        self.assertEqual(vs.ler(self.arq), {"voice": "Puck"})

    def test_invalid_values_are_dropped(self):
        self.escrever({"voice": "Nope", "model": "x y", "speaking_rate": "warp", "brief_mode": "yes"})
        self.assertEqual(vs.ler(self.arq), {})
        self.escrever("{")
        self.assertEqual(vs.ler(self.arq), {})
        self.escrever("[1]")
        self.assertEqual(vs.ler(self.arq), {})

    def test_facts_only_for_non_default_settings(self):
        self.assertEqual(vs.fatos({}), "")
        self.assertEqual(vs.fatos({"speaking_rate": "normal", "brief_mode": False}), "")
        slow = vs.fatos({"speaking_rate": "slow"})
        self.assertIn("lenta", slow)
        self.assertIn("rápida", vs.fatos({"speaking_rate": "fast"}))
        self.assertIn("breve", vs.fatos({"brief_mode": True}))

    def test_facts_state_facts_not_orders(self):
        texto = vs.fatos({"speaking_rate": "slow", "brief_mode": True}).lower()
        for ordem in ("fale ", "responda", "use ", "sempre", "nunca", "deve", "!"):
            self.assertNotIn(ordem, texto)

    def test_one_voice_for_live_and_tts(self):
        self.escrever({"voice": "Kore", "model": "gemini-3.9-live"})
        cfg = vs.ler(self.arq)
        self.assertEqual(vs.voz(cfg, {"FRIDAY_VOICE": "Aoede"}), "Kore")
        self.assertEqual(vs.modelo(cfg, {}), "models/gemini-3.9-live")
        self.assertEqual(vs.voz({}, {"FRIDAY_VOICE": "Aoede"}), "Aoede")  # fail-open: env
        self.assertEqual(vs.voz({}, {}), "Charon")
        self.assertEqual(vs.modelo({}, {"FRIDAY_GEMINI_MODEL": "models/m"}), "models/m")
        self.assertEqual(vs.modelo({}, {}), "models/gemini-3.8-live")

    def test_bot_reads_the_file_in_the_conversation_setup(self):
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("vs_cfg.ler(", src)
        self.assertIn('vs_cfg.fatos(', src)
        self.assertIn("vs_cfg.voz(cfg_voz)", src)  # both the Live session and the whisper TTS


if __name__ == "__main__":
    unittest.main()
