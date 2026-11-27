"""Camada da placa de áudio (spec 2026-11-17 xvf3800-support, Task 3).
cd satellite/program && python3 -m unittest -q test_placa"""
import shutil
import os
import tempfile
import types
import unittest

import hardware
import placa


class Resp:
    def __init__(self, out="", rc=0):
        self.stdout, self.returncode = out, rc


class Exec:
    """Grava as chamadas e devolve a saída pedida."""
    def __init__(self, out=""):
        self.chamadas, self.out = [], out

    def __call__(self, argv, **kw):
        self.chamadas.append(argv)
        return Resp(self.out)


class FakeLink:
    def __init__(self, lido=None):
        self.escritas, self.lido, self.leituras = [], lido, []

    def write(self, nome, valores):
        self.escritas.append((nome, list(valores)))
        return True

    def read(self, nome):
        self.leituras.append(nome)
        return self.lido


class TestLerPlaca(unittest.TestCase):
    def _arq(self, texto):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, "audio_board")
        if texto is not None:
            with open(p, "w") as f:
                f.write(texto)
        return p

    def test_reads_the_three_values(self):
        for v in ("xvf3800", "jabra", "none"):
            self.assertEqual(placa.ler_placa(self._arq(v + "\n")), v)

    def test_missing_or_unknown_is_jabra(self):
        """Satélite antigo, sem o arquivo: é o Jabra de sempre (anti-objetivo do spec)."""
        self.assertEqual(placa.ler_placa(self._arq(None)), "jabra")
        self.assertEqual(placa.ler_placa(self._arq("outra\n")), "jabra")


class TestVolumeXvf(unittest.TestCase):
    def test_writes_the_raw_level(self):
        for cru in (0, 26, 43, 45, 60):
            ex = Exec()
            placa.MixerXvf(executar=ex).escrever(cru)
            self.assertEqual(ex.chamadas, [["amixer", "-q", "-c", "Array", "sset", "PCM", f"{cru},{cru}"]])

    def test_write_clamps(self):
        ex = Exec()
        m = placa.MixerXvf(executar=ex)
        m.escrever(99)
        m.escrever(-3)
        self.assertEqual([c[-1] for c in ex.chamadas], ["60,60", "0,0"])

    def test_reads_the_raw_level(self):
        for cru in (0, 41, 45, 59, 60):
            saida = f"Simple mixer control 'PCM',0\n  Front Left: Playback {cru} [67%] [-20.00dB]\n"
            self.assertEqual(placa.MixerXvf(executar=Exec(saida)).ler(), cru)

    def test_read_without_position_raises(self):
        with self.assertRaises(RuntimeError):
            placa.MixerXvf(executar=Exec("nada")).ler()

    def test_read_uses_card_array_pcm(self):
        ex = Exec("  Front Left: Playback 40 [67%]\n")
        placa.MixerXvf(executar=ex).ler()
        self.assertEqual(ex.chamadas[0], ["amixer", "-c", "Array", "sget", "PCM"])


class TestMestreXvf(unittest.TestCase):
    """18/11: o XVF tem um segundo controle de reprodução (PCM índice 1) que nasce em −20 dB e o ALSA restaura assim a
    cada boot. Ele fica sempre em 0 dB; o volume da casa segue só no PCM índice 0."""
    def test_master_goes_to_0db(self):
        ex = Exec()
        self.assertTrue(placa.MixerXvf(executar=ex).fixar_mestre())
        self.assertEqual(ex.chamadas, [["amixer", "-q", "-c", "Array", "cset", "name=PCM Playback Volume,index=1", "60"]])

    def test_master_failure_is_false_not_raised(self):
        def quebra(*a, **k):
            raise OSError("sem amixer")
        self.assertFalse(placa.MixerXvf(executar=quebra).fixar_mestre())


class TestMudoXvf(unittest.TestCase):
    def test_mudo_xvf_writes_pin(self):
        link = FakeLink()
        m = placa.MudoXvf(link)
        self.assertTrue(m.aplicar(True))
        self.assertTrue(m.aplicar(False))
        self.assertEqual(link.escritas, [("GPO_WRITE_VALUE", [30, 1]), ("GPO_WRITE_VALUE", [30, 0])])

    def test_mudo_xvf_read(self):
        self.assertIs(placa.MudoXvf(FakeLink([0, 1])).ler(), True)
        self.assertIs(placa.MudoXvf(FakeLink([1, 0])).ler(), False)
        link = FakeLink([0, 1])
        placa.MudoXvf(link).ler()
        self.assertEqual(link.leituras, ["GPO_READ_VALUES"])

    def test_mudo_xvf_read_unknown_is_none(self):
        self.assertIsNone(placa.MudoXvf(FakeLink(None)).ler())
        self.assertIsNone(placa.MudoXvf(FakeLink([1])).ler())

    def test_write_failure_is_false_not_raised(self):
        class Ruim(FakeLink):
            def write(self, nome, valores):
                return False
        self.assertFalse(placa.MudoXvf(Ruim()).aplicar(True))
        self.assertFalse(placa.MudoXvf(None).aplicar(True))
        self.assertIsNone(placa.MudoXvf(None).ler())


class TestMontar(unittest.TestCase):
    def test_jabra_assembly_is_todays(self):
        c = placa.montar("jabra")
        self.assertIsInstance(c["mixer"], hardware.Mixer)
        self.assertEqual((c["mixer"].card, c["mixer"].controle), ("USB", "PCM"))
        self.assertIsInstance(c["music_mixer"], hardware.MusicMixer)
        self.assertEqual((c["music_mixer"].card, c["music_mixer"].control), ("USB", "Music"))
        self.assertIsInstance(c["controle_mudo"], hardware.ControleMudo)
        self.assertEqual((c["controle_mudo"].card, c["controle_mudo"].control), ("USB", "Headset"))
        self.assertIs(c["botao"], hardware.Botao)
        self.assertEqual((c["fine_mixer"].card, c["fine_mixer"].control, c["escala"]), ("USB", "Fine", "jabra"))

    def test_none_assembles_like_jabra(self):
        """`none` não sobe o programa; se alguém montar assim mesmo, nada muda para o Jabra."""
        self.assertIs(placa.montar("none")["botao"], hardware.Botao)

    def test_xvf_assembly(self):
        link = FakeLink()
        c = placa.montar("xvf3800", link=link)
        self.assertIsInstance(c["mixer"], placa.MixerXvf)
        self.assertEqual(c["mixer"].card, "Array")
        self.assertIsInstance(c["music_mixer"], hardware.MusicMixer)
        self.assertEqual(c["music_mixer"].card, "Array")
        self.assertIsInstance(c["controle_mudo"], placa.MudoXvf)
        self.assertIs(c["controle_mudo"]._link, link)
        self.assertIsNone(c["botao"])
        self.assertEqual((c["fine_mixer"].card, c["escala"]), ("Array", "xvf3800"))


if __name__ == "__main__":
    unittest.main()
