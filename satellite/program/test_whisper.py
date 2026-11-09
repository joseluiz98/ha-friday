import os
import unittest
import wave

try:
    import numpy as np
except ImportError:  # o HA nao tem numpy
    np = None

if np is not None:
    import whisper

DATA = os.environ.get(
    "WHISPER_DATA",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dados_sussurro"),
)


def _pcm(x, lead=8000):
    """Sinais estacionarios nao tem piso abaixo do som: poe um prefixo quase mudo (ruido de fundo)."""
    rng = np.random.default_rng(99)
    x = np.concatenate([rng.standard_normal(lead) * 10, x])
    return np.clip(x, -32768, 32767).astype("<i2").tobytes()


def _noise_band(lo, hi, n=16000, amp=3000, seed=1):
    rng = np.random.default_rng(seed)
    spec = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / 16000)
    spec[(f < lo) | (f > hi)] = 0
    y = np.fft.irfft(spec, n)
    return y / np.abs(y).max() * amp


def _wav(path, n=32000):
    with wave.open(path) as w:
        return w.readframes(n)


@unittest.skipUnless(np is not None, "numpy indisponivel")
class TestWhisper(unittest.TestCase):
    def test_tom_grave_e_muito_negativo(self):
        t = np.arange(16000) / 16000
        r = whisper.whisper_ratio_db(_pcm(3000 * np.sin(2 * np.pi * 200 * t)))
        self.assertIsNotNone(r)
        self.assertLess(r, -20)

    def test_ruido_agudo_e_muito_positivo(self):
        r = whisper.whisper_ratio_db(_pcm(_noise_band(2000, 6000)))
        self.assertIsNotNone(r)
        self.assertGreater(r, 20)

    def test_silencio_devolve_none(self):
        self.assertIsNone(whisper.whisper_ratio_db(bytes(32000)))

    def test_poucos_quadros_devolve_none(self):
        rng = np.random.default_rng(2)
        pcm = _pcm(rng.standard_normal(768) * 3000, lead=512)  # 1280 amostras = 4 quadros
        self.assertIsNone(whisper.whisper_ratio_db(pcm, min_frames=6))
        self.assertIsInstance(whisper.whisper_ratio_db(pcm, min_frames=2), float)

    def test_piso_dado_muda_a_selecao(self):
        pcm = _pcm(_noise_band(2000, 6000))
        self.assertIsNotNone(whisper.whisper_ratio_db(pcm))
        self.assertIsNone(whisper.whisper_ratio_db(pcm, floor_rms=10_000))

    def test_gravacoes_reais(self):
        d = os.path.join(DATA, "ao_vivo_20261019")
        if not os.path.isdir(d):
            self.skipTest("sem gravacoes")
        s = whisper.whisper_ratio_db(_wav(os.path.join(d, "20261019_151938.wav")))
        n = whisper.whisper_ratio_db(_wav(os.path.join(d, "20261019_151537.wav")))
        self.assertGreater(s, 10)
        self.assertLess(n, 0)


if __name__ == "__main__":
    unittest.main()
