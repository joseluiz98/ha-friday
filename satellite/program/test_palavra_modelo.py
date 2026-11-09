"""cd /homeassistant/tools/satelite/programa && python3 -m unittest test_palavra_modelo -v"""
import json, os, tempfile, unittest
import palavra_modelo as pm


class Fabrica:
    def __init__(self, falha_com_verificador=False):
        self.chamadas, self.falha = [], falha_com_verificador

    def __call__(self, **kw):
        self.chamadas.append(kw)
        if self.falha and "custom_verifier_models" in kw:
            raise ValueError("pickle quebrado")
        return ("modelo", tuple(sorted(kw)))


class Verificador:
    def __init__(self, p): self.p, self.classes_ = p, [0, 1]
    def predict_proba(self, x): return [[1 - self.p, self.p]]


class ModeloFalso:
    def __init__(self, verificadores): self.custom_verifier_models = verificadores


class TestEspiar(unittest.TestCase):
    """20/10: saber quando o verificador foi consultado e a nota que deu, sem rodar nada a mais."""
    def test_anota_a_consulta_e_devolve_o_mesmo_resultado(self):
        m = ModeloFalso({"friday": Verificador(0.07)})
        esp = pm.espiar(m)
        self.assertEqual(esp.ler(), (False, 0.0))
        self.assertEqual(m.custom_verifier_models["friday"].predict_proba("x")[0][-1], 0.07)
        self.assertEqual(esp.ler(), (True, 0.07))
        self.assertEqual(esp.ler(), (False, 0.0))  # ler zera
        self.assertEqual(m.custom_verifier_models["friday"].classes_, [0, 1])  # o resto do objeto passa direto

    def test_sem_verificador_nao_ha_espiao(self):
        self.assertIsNone(pm.espiar(ModeloFalso({})))


class TestChave(unittest.TestCase):
    def test_arquivo_trocado_muda_a_chave(self):
        """Revisão final (I2): o 2º cadastro aprovado troca o .pkl no mesmo caminho e com o mesmo limiar."""
        with tempfile.TemporaryDirectory() as d:
            v = os.path.join(d, "friday_verificador.pkl")
            open(v, "wb").write(b"um")
            cfg = {"verificador": v, "limiar_verificador": 0.5}
            antes = pm.chave(cfg)
            novo = os.path.join(d, "tmp.pkl"); open(novo, "wb").write(b"outro")
            os.utime(novo, ns=(os.stat(v).st_mtime_ns + 10**9,) * 2)
            os.replace(novo, v)
            self.assertNotEqual(pm.chave(cfg), antes)


class TestCriar(unittest.TestCase):
    def test_sem_verificador_e_o_detector_de_hoje(self):
        f = Fabrica()
        _, com = pm.criar(f, {"verificador": None, "limiar_verificador": 0.3}, avisar=lambda t: None)
        self.assertFalse(com)
        self.assertNotIn("custom_verifier_models", f.chamadas[0])

    def test_com_verificador(self):
        with tempfile.NamedTemporaryFile(suffix=".pkl") as v:
            f = Fabrica()
            _, com = pm.criar(f, {"verificador": v.name, "limiar_verificador": 0.2}, avisar=lambda t: None)
            self.assertTrue(com)
            self.assertEqual(f.chamadas[0]["custom_verifier_models"], {"friday": v.name})
            self.assertEqual(f.chamadas[0]["custom_verifier_threshold"], 0.2)

    def test_verificador_inexistente_cai_no_detector_de_hoje(self):
        avisos, f = [], Fabrica()
        _, com = pm.criar(f, {"verificador": "/nao/existe.pkl", "limiar_verificador": 0.3}, avisar=avisos.append)
        self.assertFalse(com); self.assertEqual(len(avisos), 1)
        self.assertNotIn("custom_verifier_models", f.chamadas[-1])

    def test_verificador_corrompido_cai_no_detector_de_hoje(self):
        with tempfile.NamedTemporaryFile(suffix=".pkl") as v:
            avisos, f = [], Fabrica(falha_com_verificador=True)
            _, com = pm.criar(f, {"verificador": v.name, "limiar_verificador": 0.3}, avisar=avisos.append)
            self.assertFalse(com); self.assertIn("pickle quebrado", avisos[0])

    def test_chave_muda_com_as_duas_configs(self):
        self.assertNotEqual(pm.chave({"verificador": "a", "limiar_verificador": 0.3}),
                            pm.chave({"verificador": "a", "limiar_verificador": 0.2}))


class FabricaNomes:
    """Fábrica falsa que expõe `models` como o Model do openWakeWord (nome do arquivo sem .onnx)."""
    def __init__(self, falha_com_extras=False):
        self.chamadas, self.falha = [], falha_com_extras

    def __call__(self, **kw):
        self.chamadas.append(kw)
        if self.falha and len(kw["wakeword_models"]) > 1:
            raise RuntimeError("onnx quebrado")
        m = type("M", (), {})()
        m.models = {os.path.splitext(os.path.basename(p))[0]: object() for p in kw["wakeword_models"]}
        return m


def _extra(d, nome, modo="sombra", existe=True, limiar=0.5):
    arq = os.path.join(d, nome + ".onnx")
    if existe:
        open(arq, "wb").write(b"x")
    return {"nome": nome, "arquivo": arq, "limiar": limiar, "modo": modo}


class TestPalavrasExtras(unittest.TestCase):
    def test_criar_inclui_extras_existentes_inclusive_desligadas(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = _extra(d, "sexta_feira", "sombra"), _extra(d, "friday_candidate", "desligada")
            f = FabricaNomes()
            m, _ = pm.criar(f, {"verificador": None, "palavras_extras": [a, b]}, avisar=lambda t: None)
            self.assertEqual(f.chamadas[0]["wakeword_models"], [pm.MODELO_BASE, a["arquivo"], b["arquivo"]])
            self.assertEqual(pm.carregadas(m), {"friday", "sexta_feira", "friday_candidate"})

    def test_extra_ausente_ignorada_e_avisada(self):
        with tempfile.TemporaryDirectory() as d:
            a = _extra(d, "sexta_feira", existe=False)
            avisos, f = [], FabricaNomes()
            m, _ = pm.criar(f, {"verificador": None, "palavras_extras": [a]}, avisar=avisos.append)
            self.assertEqual(f.chamadas[0]["wakeword_models"], [pm.MODELO_BASE])
            self.assertEqual(json.loads(avisos[0]), {"palavra_extra_ignorada": "sexta_feira", "motivo": "arquivo ausente"})
            self.assertEqual(pm.carregadas(m), {"friday"})

    def test_fabrica_falha_com_extras_recai_so_friday(self):
        with tempfile.TemporaryDirectory() as d:
            a = _extra(d, "sexta_feira")
            avisos, f = [], FabricaNomes(falha_com_extras=True)
            m, _ = pm.criar(f, {"verificador": None, "palavras_extras": [a]}, avisar=avisos.append)
            self.assertEqual(pm.carregadas(m), {"friday"})
            av = json.loads(avisos[-1])
            self.assertEqual(av["palavra_extra_ignorada"], "todas")
            self.assertTrue(av["motivo"].startswith("RuntimeError: "))

    def test_sem_extras_igual_a_hoje(self):
        f = FabricaNomes()
        pm.criar(f, {"verificador": None}, avisar=lambda t: None)
        self.assertEqual(f.chamadas[0]["wakeword_models"], [pm.MODELO_BASE])

    def test_chave_ignora_modo_e_limiar(self):
        """Foco 3: trocar o modo não recria o modelo."""
        with tempfile.TemporaryDirectory() as d:
            a = _extra(d, "sexta_feira", "sombra", limiar=0.5)
            antes = pm.chave({"verificador": None, "palavras_extras": [a]})
            depois = pm.chave({"verificador": None, "palavras_extras": [dict(a, modo="ativa", limiar=0.7)]})
            self.assertEqual(antes, depois)
            self.assertEqual(pm.chave({"verificador": None, "modo_friday": "ativa"}),
                             pm.chave({"verificador": None, "modo_friday": "desligada"}))

    def test_chave_muda_com_arquivo_novo(self):
        with tempfile.TemporaryDirectory() as d:
            a = _extra(d, "sexta_feira")
            antes = pm.chave({"verificador": None, "palavras_extras": [a]})
            novo = os.path.join(d, "tmp.onnx"); open(novo, "wb").write(b"outro")
            os.utime(novo, ns=(os.stat(a["arquivo"]).st_mtime_ns + 10**9,) * 2)
            os.replace(novo, a["arquivo"])
            self.assertNotEqual(pm.chave({"verificador": None, "palavras_extras": [a]}), antes)
            self.assertNotEqual(pm.chave({"verificador": None, "palavras_extras": []}), antes)


class FabricaVerif:
    """Fábrica com o mesmo comportamento do openWakeWord 0.6: guarda os verificadores do construtor em
    `custom_verifier_models` e RECUSA mais de um (a conferência fica dentro do laço que carrega os modelos)."""
    def __init__(self, recusa=None):
        self.chamadas, self.recusa = [], recusa

    def __call__(self, **kw):
        self.chamadas.append(kw)
        v = kw.get("custom_verifier_models") or {}
        if len(v) > 1:
            raise ValueError("Custom verifier models were provided, but some were not matched with a base model!")
        m = type("M", (), {})()
        m.models = {os.path.splitext(os.path.basename(p))[0]: object() for p in kw["wakeword_models"]}
        m.custom_verifier_models = {k: ("pkl", p) for k, p in v.items()}
        return m


def _carregar(caminho):
    if open(caminho, "rb").read() == b"quebrado":
        raise ValueError("pickle quebrado")
    return ("pkl", caminho)


class TestVerificadorDaExtra(unittest.TestCase):
    """02/11: palavra extra com verificador próprio (Sexta: ensaio offline tirou 95% dos disparos falsos). O modelo
    nasce só com o da Friday e o da extra entra depois em `custom_verifier_models` (bug do openWakeWord 0.6)."""
    def _cfg(self, d, friday=True, conteudo=b"s"):
        vf_ = os.path.join(d, "friday_verificador.pkl"); open(vf_, "wb").write(b"f")
        vs = os.path.join(d, "sexta_feira_verificador.pkl"); open(vs, "wb").write(conteudo)
        e = dict(_extra(d, "sexta_feira"), verificador=vs)
        return {"verificador": vf_ if friday else None, "limiar_verificador": 0.5, "palavras_extras": [e]}, vf_, vs

    def test_os_dois_verificadores(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, vf_, vs = self._cfg(d)
            f = FabricaVerif()
            m, com = pm.criar(f, cfg, avisar=lambda t: None, carregar=_carregar)
            self.assertTrue(com)
            self.assertEqual(f.chamadas[-1]["custom_verifier_models"], {"friday": vf_})
            self.assertEqual(m.custom_verifier_models, {"friday": ("pkl", vf_), "sexta_feira": ("pkl", vs)})

    def test_so_o_da_extra(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, _, vs = self._cfg(d, friday=False)
            f = FabricaVerif()
            m, com = pm.criar(f, cfg, avisar=lambda t: None, carregar=_carregar)
            self.assertFalse(com)  # `com` continua dizendo se a FRIDAY tem verificador
            self.assertEqual(f.chamadas[-1]["custom_verifier_threshold"], 0.5)
            self.assertEqual(m.custom_verifier_models, {"sexta_feira": ("pkl", vs)})

    def test_o_da_extra_quebrado_mantem_o_da_friday(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, vf_, _ = self._cfg(d, conteudo=b"quebrado")
            avisos, f = [], FabricaVerif()
            m, com = pm.criar(f, cfg, avisar=avisos.append, carregar=_carregar)
            self.assertTrue(com)
            self.assertEqual(m.custom_verifier_models, {"friday": ("pkl", vf_)})
            self.assertIn("sexta_feira", avisos[0])

    def test_o_da_extra_inexistente_e_avisado(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, vf_, vs = self._cfg(d)
            os.remove(vs)
            avisos, f = [], FabricaVerif()
            m, _ = pm.criar(f, cfg, avisar=avisos.append, carregar=_carregar)
            self.assertEqual(m.custom_verifier_models, {"friday": ("pkl", vf_)})
            self.assertEqual(len(avisos), 1)

    def test_chave_muda_com_o_verificador_da_extra(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, _, vs = self._cfg(d)
            antes = pm.chave(cfg)
            sem = dict(cfg, palavras_extras=[{k: v for k, v in cfg["palavras_extras"][0].items() if k != "verificador"}])
            self.assertNotEqual(pm.chave(sem), antes)
            os.utime(vs, ns=(os.stat(vs).st_mtime_ns + 10**9,) * 2)
            self.assertNotEqual(pm.chave(cfg), antes)
