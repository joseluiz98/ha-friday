"""Spec 2026-11-24 goal 5: falhas de TTS SEGUIDAS (sem sucesso no meio, em qualquer intervalo de tempo) ligam um
problema no HA. Rodar: cd voice_gateway && python3 -m unittest -q test_tts_saude"""
import json, os, shutil, tempfile, unittest

import tts_saude as ts

DIA = 86400.0


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.arq = os.path.join(self.d, "tts-health.json")
        self.antes = os.environ.get("FRIDAY_TTS_HEALTH")
        os.environ["FRIDAY_TTS_HEALTH"] = self.arq
        self.addCleanup(self._volta)

    def _volta(self):
        if self.antes is None:
            os.environ.pop("FRIDAY_TTS_HEALTH", None)
        else:
            os.environ["FRIDAY_TTS_HEALTH"] = self.antes


class TestSaude(Base):
    def test_starts_healthy(self):
        self.assertEqual(ts.estado(0.0)["falhando"], False)
        self.assertEqual(ts.estado(0.0)["seguidas"], 0)

    def test_three_consecutive_failures_turn_on(self):  # horas de intervalo contam: timers são raros
        ts.registrar(False, "timer", "ClientError", agora=0.0)
        ts.registrar(False, "anuncio", "TimeoutError", agora=3 * 3600.0)
        self.assertFalse(ts.estado(3 * 3600.0)["falhando"])
        ts.registrar(False, "timer", "ClientError", agora=9 * 3600.0)
        e = ts.estado(9 * 3600.0)
        self.assertEqual((e["falhando"], e["seguidas"], e["desde"], e["ultimo_erro"], e["origem"]),
                         (True, 3, 0.0, "ClientError", "timer"))

    def test_success_clears_the_streak(self):
        for t in (0.0, 1.0, 2.0):
            ts.registrar(False, "timer", "E", agora=t)
        ts.registrar(True, "timer", agora=3.0)
        e = ts.estado(3.0)
        self.assertEqual((e["falhando"], e["seguidas"], e["desde"]), (False, 0, None))

    def test_two_failures_then_success_then_two_failures_is_off(self):
        for t in (0.0, 1.0):
            ts.registrar(False, "timer", "E", agora=t)
        ts.registrar(True, "anuncio", agora=2.0)
        for t in (3.0, 4.0):
            ts.registrar(False, "timer", "E", agora=t)
        self.assertFalse(ts.estado(4.0)["falhando"])

    def test_expires_after_seven_days_without_attempts(self):
        for t in (0.0, 1.0, 2.0):
            ts.registrar(False, "timer", "E", agora=t)
        self.assertTrue(ts.estado(2.0 + 6.9 * DIA)["falhando"])
        self.assertFalse(ts.estado(2.0 + 7.1 * DIA)["falhando"])

    def test_a_new_failure_after_expiry_starts_counting_again_not_instantly_on(self):
        for t in (0.0, 1.0, 2.0):
            ts.registrar(False, "timer", "E", agora=t)
        ts.registrar(False, "timer", "E", agora=8 * DIA)  # a lista expirou: esta é a primeira da nova sequência
        e = ts.estado(8 * DIA)
        self.assertEqual((e["falhando"], e["seguidas"]), (False, 1))

    def test_announce_and_timer_failures_share_the_streak(self):
        ts.registrar(False, "anuncio", "E", agora=0.0)
        ts.registrar(False, "timer", "E", agora=1.0)
        ts.registrar(False, "anuncio", "E", agora=2.0)
        self.assertTrue(ts.estado(2.0)["falhando"])

    def test_corrupt_state_file_reads_as_healthy_and_recovers(self):
        with open(self.arq, "w") as f:
            f.write("{nao e json")
        self.assertFalse(ts.estado(0.0)["falhando"])
        ts.registrar(False, "timer", "E", agora=1.0)
        self.assertEqual(ts.estado(1.0)["seguidas"], 1)

    def test_unwritable_location_never_raises(self):
        os.environ["FRIDAY_TTS_HEALTH"] = "/proc/nao/pode/gravar.json"
        ts.registrar(False, "timer", "E", agora=0.0)  # não levanta: nunca atrapalha o timer nem o anúncio

    def test_file_is_plain_json(self):
        ts.registrar(False, "timer", "E", agora=5.0)
        with open(self.arq) as f:
            self.assertEqual(json.load(f)["seguidas"], 1)


if __name__ == "__main__":
    unittest.main()
