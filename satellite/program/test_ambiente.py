"""cd /homeassistant/tools/satelite/programa && python3 -m unittest -q test_ambiente"""
import datetime, glob, os, tempfile, unittest, wave

import ambiente as a
import configuracao as c

T0 = datetime.datetime(2026, 11, 3, 14, 0, 0)
PCM = b"\x01\x00" * 1280  # 80 ms


class TestInterpretarAte(unittest.TestCase):
    def test_aceita_null_e_futuro_ate_24h(self):
        self.assertIsNone(a.interpretar_ate(None, T0))
        self.assertEqual(a.interpretar_ate("2026-11-03T22:00", T0), "2026-11-03T22:00")
        self.assertEqual(a.interpretar_ate("2026-11-04T14:00", T0), "2026-11-04T14:00")

    def test_recusa(self):
        for ruim in ("2026-11-03T13:59", "2026-11-04T14:01", "amanhã", "2026-11-03 22:00", 5, True):
            with self.assertRaises(ValueError, msg=str(ruim)):
                a.interpretar_ate(ruim, T0)

    def test_porta_aceita_a_chave(self):
        futuro = (datetime.datetime.now() + datetime.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
        novo = c.validar_mudancas(dict(c.PADROES), {"gravar_ambiente_ate": futuro})
        self.assertEqual(novo["gravar_ambiente_ate"], futuro)
        self.assertIsNone(c.validar_mudancas(novo, {"gravar_ambiente_ate": None})["gravar_ambiente_ate"])
        self.assertIsNone(c.PADROES["gravar_ambiente_ate"])
        with self.assertRaises(ValueError):
            c.validar_mudancas(dict(c.PADROES), {"gravar_ambiente_ate": "2020-01-01T00:00"})


class TestJanela(unittest.TestCase):
    """09/11: janela diária guardada na Pi. Grava todo dia pelo relógio local, sem depender de o HA renovar pela rede."""

    def test_interpretar(self):
        self.assertIsNone(a.interpretar_janela(None))
        self.assertEqual(a.interpretar_janela("08:00-22:00"), "08:00-22:00")
        self.assertEqual(a.interpretar_janela("22:00-06:00"), "22:00-06:00")
        for ruim in ("08:00", "8:00-22:00", "08:00-24:00", "08:00-08:00", "x", 5, True, ["08:00", "22:00"]):
            with self.assertRaises(ValueError, msg=str(ruim)):
                a.interpretar_janela(ruim)

    def test_dentro(self):
        d = lambda h, m: datetime.datetime(2026, 11, 9, h, m)
        self.assertEqual([a.dentro_da_janela("08:00-22:00", d(*x)) for x in ((7, 59), (8, 0), (21, 59), (22, 0))],
                         [False, True, True, False])
        self.assertEqual([a.dentro_da_janela("22:00-06:00", d(*x)) for x in ((23, 0), (5, 59), (6, 0), (12, 0))],
                         [True, True, False, False])
        self.assertFalse(a.dentro_da_janela(None, d(12, 0)))

    def test_porta_aceita_a_chave(self):
        novo = c.validar_mudancas(dict(c.PADROES), {"gravar_ambiente_janela": "08:00-22:00"})
        self.assertEqual(novo["gravar_ambiente_janela"], "08:00-22:00")
        self.assertIsNone(c.validar_mudancas(novo, {"gravar_ambiente_janela": None})["gravar_ambiente_janela"])
        self.assertIsNone(c.PADROES["gravar_ambiente_janela"])
        with self.assertRaises(ValueError):
            c.validar_mudancas(dict(c.PADROES), {"gravar_ambiente_janela": "08:00"})


class TestGravador(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.g = a.GravadorAmbiente(self.dir, bloco_s=60)

    def _rodar(self, segundos, ate, inicio=T0):
        for i in range(int(segundos / 0.08)):
            self.g.alimentar(PCM, inicio + datetime.timedelta(seconds=i * 0.08), ate)

    def test_desligado_nao_grava(self):
        self._rodar(5, None)
        self.assertEqual(glob.glob(self.dir + "/*.wav"), [])

    def test_grava_em_blocos_e_para_na_hora(self):
        self._rodar(150, "2026-11-03T14:02")  # 120 s gravados, 2 blocos de 60 s
        self.assertFalse(self.g.gravando)
        arqs = sorted(glob.glob(self.dir + "/*.wav"))
        self.assertEqual([os.path.basename(x) for x in arqs], ["ambiente_20261103_140000.wav", "ambiente_20261103_140100.wav"])
        total = 0
        for x in arqs:
            with wave.open(x) as w:
                self.assertEqual((w.getframerate(), w.getnchannels(), w.getsampwidth()), (16000, 1, 2))
                total += w.getnframes()
        self.assertAlmostEqual(total / 16000, 120, delta=0.2)

    def test_null_para_e_fecha_o_bloco(self):
        self._rodar(3, "2026-11-03T15:00")
        self.assertTrue(self.g.gravando)
        self.g.alimentar(PCM, T0 + datetime.timedelta(seconds=4), None)
        self.assertFalse(self.g.gravando)
        with wave.open(glob.glob(self.dir + "/*.wav")[0]) as w:
            self.assertGreater(w.getnframes(), 0)


class TestGravadorJanela(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.g = a.GravadorAmbiente(self.dir, bloco_s=60)

    def _rodar(self, segundos, inicio, ate=None, janela=None):
        for i in range(int(segundos / 0.08)):
            self.g.alimentar(PCM, inicio + datetime.timedelta(seconds=i * 0.08), ate, janela)

    def test_grava_todo_dia_sem_nova_ordem(self):
        # o caso de 09/11: nenhuma mensagem do HA no dia seguinte, e a Pi grava de novo na janela
        for dia in (T0, T0 + datetime.timedelta(days=1)):
            self._rodar(180, dia - datetime.timedelta(seconds=30), janela="14:00-14:02")
            self.assertFalse(self.g.gravando)
        nomes = sorted(os.path.basename(x) for x in glob.glob(self.dir + "/*.wav"))
        self.assertEqual(nomes, ["ambiente_20261103_140000.wav", "ambiente_20261103_140100.wav",
                                 "ambiente_20261104_140000.wav", "ambiente_20261104_140100.wav"])

    def test_ate_continua_valendo_fora_da_janela(self):
        self._rodar(3, T0, ate="2026-11-03T15:00", janela="08:00-09:00")
        self.assertTrue(self.g.gravando)

    def test_janela_nula_e_ate_nulo_nao_gravam(self):
        self._rodar(3, T0, ate=None, janela=None)
        self.assertEqual(glob.glob(self.dir + "/*.wav"), [])


if __name__ == "__main__":
    unittest.main()
