"""As palavras do cadastro de voz (spec 2026-11-23-voice-learning-extra-words): quais o satélite grava, como nomear
as janelas e onde cada verificador mora. Puro: sem openWakeWord e sem rede. A palavra principal é a "friday"; as
outras são as `palavras_extras` da configuração (cada uma com a fala gravada `aprendizagem_intro_<nome>.wav`)."""
import os

PRINCIPAL = "friday"
MODELOS = "/home/jose/modelos"
MODELO_PRINCIPAL = f"{MODELOS}/friday.onnx"
VERIFICADOR_PRINCIPAL = f"{MODELOS}/friday_verificador.pkl"
SEPARADOR = "__"


def nome_falado(nome) -> str:
    """Como a pessoa diz: friday -> Friday, sexta_feira -> Sexta-feira."""
    t = str(nome).replace("_", "-")
    return t[:1].upper() + t[1:]


def verificador_de(nome) -> str:
    return VERIFICADOR_PRINCIPAL if nome == PRINCIPAL else f"{MODELOS}/{nome}_verificador.pkl"


def nome_janela(palavra, k) -> str:
    """tentativa_03 para a principal (como sempre foi); <palavra>__tentativa_03 para as outras."""
    base = f"tentativa_{k:02d}"
    return base if palavra == PRINCIPAL else f"{palavra}{SEPARADOR}{base}"


def palavra_da_janela(nome) -> str:
    return nome.split(SEPARADOR, 1)[0] if SEPARADOR in str(nome) else PRINCIPAL


def arquivo_da_intro(palavra, sons_dir) -> str:
    nome = "aprendizagem_intro.wav" if palavra == PRINCIPAL else f"aprendizagem_intro_{palavra}.wav"
    return os.path.join(sons_dir, nome)


def todas(cfg) -> list:
    """[{"nome", "arquivo"}] da principal e de cada extra, nesta ordem."""
    out = [{"nome": PRINCIPAL, "arquivo": MODELO_PRINCIPAL}]
    for e in cfg.get("palavras_extras") or []:
        if isinstance(e, dict) and e.get("nome") and e.get("arquivo"):
            out.append({"nome": e["nome"], "arquivo": e["arquivo"]})
    return out


def escolher(cfg, sons_dir, ja_cadastradas=()) -> dict:
    """{"gravar": [palavra...], "puladas": [palavra...]}. Só entra palavra com a fala gravada (um bipe não diz qual
    palavra falar). Quem já está cadastrado em algumas palavras grava só as que faltam; se não falta nenhuma,
    grava todas de novo (substitui)."""
    com_fala = [p["nome"] for p in todas(cfg) if os.path.isfile(arquivo_da_intro(p["nome"], sons_dir))]
    puladas = [p["nome"] for p in todas(cfg) if p["nome"] not in com_fala]
    ja = set(ja_cadastradas or ())
    faltam = [p for p in com_fala if p not in ja]
    return {"gravar": faltam if ja and faltam else com_fala, "puladas": puladas}


def mudancas_de_config(cfg, aprovadas) -> dict:
    """`aprovadas`: {palavra: resultado do treino aprovado}. Devolve as mudanças para `validar_mudancas`: o caminho do
    verificador e o limiar da palavra de cada uma (friday: `verificador`/`limiar_palavra`; extras: dentro de
    `palavras_extras`), e o limiar do verificador, que é um só para todas."""
    mud = {}
    extras = [dict(e) for e in (cfg.get("palavras_extras") or [])]
    for palavra, r in aprovadas.items():
        if palavra == PRINCIPAL:
            mud["verificador"] = VERIFICADOR_PRINCIPAL
            mud["limiar_palavra"] = r["limiar_palavra"]
        else:
            for e in extras:
                if e.get("nome") == palavra:
                    e["verificador"] = verificador_de(palavra)
                    e["limiar"] = r["limiar_palavra"]
        mud["limiar_verificador"] = r["limiar_verificador"]
    if any(p != PRINCIPAL for p in aprovadas):
        mud["palavras_extras"] = extras
    return mud


def limiar_do_verificador(cfg):
    """O limiar do verificador em uso, ou None quando nenhuma palavra tem verificador ainda (o primeiro cadastro
    escolhe)."""
    tem = bool(cfg.get("verificador")) or any(e.get("verificador") for e in cfg.get("palavras_extras") or [])
    return cfg.get("limiar_verificador") if tem else None
