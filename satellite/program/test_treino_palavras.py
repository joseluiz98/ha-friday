"""O treino por palavra (spec 2026-11-23-voice-learning-extra-words) sem openWakeWord: os módulos pesados são
substituídos por dublês e o extrator por um falso. cd satellite/program && python3 -m unittest test_treino_palavras -v"""
import json, os, sys, tempfile, unittest
from unittest import mock

PESADOS = ("numpy", "scipy", "scipy.io", "scipy.io.wavfile", "threadpoolctl", "openwakeword", "openwakeword.model",
           "openwakeword.vad", "openwakeword.custom_verifier_model")
tv = None


def setUpModule():
    """Os dublês valem só neste módulo de teste: a suíte do satélite roda tudo num processo e outros testes
    importam o numpy de verdade (ou esperam que ele não exista)."""
    global tv, _dubles
    _dubles = mock.patch.dict(sys.modules, {n: mock.MagicMock() for n in PESADOS})
    _dubles.start()
    sys.modules.pop("treino_verificador", None)
    import treino_verificador
    tv = treino_verificador


def tearDownModule():
    sys.modules.pop("treino_verificador", None)
    _dubles.stop()


def falso(pedacos=40):
    """Extrator falso: cada arquivo tem `pedacos` pedaços; a nota base é 0,9 no meio e 0 nas pontas."""
    def extrair(_caminho):
        base = [0.0] * pedacos
        base[pedacos // 2] = 0.9
        return {"base": base, "vad": [0.9] * pedacos, "feats": [[0.0]] * pedacos}
    return extrair


class TestClipesPorPalavra(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory(); self.addCleanup(self._t.cleanup)
        self.raiz = self._t.name
        self._patch = mock.patch.object(tv, "RAIZ", self.raiz); self._patch.start(); self.addCleanup(self._patch.stop)
        nova = os.path.join(self.raiz, "bia", "novo_x"); os.makedirs(nova)
        open(os.path.join(nova, "sessao.wav"), "wb").close()
        marcas = [{"nome": "tentativa_01", "ini": 0, "fim": 10}, {"nome": "tentativa_02", "ini": 10, "fim": 20},
                  {"nome": "sexta_feira__tentativa_01", "ini": 20, "fim": 30, "palavra": "sexta_feira"},
                  {"nome": "fala_livre", "ini": 30, "fim": 40}]
        with open(os.path.join(nova, "marcas.json"), "w") as f:
            json.dump(marcas, f)
        self.nova = nova

    def clipes(self, palavra):
        return tv.clipes("bia", self.nova, falso(), {}, (), palavra)

    def test_friday_usa_as_marcas_da_friday_como_positivos(self):
        cl = self.clipes("friday")
        self.assertEqual(sorted(c["grupo"] for c in cl if c["cat"] == "nova"), ["bia:tentativa_01", "bia:tentativa_02"])

    def test_a_outra_palavra_vira_negativo(self):
        cl = self.clipes("friday")
        outra = [c for c in cl if c["grupo"] == "bia:sexta_feira__tentativa_01"]
        self.assertEqual([(c["cat"], c["rotulo"]) for c in outra], [("falsas", 0)])

    def test_sexta_feira_usa_as_dela_e_a_friday_vira_negativo(self):
        cl = self.clipes("sexta_feira")
        self.assertEqual([c["grupo"] for c in cl if c["cat"] == "nova"], ["bia:sexta_feira__tentativa_01"])
        self.assertEqual(sorted(c["grupo"] for c in cl if c["cat"] == "falsas"), ["bia:tentativa_01", "bia:tentativa_02"])

    def test_fala_livre_e_negativo_para_as_duas(self):
        for p in ("friday", "sexta_feira"):
            self.assertEqual(len([c for c in self.clipes(p) if c["cat"] == "fala_livre"]), 1)

    def test_acervo_do_jose_so_vale_para_a_friday(self):
        jose = os.path.join(self.raiz, "jose", "historico", "ativacoes"); os.makedirs(jose)
        open(os.path.join(jose, "a.wav"), "wb").close()
        self.assertEqual(len([c for c in self.clipes("friday") if c["cat"] == "verdadeiras"]), 1)
        self.assertEqual(len([c for c in self.clipes("sexta_feira") if c["cat"] == "verdadeiras"]), 0)

    def test_marcas_antigas_sem_palavra_sao_da_friday(self):
        with open(os.path.join(self.nova, "marcas.json"), "w") as f:
            json.dump([{"nome": "tentativa_01", "ini": 0, "fim": 10}], f)
        self.assertEqual(len([c for c in self.clipes("friday") if c["cat"] == "nova"]), 1)
        self.assertEqual(len([c for c in self.clipes("sexta_feira") if c["cat"] == "nova"]), 0)


if __name__ == "__main__":
    unittest.main()
