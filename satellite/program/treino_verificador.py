"""Treino e portão do verificador, na Pi (spec 2026-10-15 D11, D14, D17). IO + openWakeWord + scikit-learn; quem
decide é o verificador.py. Prioridade baixa e uma thread de BLAS: a voz da Friday não pode engasgar.
Uso manual: ~/oww/bin/python treino_verificador.py --conferir-recortes | --avaliar-previo"""
import glob, hashlib, json, os, pickle, sys, tempfile, threading
import numpy as np
import scipy.io.wavfile
from threadpoolctl import threadpool_limits
from openwakeword.model import Model
from openwakeword.vad import VAD
from openwakeword.custom_verifier_model import train_verifier_model
import verificador as vf

RAIZ = "/home/jose/vozes"
MODELO_BASE = "/home/jose/modelos/friday.onnx"
PASSO, SILENCIO_ANTES = 1280, 6   # 6 pedaços de silêncio antes de cada clipe: os 5 primeiros do modelo saem zerados
NEG_PASSO, NEG_MAX = 2, 6000      # negativos: um quadro a cada 2, no máximo 6000 (RAM da Pi 3)


def _baixa_prioridade():
    try:  # só esta thread (no Linux o setpriority com o TID vale por thread)
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 19)
    except OSError:
        pass


class Extrator:
    """base (nota do modelo), vad e feats (16x96, as entradas do verificador) por pedaço de 80 ms, com cache."""

    def __init__(self):
        self.m = Model(wakeword_models=[MODELO_BASE], inference_framework="onnx")

    def __call__(self, caminho):
        chave = hashlib.sha1(f"{caminho}:{os.path.getmtime(caminho)}".encode()).hexdigest()
        cache = os.path.join(RAIZ, "_cache", chave + ".npz")
        if os.path.exists(cache):
            z = np.load(cache)
            return {"base": z["base"].tolist(), "vad": z["vad"].tolist(), "feats": z["feats"]}
        taxa, dat = scipy.io.wavfile.read(caminho)
        if taxa != 16000 or dat.ndim != 1:
            raise ValueError(f"{caminho}: precisa ser 16 kHz mono")
        dat = np.concatenate([np.zeros(PASSO * SILENCIO_ANTES, dtype=np.int16), dat.astype(np.int16)])
        self.m.reset()
        vad = VAD()
        base, vz, feats = [], [], []
        for i in range(0, len(dat) - PASSO + 1, PASSO):
            x = dat[i:i + PASSO]
            base.append(float(self.m.predict(x)["friday"]))
            vz.append(float(vad.predict(x, frame_size=640)))
            feats.append(self.m.preprocessor.get_features(self.m.model_inputs["friday"])[0])
        base, vz = base[SILENCIO_ANTES:], vz[SILENCIO_ANTES:]
        feats = np.asarray(feats[SILENCIO_ANTES:], dtype=np.float16)
        os.makedirs(os.path.dirname(cache), mode=0o700, exist_ok=True)
        np.savez(cache, base=np.asarray(base), vad=np.asarray(vz), feats=feats)
        return {"base": base, "vad": vz, "feats": feats}


def _sessao(rotulos, chave, arquivo):
    return rotulos.get(chave, {}).get(arquivo, arquivo)


def _dia(arquivo, rotulos):
    """Grupo das falsas = o dia (revisão de 15/10): as falsas vêm em rajadas da mesma fonte (12 em 14/10, 10 em 13/10);
    separar só por conversa deixava a rajada inteira cair nos dois lados da validação."""
    return arquivo[:8] if arquivo[:8].isdigit() else _sessao(rotulos, "falsas", arquivo)


# As duas gravações de 06/10 foram o teste A/B do alto-falante (modo CONTINUO): o replay não acha a palavra nos
# recortes (picos em posições sem padrão), então não valem como referência nem como positivos.
HISTORICO_IGNORADO = ("tentativas_friday_20261006",)


def _fatia(e, ini, fim):
    return {"base": e["base"][ini:fim], "vad": e["vad"][ini:fim], "feats": e["feats"][ini:fim]}


def clipes(ident_novo, pasta_nova, extrair, rotulos):
    """Os clipes do D14, cada um com base/vad/feats, categoria, grupo (sessão), rótulo e quadros de treino.
    16/10: tentativas e fala livre saem do áudio CONTÍNUO (sessao.wav + marcas.json; historico/continuas/*.wav com os
    recortes de recortes_tentativas): o modelo roda no contínuo e só depois se fatia. Recortes com começo a frio
    perdiam nota (08/10: 25 de 40 recortados × 33 de 40 contínuos, que é o número visto ao vivo)."""
    out = []

    def add(caminho, cat, grupo, rotulo, fala_livre=False, e=None):
        e = e if e is not None else extrair(caminho)
        if rotulo:
            q = vf.quadros_positivos(e["base"])
        else:
            q = (vf.quadros_fala_livre(e["base"]) if fala_livre else list(range(len(e["base"]))))[::NEG_PASSO]
        out.append(dict(e, cat=cat, grupo=grupo, rotulo=rotulo, quadros=q))

    for c in sorted(glob.glob(f"{RAIZ}/_negativos/*.wav")):
        add(c, "falsas", "f:" + _dia(os.path.basename(c), rotulos), 0)
    pessoas = sorted(d for d in os.listdir(RAIZ) if not d.startswith("_") and os.path.isdir(f"{RAIZ}/{d}"))
    for p in pessoas:
        pasta = pasta_nova if (p == ident_novo and pasta_nova) else f"{RAIZ}/{p}/atual"
        cat = "nova" if p == ident_novo else f"pessoa:{p}"
        if os.path.isfile(f"{pasta}/sessao.wav") and os.path.isfile(f"{pasta}/marcas.json"):
            e = extrair(f"{pasta}/sessao.wav")
            for mk in json.load(open(f"{pasta}/marcas.json")):
                pedaco = _fatia(e, mk["ini"], mk["fim"])
                if mk["nome"] == "fala_livre":
                    add(None, "fala_livre", f"{p}:fala_livre", 0, fala_livre=True, e=pedaco)
                else:
                    add(None, cat, f"{p}:{mk['nome']}", 1, e=pedaco)
        elif os.path.isdir(pasta):  # cadastros anteriores a 16/10: só as janelas recortadas
            for c in sorted(glob.glob(f"{pasta}/tentativa_*.wav")):
                add(c, cat, f"{p}:{os.path.basename(c)}", 1)
            if os.path.exists(f"{pasta}/fala_livre.wav"):
                add(f"{pasta}/fala_livre.wav", "fala_livre", f"{p}:fala_livre", 0, fala_livre=True)
        if p == "jose":
            continuas = sorted(c for c in glob.glob(f"{RAIZ}/jose/historico/continuas/*.wav")
                               if not os.path.basename(c).startswith(HISTORICO_IGNORADO))
            for c in continuas:
                e = extrair(c)
                for ini, fim in vf.recortes_tentativas(len(e["base"]) * 0.08):
                    add(None, "historico", "h:" + os.path.basename(c)[:-4], 1,
                        e=_fatia(e, int(ini / 0.08), int(fim / 0.08)))
            for c in ([] if continuas else sorted(glob.glob(f"{RAIZ}/jose/historico/tentativas/*.wav"))):
                if os.path.basename(c).startswith(HISTORICO_IGNORADO):
                    continue
                add(c, "historico", "h:" + os.path.basename(c).rsplit("_", 1)[0], 1)
            for c in sorted(glob.glob(f"{RAIZ}/jose/historico/ativacoes/*.wav")):
                add(c, "verdadeiras", "v:" + _sessao(rotulos, "verdadeiras", os.path.basename(c)), 1)
    return out


def treinar(lista):
    pos = [c["feats"][i] for c in lista if c["rotulo"] for i in c["quadros"]]
    neg = [c["feats"][i] for c in lista if not c["rotulo"] for i in c["quadros"]]
    if not pos or not neg:
        raise ValueError("treino sem as duas classes")
    if len(neg) > NEG_MAX:
        neg = [neg[int(j)] for j in np.linspace(0, len(neg) - 1, NEG_MAX)]
    X = np.asarray(pos + neg, dtype=np.float32)
    y = np.asarray([1] * len(pos) + [0] * len(neg))
    with threadpool_limits(1):
        return train_verifier_model(X, y)


def notas_de(modelo, c):
    with threadpool_limits(1):
        return modelo.predict_proba(np.asarray(c["feats"], dtype=np.float32))[:, -1].tolist()


def _numeros(cats):
    n, f = cats.get("nova"), cats.get("falsas")
    return {"nova": f"{n[0]}/{n[1]}" if n else None, "aceitas": n[0] if n else None,
            "falsas_barradas": f"{f[1] - f[0]}/{f[1]}" if f else None}


def executar(ident, nome, pasta_nova, limiar_voz, antes, saida_dir="/home/jose/modelos"):
    _baixa_prioridade()
    rotulos = json.load(open(f"{RAIZ}/rotulos.json"))
    cl = clipes(ident, pasta_nova, Extrator(), rotulos)
    novas = [c["base"] for c in cl if c["cat"] == "nova"]
    mal = vf.ouviu_mal(novas)
    if mal > vf.OUVIU_MAL_MAX:
        return {"aprovado": False, "numeros": {},
                "motivo": f"o modelo base mal ouviu o Friday de {nome} (nota abaixo de 0,3 em {mal} de {len(novas)})"}
    tabela = vf.avaliar(cl, treinar, notas_de, limiar_voz)
    aprovado, (lv, lp), textos, falhou = vf.escolher(tabela, antes)
    r = {"aprovado": aprovado, "motivo": falhou, "numeros": dict(_numeros(tabela[(lv, lp)]), textos=textos),
         "limiar_verificador": lv, "limiar_palavra": lp}
    if aprovado:
        modelo = treinar(cl)
        fd, tmp = tempfile.mkstemp(dir=saida_dir, prefix=".verificador-", suffix=".pkl")
        with os.fdopen(fd, "wb") as f:
            pickle.dump(modelo, f)
        r["modelo_tmp"] = tmp
    return r


def _conferir_recortes():
    hist = [c for c in clipes("jose", None, Extrator(), json.load(open(f"{RAIZ}/rotulos.json")))
            if c["cat"] == "historico"]
    ok = sum(1 for c in hist if max(c["base"]) >= 0.5)
    print(json.dumps({"tentativas": len(hist), "base_acima_de_0_5": ok}))


def _avaliar_previo():
    rotulos = json.load(open(f"{RAIZ}/rotulos.json"))
    cl = clipes("jose", None, Extrator(), rotulos)
    tabela = vf.avaliar(cl, treinar, notas_de, 0.6)
    for combo, cats in sorted((k, v) for k, v in tabela.items() if k[0] != "base"):
        print(json.dumps({"combo": combo, "cats": cats, "base": tabela[("base", vf.REFERENCIA_PALAVRA)],
                          "criterios": vf.criterios(cats, {}, tabela[("base", vf.REFERENCIA_PALAVRA)])}, ensure_ascii=False), flush=True)


def _executar_cli(args_json):
    a = json.loads(args_json)
    print(json.dumps(executar(a["ident"], a["nome"], a["pasta"], a["limiar_voz"], a["antes"]), ensure_ascii=False),
          flush=True)


if __name__ == "__main__":
    _baixa_prioridade()
    if sys.argv[1] == "--executar":  # chamado pelo friday_satelite._treinar, num processo à parte
        _executar_cli(sys.argv[2])
    else:
        {"--conferir-recortes": _conferir_recortes, "--avaliar-previo": _avaliar_previo}[sys.argv[1]]()
