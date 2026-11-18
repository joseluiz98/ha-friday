import os, tempfile, unittest, wave

import numpy as np

from featcache import ler


def _wav(caminho, amostras):
    with wave.open(caminho, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(np.asarray(amostras, dtype=np.int16).tobytes())


class TestLer(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def _vazio(self):
        p = os.path.join(self.d, "vazio.wav")
        open(p, "wb").close()   # a Pi desligou com a gravação aberta (17/11/2026): 0 bytes
        return p

    def test_wav_bom_volta_as_amostras(self):
        p = os.path.join(self.d, "bom.wav"); _wav(p, [1, -2, 3])
        self.assertEqual(ler(p).tolist(), [1, -2, 3])

    def test_wav_vazio_tolerante_volta_none(self):
        self.assertIsNone(ler(self._vazio(), tolerante=True))

    def test_wav_vazio_estrito_levanta(self):
        with self.assertRaises(EOFError):
            ler(self._vazio())

    def test_cabecalho_cortado_tolerante_volta_none(self):
        p = os.path.join(self.d, "cortado.wav"); _wav(p, [1, 2, 3])
        with open(p, "r+b") as f:
            f.truncate(20)
        self.assertIsNone(ler(p, tolerante=True))

    def test_dados_com_byte_impar_nao_quebram(self):
        p = os.path.join(self.d, "impar.wav"); _wav(p, [5, 6, 7])
        with open(p, "ab") as f:
            f.write(b"\x01")
        self.assertEqual(ler(p).tolist()[:3], [5, 6, 7])


if __name__ == "__main__":
    unittest.main()
