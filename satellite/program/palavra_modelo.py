"""Cria o detector da palavra com ou sem o verificador pessoal (spec 2026-10-15 D12, D13). `fabrica` é o Model do
openWakeWord (injetado: os testes rodam sem ele). Verificador ausente ou quebrado: detector de hoje + um aviso."""
import json, os, pickle

MODELO_BASE = "/home/jose/modelos/friday.onnx"


def _extras(cfg):
    return cfg.get("palavras_extras") or []


def _mtime(caminho):
    try:
        return os.stat(caminho).st_mtime_ns
    except OSError:
        return None


def chave(cfg):
    """Muda quando o .pkl é trocado no mesmo caminho (revisão final I2: o 2º cadastro aprovado) ou quando uma
    palavra extra entra, sai ou tem o .onnx trocado (spec 2026-10-24). NÃO inclui `modo` nem `limiar`: trocar o
    modo vale no quadro seguinte, sem recarregar o modelo (sem engasgo de áudio)."""
    v = cfg.get("verificador")
    versao = _mtime(v) if v else None
    extras = tuple(sorted((e["nome"], e["arquivo"], _mtime(e["arquivo"]), e.get("verificador"),
                           _mtime(e["verificador"]) if e.get("verificador") else None) for e in _extras(cfg)))
    return (v, cfg.get("limiar_verificador"), versao, extras)


def carregadas(modelo):
    """Os nomes que o modelo realmente carregou (chave = nome do arquivo sem .onnx)."""
    return set(modelo.models.keys())


def _modelos(cfg, avisar):
    """Friday + extras com arquivo. As `desligada` também carregam: ligar na hora não recarrega nada."""
    caminhos = [MODELO_BASE]
    for e in _extras(cfg):
        if os.path.exists(e["arquivo"]):
            caminhos.append(e["arquivo"])
        else:
            avisar(json.dumps({"palavra_extra_ignorada": e["nome"], "motivo": "arquivo ausente"}))
    return caminhos


def _fabricar(fabrica, cfg, avisar, **kw):
    caminhos = _modelos(cfg, avisar)
    try:
        return fabrica(wakeword_models=caminhos, inference_framework="onnx", **kw)
    except Exception as e:
        if len(caminhos) == 1:
            raise
        avisar(json.dumps({"palavra_extra_ignorada": "todas", "motivo": f"{type(e).__name__}: {e}"}))
        return fabrica(wakeword_models=[MODELO_BASE], inference_framework="onnx", **kw)


def _verificadores_extras(cfg, avisar):
    """02/11: {nome: .pkl} das extras com verificador próprio que existe (o ausente é avisado e fica de fora)."""
    out = {}
    for e in _extras(cfg):
        v = e.get("verificador")
        if not v:
            continue
        if os.path.exists(v) and os.path.exists(e["arquivo"]):
            out[e["nome"]] = v
        else:
            avisar(json.dumps({"verificador_falhou": f"{e['nome']}: arquivo ausente"}))
    return out


def _carregar_pkl(caminho):
    with open(caminho, "rb") as f:
        return pickle.load(f)


def criar(fabrica, cfg, avisar, carregar=_carregar_pkl):
    """Devolve (modelo, a Friday tem verificador). O openWakeWord 0.6 recusa mais de um verificador no construtor (a
    conferência das chaves fica dentro do laço que carrega os modelos), então o modelo nasce só com o da Friday e o
    de cada extra entra depois em `custom_verifier_models`, que é o que o predict consulta. Um .pkl de extra quebrado
    é avisado e não tira o da Friday. O limiar do verificador é um só (custom_verifier_threshold) para todas."""
    v, lv = cfg.get("verificador"), cfg.get("limiar_verificador")
    extras = _verificadores_extras(cfg, avisar)
    kw = {"custom_verifier_threshold": lv} if (v or extras) else {}
    modelo, com = None, False
    if v:
        try:
            if not os.path.exists(v):
                raise FileNotFoundError(v)
            modelo, com = _fabricar(fabrica, cfg, avisar, custom_verifier_models={"friday": v}, **kw), True
        except Exception as e:
            avisar(json.dumps({"verificador_falhou": f"{type(e).__name__}: {e}"}))
    if modelo is None:
        modelo = _fabricar(fabrica, cfg, avisar, **kw)
    carregadas_ = carregadas(modelo) if extras else set()
    for nome, caminho in extras.items():
        if nome not in carregadas_:
            continue
        try:
            modelo.custom_verifier_models[nome] = carregar(caminho)
        except Exception as e:
            avisar(json.dumps({"verificador_falhou": f"{nome}: {type(e).__name__}: {e}"}))
    return modelo, com


class _Espiao:
    """Envolve o verificador: anota cada consulta e a nota dada, e repassa todo o resto ao objeto original."""
    def __init__(self, interno):
        self._interno, self._consultado, self._nota = interno, False, 0.0

    def predict_proba(self, x):
        p = self._interno.predict_proba(x)
        self._consultado, self._nota = True, max(self._nota, float(p[0][-1]))
        return p

    def ler(self):
        r = (self._consultado, self._nota)
        self._consultado, self._nota = False, 0.0
        return r

    def __getattr__(self, nome):
        return getattr(self._interno, nome)


def espiar(modelo):
    """20/10: troca o verificador do modelo por um espião e o devolve (None se o modelo não tem verificador)."""
    v = getattr(modelo, "custom_verifier_models", {}).get("friday")
    if v is None:
        return None
    esp = _Espiao(v)
    modelo.custom_verifier_models["friday"] = esp
    return esp
