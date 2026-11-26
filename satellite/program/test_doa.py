"""DOA v0 / trava de feixe (spec 2026-11-17-xvf3800-support). python3 -m unittest -q test_doa"""
import math
import unittest

import doa


def rad(g):
    return math.radians(g)


class Link:
    def __init__(self, ok=True):
        self.escritas, self.ok = [], ok
        self.leituras = {"AEC_FIXEDBEAMSONOFF": [1], "AUDIO_MGR_OP_L": [6, 0]}

    def write(self, nome, valores):
        self.escritas.append((nome, list(valores)))
        return self.ok

    def read(self, nome):
        return self.leituras.get(nome)


class TestDirecao(unittest.TestCase):
    def test_circular_mean_wraps(self):
        d = doa.Direcao()
        for t, g in ((9.0, 350), (9.3, 10), (9.6, 350), (9.9, 10)):
            d.alimentar(t, rad(g))
        theta = d.decidir(10.0)
        self.assertAlmostEqual(math.degrees(theta) % 360 if math.degrees(theta) % 360 < 180 else math.degrees(theta) % 360 - 360, 0, delta=0.5)

    def test_needs_three_speech_samples(self):
        d = doa.Direcao()
        d.alimentar(9.5, rad(90)); d.alimentar(9.6, rad(90)); d.alimentar(9.7, None)
        self.assertIsNone(d.decidir(10.0))
        d.alimentar(9.8, rad(90))
        self.assertAlmostEqual(math.degrees(d.decidir(10.0)), 90, delta=0.5)

    def test_needs_70_percent_within_30_degrees(self):
        d = doa.Direcao()
        for t, g in ((9.0, 90), (9.2, 95), (9.4, 270), (9.6, 260)):  # pessoa e TV disputando
            d.alimentar(t, rad(g))
        self.assertIsNone(d.decidir(10.0))

    def test_only_the_last_window_counts(self):
        d = doa.Direcao()
        for t in (5.0, 5.1, 5.2):  # velhas: fora de 1,5 s
            d.alimentar(t, rad(200))
        for t in (9.0, 9.2, 9.4):
            d.alimentar(t, rad(30))
        self.assertAlmostEqual(math.degrees(d.decidir(10.0)), 30, delta=0.5)

    def test_nan_counts_as_silence(self):
        d = doa.Direcao()
        for t in (9.0, 9.2, 9.4):
            d.alimentar(t, float("nan"))
        self.assertIsNone(d.decidir(10.0))


class TestTrava(unittest.TestCase):
    def test_lock_sequence(self):
        link = Link()
        t = doa.TravaFeixe(link, log=lambda m: None, relogio=lambda: 100.0)
        self.assertTrue(t.travar(rad(30)))
        self.assertEqual([e[0] for e in link.escritas],
                         ["AEC_FIXEDBEAMSAZIMUTH_VALUES", "AEC_FIXEDBEAMSONOFF", "AUDIO_MGR_OP_R", "AUDIO_MGR_OP_L"])
        self.assertAlmostEqual(link.escritas[0][1][0], rad(30)); self.assertAlmostEqual(link.escritas[0][1][1], rad(30))
        self.assertEqual(link.escritas[1][1], [1]); self.assertEqual(link.escritas[2][1], [7, 0])
        self.assertEqual(link.escritas[3][1], [6, 0])
        self.assertTrue(t.travado)

    def test_channel0_not_read_back_falls_back(self):
        for lido in ([8, 0], None):
            with self.subTest(lido=lido):
                link, logs = Link(), []
                link.leituras["AUDIO_MGR_OP_L"] = lido
                t = doa.TravaFeixe(link, log=logs.append, relogio=lambda: 100.0)
                self.assertTrue(t.travar(rad(30)))
                self.assertTrue(t.travado)
                self.assertEqual(link.escritas[-1], ("AUDIO_MGR_OP_L", [8, 0]))
                self.assertEqual(sum("canal 0" in m for m in logs), 1)

    def test_unlock_sequence(self):
        link = Link()
        t = doa.TravaFeixe(link, log=lambda m: None, relogio=lambda: 100.0)
        t.travar(rad(30)); link.escritas.clear()
        self.assertTrue(t.soltar())
        self.assertEqual(link.escritas,
                         [("AEC_FIXEDBEAMSONOFF", [0]), ("AEC_FIXEDBEAMSELEVATION_VALUES", [0.0, 0.0]),
                          ("AUDIO_MGR_OP_R", [7, 3]), ("AUDIO_MGR_OP_L", [8, 0])])
        self.assertFalse(t.travado)

    def test_failed_lock_unlocks(self):
        link = Link(ok=False)
        t = doa.TravaFeixe(link, log=lambda m: None, relogio=lambda: 100.0)
        self.assertFalse(t.travar(rad(30)))
        self.assertFalse(t.travado)
        self.assertEqual(link.escritas[-4:],
                         [("AEC_FIXEDBEAMSONOFF", [0]), ("AEC_FIXEDBEAMSELEVATION_VALUES", [0.0, 0.0]),
                          ("AUDIO_MGR_OP_R", [7, 3]), ("AUDIO_MGR_OP_L", [8, 0])])

    def test_ceiling_unlocks(self):
        agora = {"t": 100.0}
        link = Link()
        t = doa.TravaFeixe(link, log=lambda m: None, teto_s=90, relogio=lambda: agora["t"])
        t.travar(rad(30))
        agora["t"] = 189.0; t.tique(); self.assertTrue(t.travado)
        agora["t"] = 191.0; t.tique(); self.assertFalse(t.travado)

    def test_unlock_when_not_locked_writes_nothing(self):
        link = Link()
        t = doa.TravaFeixe(link, log=lambda m: None, relogio=lambda: 1.0)
        self.assertTrue(t.soltar())
        self.assertEqual(link.escritas, [])


class TestRegra(unittest.TestCase):
    """Regra do ESPHome (dono, 17/11): trava na palavra, solta quando acaba o primeiro pedido."""
    def setUp(self):
        self.link = Link()
        self.d = doa.Direcao()
        for t in (9.0, 9.2, 9.4):
            self.d.alimentar(t, rad(45))
        self.trava = doa.TravaFeixe(self.link, log=lambda m: None, relogio=lambda: 10.0)

    def test_no_lock_without_decision(self):
        r = doa.Regra(doa.Direcao(), self.trava, ligada=lambda: True)
        self.assertIsNone(r.palavra(10.0))
        self.assertEqual(self.link.escritas, [])

    def test_switch_off_never_locks(self):
        r = doa.Regra(self.d, self.trava, ligada=lambda: False)
        self.assertIsNone(r.palavra(10.0))
        self.assertEqual(self.link.escritas, [])

    def test_releases_when_first_answer_ends(self):
        r = doa.Regra(self.d, self.trava, ligada=lambda: True)
        self.assertAlmostEqual(r.palavra(10.0), 45, delta=0.5)  # graus para o anel
        self.assertTrue(self.trava.travado)  # the request pauses do not release it
        r.resposta_acabou()
        self.assertFalse(self.trava.travado)

    def test_answer_end_without_lock_writes_nothing(self):
        r = doa.Regra(self.d, self.trava, ligada=lambda: True)
        r.resposta_acabou()
        self.assertEqual(self.link.escritas, [])

    def test_conversation_close_releases(self):
        r = doa.Regra(self.d, self.trava, ligada=lambda: True)
        r.palavra(10.0)
        r.conversa_fechou()
        self.assertFalse(self.trava.travado)


if __name__ == "__main__":
    unittest.main()


class TestRevisaoDoa(unittest.TestCase):
    def test_theta_is_normalized_to_0_2pi(self):
        link = Link()
        doa.TravaFeixe(link, log=lambda m: None, relogio=lambda: 1.0).travar(rad(-90))
        self.assertAlmostEqual(link.escritas[0][1][0], rad(270))


class TestDoisFeixes(unittest.TestCase):
    def test_two_beam_lock_writes_both_azimuths_and_elevation(self):
        link = Link()
        t = doa.TravaFeixe(link, log=lambda m: None, relogio=lambda: 1.0)
        self.assertTrue(t.travar(rad(140), rad(10), rad(45)))
        self.assertEqual([e[0] for e in link.escritas],
                         ["AEC_FIXEDBEAMSAZIMUTH_VALUES", "AEC_FIXEDBEAMSELEVATION_VALUES", "AEC_FIXEDBEAMSONOFF",
                          "AUDIO_MGR_OP_R", "AUDIO_MGR_OP_L"])
        self.assertAlmostEqual(link.escritas[0][1][0], rad(140)); self.assertAlmostEqual(link.escritas[0][1][1], rad(10))
        self.assertEqual(link.escritas[1][1][0], 0.0); self.assertAlmostEqual(link.escritas[1][1][1], rad(45))
        self.assertEqual(link.escritas[2][1], [1]); self.assertEqual(link.escritas[3][1], [7, 0])
        self.assertEqual(link.escritas[4][1], [6, 0])
        self.assertAlmostEqual(t.theta_a, rad(140)); self.assertAlmostEqual(t.theta_t, rad(10))

    def test_release_resets_elevation(self):
        link, logs = Link(), []
        t = doa.TravaFeixe(link, log=logs.append, relogio=lambda: 1.0)
        t.travar(rad(140), rad(10), rad(45)); link.escritas.clear()
        t.soltar()
        self.assertEqual(link.escritas, [("AEC_FIXEDBEAMSONOFF", [0]), ("AEC_FIXEDBEAMSELEVATION_VALUES", [0.0, 0.0]),
                                         ("AUDIO_MGR_OP_R", [7, 3]), ("AUDIO_MGR_OP_L", [8, 0])])
        self.assertEqual(t.motivo_soltura, "closed")
        self.assertTrue(any("beam_t_deg" in m for m in logs if isinstance(m, str)))

    def test_log_has_no_tv_field_without_tv(self):
        logs = []
        t = doa.TravaFeixe(Link(), log=logs.append, relogio=lambda: 1.0)
        t.travar(rad(30)); t.soltar()
        self.assertFalse(any("beam_t_deg" in m for m in logs))

    def test_decide_retries_without_tv_sector(self):
        d = doa.Direcao()
        for i, g in enumerate((10, 140, 10, 140, 10, 140)):
            d.alimentar(9.0 + i * 0.15, rad(g))
        self.assertIsNone(d.decidir(10.0))
        self.assertAlmostEqual(math.degrees(d.decidir(10.0, excluir=(rad(10), 30))), 140, delta=0.5)

    def test_whole_conversation_lock_survives_first_answer(self):
        link = Link()
        d = doa.Direcao()
        for t in (9.0, 9.2, 9.4):
            d.alimentar(t, rad(140))
        trava = doa.TravaFeixe(link, log=lambda m: None, relogio=lambda: 10.0)
        r = doa.Regra(d, trava, ligada=lambda: True, dois_feixes=lambda: True, tv=lambda: (rad(10), rad(45)))
        self.assertAlmostEqual(r.palavra(10.0), 140, delta=0.5)
        self.assertEqual(r.ultima_decisao, "locked")
        r.resposta_acabou()
        self.assertTrue(trava.travado)
        r.conversa_fechou()
        self.assertFalse(trava.travado)
        self.assertFalse(r.dois_feixes_ativo)

    def test_two_beam_locks_even_with_beam_lock_off(self):
        d = doa.Direcao()
        for t in (9.0, 9.2, 9.4):
            d.alimentar(t, rad(140))
        trava = doa.TravaFeixe(Link(), log=lambda m: None, relogio=lambda: 10.0)
        r = doa.Regra(d, trava, ligada=lambda: False, dois_feixes=lambda: True)
        self.assertIsNotNone(r.palavra(10.0))
        self.assertTrue(trava.travado)

    def test_two_beam_retry_excludes_tv_sector(self):
        d = doa.Direcao()
        for i, g in enumerate((10, 140, 10, 140, 10, 140)):
            d.alimentar(9.0 + i * 0.15, rad(g))
        link = Link()
        trava = doa.TravaFeixe(link, log=lambda m: None, relogio=lambda: 10.0)
        r = doa.Regra(d, trava, ligada=lambda: True, dois_feixes=lambda: True, tv=lambda: (rad(10), rad(45)))
        self.assertAlmostEqual(r.palavra(10.0), 140, delta=0.5)
        self.assertEqual(r.ultima_decisao, "locked_tv_excluded")
        self.assertAlmostEqual(trava.theta_t, rad(10))

    def test_two_beam_lock_failed_reason(self):
        trava = doa.TravaFeixe(Link(), log=lambda m: None, relogio=lambda: 10.0)
        r = doa.Regra(doa.Direcao(), trava, ligada=lambda: True, dois_feixes=lambda: True)
        self.assertIsNone(r.palavra(10.0))
        self.assertEqual(r.ultima_decisao, "lock_failed")
        self.assertFalse(r.dois_feixes_ativo)

    def test_reaponta_needs_three_of_four_far_from_beams(self):
        r = doa.Reaponta()
        for i in range(4):
            r.alimentar(10.0 + i * 0.125, rad(230), 1.0)
        self.assertAlmostEqual(math.degrees(r.decidir(10.4, rad(140), rad(10))) % 360, 230, delta=0.5)
        r.zerar()
        for i in range(4):
            r.alimentar(10.0 + i * 0.125, rad(150), 1.0)
        self.assertIsNone(r.decidir(10.4, rad(140), rad(10)))
        r.zerar()
        for i, e in enumerate((1.0, 0.0, 0.0, 1.0)):
            r.alimentar(10.0 + i * 0.125, rad(230), e)
        self.assertIsNone(r.decidir(10.4, rad(140), rad(10)))
        r.zerar()
        for i in range(4):
            r.alimentar(10.0 + i * 0.125, rad(15), 1.0)  # near T
        self.assertIsNone(r.decidir(10.4, rad(140), rad(10)))
        self.assertAlmostEqual(math.degrees(r.decidir(10.4, rad(140), None)), 15, delta=0.5)

    def test_reaim_rewrites_azimuth_pair_and_logs(self):
        link, logs = Link(), []
        t = doa.TravaFeixe(link, log=logs.append, relogio=lambda: 1.0)
        t.travar(rad(140), rad(10), rad(45)); link.escritas.clear()
        self.assertTrue(t.reapontar(rad(230), causa="follow_up", polls=4))
        self.assertEqual(len(link.escritas), 1)
        self.assertEqual(link.escritas[0][0], "AEC_FIXEDBEAMSAZIMUTH_VALUES")
        self.assertAlmostEqual(link.escritas[0][1][0], rad(230)); self.assertAlmostEqual(link.escritas[0][1][1], rad(10))
        ev = [m for m in logs if isinstance(m, dict)]
        self.assertEqual(ev, [{"two_beam_reaim": {"cause": "follow_up", "from_deg": 140.0, "to_deg": 230.0, "polls": 4}}])
        self.assertAlmostEqual(t.theta_a, rad(230))

    def test_ceiling_rearmed_by_renovar(self):
        agora = {"t": 0.0}
        t = doa.TravaFeixe(Link(), log=lambda m: None, teto_s=90, relogio=lambda: agora["t"])
        t.travar(rad(30))
        agora["t"] = 80.0; t.renovar()
        agora["t"] = 100.0; t.tique(); self.assertTrue(t.travado)
        agora["t"] = 171.0; t.tique(); self.assertFalse(t.travado)
        self.assertEqual(t.motivo_soltura, "ceiling90")

    def test_direction_history_prunes_and_medians(self):
        import os, tempfile
        with tempfile.TemporaryDirectory() as tmp:
            caminho = os.path.join(tmp, "caller_directions.ndjson")
            now = 10 * 86400.0
            doa.registrar_direcao(caminho, rad(100), now - 8 * 86400)  # old, pruned on the next write
            self.assertIsNone(doa.mediana_direcoes(caminho, now))
            for i, g in enumerate((130, 140, 150, 140, 135, 145, 140, 138, 142, 90, 200)):
                doa.registrar_direcao(caminho, rad(g), now - 1000 + i)
            with open(caminho) as f:
                self.assertEqual(len(f.read().splitlines()), 11)
            self.assertAlmostEqual(math.degrees(doa.mediana_direcoes(caminho, now)), 140, delta=3)
            self.assertIsNone(doa.mediana_direcoes(caminho, now + 9 * 86400))  # all too old


class TestLerContinuoDoisFeixes(unittest.TestCase):
    class LinkLeitura:
        def __init__(self): self.limitadas = []
        def read(self, nome): return [1.0]
        def read_bounded(self, nome):
            self.limitadas.append(nome)
            return [1.0, 1.0, 2.0, 3.0] if nome == "AEC_SPENERGY_VALUES" else [0.1, 0.2, 0.3, 0.4]

    class Parar:
        def __init__(self, n): self.n = n
        def wait(self, _):
            self.n -= 1
            return self.n < 0

    def test_two_beam_reads_energy_and_feeds_reaim(self):
        import threading
        from detectores import EnergiaFeixes
        link, en, re, ciclos = self.LinkLeitura(), EnergiaFeixes(), doa.Reaponta(), []
        doa.ler_continuo(link, doa.Direcao(), self.Parar(2), relogio=lambda: 5.0, energia=en, reaponta=re,
                         ativo=lambda: True, ao_ciclo=ciclos.append)
        self.assertEqual(link.limitadas, ["AEC_SPENERGY_VALUES", "AEC_AZIMUTH_VALUES"] * 2)
        self.assertEqual(en.ultimas(), ((1.0, 1.0, 2.0), 5.0))
        self.assertEqual(ciclos, [5.0, 5.0])
        self.assertEqual(list(re._polls)[-1], (5.0, 0.3, 2.0))

    def test_inactive_does_not_touch_the_bounded_reads(self):
        link = self.LinkLeitura()
        doa.ler_continuo(link, doa.Direcao(), self.Parar(2), relogio=lambda: 5.0, ativo=lambda: False)
        self.assertEqual(link.limitadas, [])

    def test_wake_word_direction_excludes_the_tv_sector(self):
        d = doa.Direcao()
        for t in (9.4, 9.6, 9.8, 10.0):
            d.alimentar(t, math.radians(90))
        self.assertAlmostEqual(math.degrees(doa.direcao_da_palavra(d, 10.0, None)), 90, delta=1)
        self.assertIsNone(doa.direcao_da_palavra(d, 10.0, (math.radians(100), 0.0)))
