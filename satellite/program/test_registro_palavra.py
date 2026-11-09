"""cd /homeassistant/tools/satelite/programa && python3 -m unittest test_registro_palavra -v"""
import datetime, json, os, tempfile, unittest
import registro_palavra as rp


class TestLinha(unittest.TestCase):
    def test_campos(self):
        t = datetime.datetime(2026, 10, 15, 18, 0, 5)
        d = json.loads(rp.linha("quase", 0.4567, 0.91, "/x/quase_1.wav", t))
        self.assertEqual(d, {"ts": "2026-10-15T18:00:05", "tipo": "quase", "nota": 0.46, "voz": 0.91,
                             "arquivo": "/x/quase_1.wav"})

    def test_sem_arquivo_nao_tem_a_chave(self):
        self.assertNotIn("arquivo", json.loads(rp.linha("sem_voz", 0.9, 0.1)))

    def test_barrada_pelo_verificador(self):
        d = json.loads(rp.linha("barrada_verificador", 0.123, 0.9, "/x/barrada_1.wav"))
        self.assertEqual((d["tipo"], d["nota"], d["arquivo"]), ("barrada_verificador", 0.12, "/x/barrada_1.wav"))

    def test_tipo_invalido(self):
        with self.assertRaises(ValueError):
            rp.linha("outro", 0.5, 0.5)


    def test_tipo_sussurro_e_razao(self):
        d = json.loads(rp.linha("sussurro", 0.8, 0.2, whisper_ratio_db=12.34))
        self.assertEqual((d["tipo"], d["whisper_ratio_db"]), ("sussurro", 12.3))

    def test_sem_razao_nao_tem_campo(self):
        self.assertNotIn("whisper_ratio_db", json.loads(rp.linha("ativou", 0.8, 0.9)))


class TestQuaseVale(unittest.TestCase):
    def test_so_abaixo_do_limiar_e_com_voz(self):
        self.assertTrue(rp.quase_vale({"nota": 0.45, "voz": 0.9}, 0.5, 0.6))
        self.assertFalse(rp.quase_vale({"nota": 0.9, "voz": 0.1}, 0.5, 0.6))   # é o "sem_voz", já registrado
        self.assertFalse(rp.quase_vale({"nota": 0.45, "voz": 0.3}, 0.5, 0.6))
        self.assertFalse(rp.quase_vale(None, 0.5, 0.6))


class TestRegistro(unittest.TestCase):
    def test_acrescenta_linhas(self):
        with tempfile.TemporaryDirectory() as d:
            r = rp.Registro(os.path.join(d, "e.ndjson"))
            r.escrever("a"); r.escrever("b")
            self.assertEqual(open(os.path.join(d, "e.ndjson")).read(), "a\nb\n")

    def test_falha_de_escrita_nao_levanta_e_avisa_uma_vez(self):
        avisos = []
        r = rp.Registro("/inexistente/x/e.ndjson", avisar=avisos.append)
        r.escrever("a"); r.escrever("b")
        self.assertEqual(len(avisos), 1)


class TestLinhaFala(unittest.TestCase):
    def test_quase_de_palavra_extra(self):
        d = json.loads(rp.linha("quase", 0.42, 0.9, "/x/sexta_feira_quase_1.wav", palavra="sexta_feira"))
        self.assertEqual((d["tipo"], d["palavra"]), ("quase", "sexta_feira"))
        self.assertEqual(rp.prefixo_quase_extra("sexta_feira"), "sexta_feira_quase_")
        self.assertFalse(rp.prefixo_quase_extra("sexta_feira").startswith("quase_"))

    def test_paciencia_de_palavra_extra(self):
        # 09/11: rejeição por paciência (nota e voz passaram, faltou o 2º quadro) ganha linha e clipe próprios
        d = json.loads(rp.linha("paciencia", 0.55, 1.0, "/x/sexta_feira_paciencia_1.wav", palavra="sexta_feira"))
        self.assertEqual((d["tipo"], d["palavra"], d["arquivo"]), ("paciencia", "sexta_feira", "/x/sexta_feira_paciencia_1.wav"))
        self.assertEqual(rp.prefixo_paciencia_extra("sexta_feira"), "sexta_feira_paciencia_")

    def test_linha_fala(self):
        import datetime, json
        t = json.loads(rp.linha_fala({"pico_base": 0.12, "verificador_consultas": 0, "nota_verificador": 0.0,
                                      "voz": 0.95, "duracao_s": 0.24, "ativou": False},
                                     agora=datetime.datetime(2026, 10, 20, 18, 0, 1)))
        self.assertEqual(t["ts"], "2026-10-20T18:00:01")
        self.assertEqual(t["pico_base"], 0.12)
        self.assertFalse(t["ativou"])

    def test_arquivo_separado(self):
        self.assertNotEqual(rp.ARQUIVO_FALAS, rp.ARQUIVO)


class TestLinhaPalavra(unittest.TestCase):
    AGORA = datetime.datetime(2026, 10, 24, 12, 0, 0)

    def test_linha_sem_palavra_igual_a_hoje(self):
        d = json.loads(rp.linha("ativou", 0.9, 0.8, "a.wav", agora=self.AGORA))
        self.assertNotIn("palavra", d)
        self.assertEqual(set(d), {"ts", "tipo", "nota", "voz", "arquivo"})

    def test_linha_sombra_com_palavra(self):
        d = json.loads(rp.linha("sombra", 0.71, 0.9, "s.wav", agora=self.AGORA, palavra="sexta_feira"))
        self.assertEqual((d["tipo"], d["palavra"]), ("sombra", "sexta_feira"))
        self.assertIn("sombra", rp.TIPOS)
