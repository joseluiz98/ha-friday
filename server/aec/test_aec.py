"""Testes do aec.py. Rodam dentro da imagem (precisam de numpy/scipy; Speex e WebRTC só se instalados):
sudo docker run --rm --entrypoint python -v /opt/friday/aec:/t -w /t friday-aec:1 -m unittest -q test_aec"""
import unittest

import numpy as np

import aec

RNG = np.random.default_rng(7)


def music_like(n):
    return (RNG.standard_normal(n) * 3000).astype(np.int16)


def echo_of(ref, k, delay, gain=0.4):
    """Microfone que ouve a referência com atraso acústico `delay` e começou `k` amostras depois dela."""
    n = len(ref) - k
    x = np.zeros(n)
    x[delay:] = gain * ref[k:k + n - delay]
    return (x + RNG.standard_normal(n) * 30).astype(np.int16)


class TestAlign(unittest.TestCase):
    def test_find_lag_recovers_offset_plus_delay(self):
        ref = music_like(16000 * 8)
        mic = echo_of(ref, 16000 * 2, 800)  # 2 s de vantagem da referência + 50 ms acústicos
        self.assertEqual(aec.find_lag(mic, ref), 16000 * 2 - 800)

    def test_align_puts_echo_after_reference(self):
        ref = np.arange(1, 1001, dtype=np.int16)
        out = aec.align(ref, 500, 100, advance_s=0)
        self.assertEqual(out[0], ref[500])
        out = aec.align(ref, 500, 100, advance_s=0.01)  # 160 amostras de vantagem: a referência vem do futuro
        self.assertEqual(out[0], ref[660])
        out = aec.align(ref, -200, 300, advance_s=0.01)  # microfone começou antes da referência: começo zerado
        self.assertEqual(out[39], 0)
        self.assertEqual(out[40], ref[0])

    def test_ref_from_raw_downmix_and_rate(self):
        st = np.zeros((4800, 2), dtype=np.int16)
        st[:, 0], st[:, 1] = 1000, 3000
        r = aec.ref_from_raw(st.tobytes())
        self.assertEqual(len(r), 1600)
        self.assertTrue(abs(int(r[800]) - 2000) <= 2)


class TestMetrics(unittest.TestCase):
    def test_mask_excludes_speech_windows(self):
        m = aec.echo_mask(16000 * 20, [5.0])
        self.assertTrue(m[int(4.7 * 16000)])
        self.assertFalse(m[int(4.9 * 16000)])
        self.assertFalse(m[int(9.9 * 16000)])
        self.assertTrue(m[int(10.1 * 16000)])

    def test_erle_halving_amplitude_is_6db(self):
        x = music_like(16000)
        self.assertAlmostEqual(aec.erle_db(x, (x // 2).astype(np.int16), np.ones(16000, bool)), 6.0, delta=0.1)


class TestCancellers(unittest.TestCase):
    def setUp(self):
        self.ref = music_like(16000 * 12)
        self.mic = echo_of(self.ref, 16000, 480)
        k = aec.find_lag(self.mic, self.ref)
        self.al = aec.align(self.ref, k, len(self.mic))
        self.mask = np.zeros(len(self.mic), bool)
        self.mask[16000 * 6:] = True  # depois de convergir

    def test_speex_removes_linear_echo(self):
        try:
            y = aec.speex(self.mic, self.al, 4096)
        except OSError:
            self.skipTest("libspeexdsp ausente")
        self.assertGreater(aec.erle_db(self.mic, y, self.mask), 15)

    def test_webrtc_removes_linear_echo(self):
        try:
            import livekit  # noqa: F401
        except ImportError:
            self.skipTest("livekit ausente")
        y = aec.webrtc(self.mic, self.al)
        self.assertGreater(aec.erle_db(self.mic, y, self.mask), 10)


if __name__ == "__main__":
    unittest.main()
