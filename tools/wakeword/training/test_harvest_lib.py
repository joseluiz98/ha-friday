"""cd /homeassistant/tools/satelite/wakeword-training && python3 -m unittest -q test_harvest_lib"""
import unittest
import harvest_lib as h


class TestHarvestLib(unittest.TestCase):
    def test_nome_unico_evita_colisao_entre_pastas(self):
        self.assertEqual(h.nome_unico("ativou", "/a/20261012_160041.wav"), "ativou__20261012_160041.wav")
        self.assertNotEqual(h.nome_unico("acervo_v", "/b/20261012_160041.wav"),
                            h.nome_unico("ativou", "/a/20261012_160041.wav"))

    def test_ts_do_nome(self):
        self.assertEqual(h.ts_do_nome("/x/contexto_20261021_202454.wav"), "2026-10-21T20:24:54")
        self.assertEqual(h.ts_do_nome("/x/tentativas_friday_20261008_094900.wav"), "2026-10-08T09:49:00")
        self.assertIsNone(h.ts_do_nome("/x/sessao.wav"))
        self.assertIsNone(h.ts_do_nome("/x/99999999_999999.wav"))

    def test_duplicata_fica_a_de_maior_prioridade(self):
        linhas = [("ativou", "/a/1.wav", "AAA"), ("acervo_v", "/b/1.wav", "AAA"), ("quase", "/a/q.wav", "BBB")]
        fontes, desc = h.deduplicar(linhas)
        self.assertEqual(desc, 1)
        self.assertEqual({f["name"] for f in fontes}, {"acervo_v__1.wav", "quase__q.wav"})

    def test_sem_duplicata_nada_some(self):
        fontes, desc = h.deduplicar([("ativou", "/a/1.wav", "A"), ("ativou", "/a/2.wav", "B")])
        self.assertEqual((len(fontes), desc), (2, 0))

    def test_mesma_prioridade_fica_a_primeira(self):
        fontes, desc = h.deduplicar([("ativou", "/a/1.wav", "A"), ("ativou", "/a/copia.wav", "A")])
        self.assertEqual([f["path"] for f in fontes], ["/a/1.wav"]); self.assertEqual(desc, 1)

    def test_ativacao_rotulada_falsa_vence_o_ativou(self):
        linhas = [("ativou", "/a/20261030_104841.wav", "AAA"), ("acervo_f", "/a/20261030_104841.wav", "AAA")]
        fontes, desc = h.deduplicar(linhas)
        self.assertEqual([f["src"] for f in fontes], ["acervo_f"])
        self.assertEqual(fontes[0]["name"], "acervo_f__20261030_104841.wav")
        self.assertEqual(desc, 1)

    def test_falsas_rotuladas(self):
        r = {"_leia": "x", "falsas_ativacoes": ["20261030_143729.wav", "20261030_085000.wav"]}
        self.assertEqual(h.falsas_rotuladas(r), ["20261030_085000.wav", "20261030_143729.wav"])
        self.assertEqual(h.falsas_rotuladas({}), [])

    def test_falsas_rotuladas_recusa_nome_que_nao_e_ativacao(self):
        for ruim in ("../x.wav", "a b.wav", "contexto_20261030_143729.wav", "20261030_143729.wav;rm", "x.mp3"):
            with self.assertRaises(ValueError, msg=ruim):
                h.falsas_rotuladas({"falsas_ativacoes": [ruim]})

    def test_src_desconhecido(self):
        with self.assertRaises(ValueError):
            h.deduplicar([("xx", "/a/1.wav", "A")])



class TestRulingsExtras(unittest.TestCase):  # 06/11: a Sexta em ativa também é rotulada pelo o dono
    def test_nome_da_sexta_vale(self):
        self.assertEqual(h.falsas_rotuladas({"falsas_ativacoes": ["sexta_feira_20261026_083000.wav"]}),
                         ["sexta_feira_20261026_083000.wav"])

    def test_verdadeiras_saem_com_prefixo(self):
        self.assertEqual(h.verdadeiras_rotuladas({"verdadeiras_ativacoes": ["sexta_feira_20261105_135728.wav"]}),
                         ["v:sexta_feira_20261105_135728.wav"])

    def test_nome_de_outro_tipo_recusa(self):
        with self.assertRaises(ValueError):
            h.verdadeiras_rotuladas({"verdadeiras_ativacoes": ["quase_20261105_135728.wav"]})


if __name__ == "__main__":
    unittest.main()
