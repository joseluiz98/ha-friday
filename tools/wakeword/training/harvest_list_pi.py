"""Roda NA PI (via ssh, stdin): lista os candidatos do modelo, uma linha `src<TAB>caminho<TAB>sha1` (spec 2026-10-24).
Uso: python3 - <friday|sexta_feira> [ativação rotulada falsa ...] < harvest_list_pi.py"""
import glob, hashlib, json, os, re, sys

H = "/home/jose"
A = H + "/gravacoes/ativacoes"
# tentativas_friday_20261006_*: teste A/B do alto-falante; o replay não acha a palavra nos recortes (ver
# HISTORICO_IGNORADO em treino_verificador.py): como positivo guiado seriam falsos positivos.
IGNORAR = ("tentativas_friday_20261006",)


def sha1(p):
    h = hashlib.sha1()
    with open(p, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def candidatos(modelo, falsas=()):
    if modelo == "friday":
        for p in sorted(glob.glob(A + "/*.wav")):
            b = os.path.basename(p)
            if re.match(r"^\d{8}_\d{6}\.wav$", b):
                yield "ativou", p
            else:
                for pref, src in (("quase_", "quase"), ("contexto_", "contexto"), ("barrada_", "barrada"),
                                  ("sem_voz_", "sem_voz")):
                    if b.startswith(pref):
                        yield src, p
        # 31/10: ativações que o dono ouviu e rotulou como falsas (rulings.json no HA, via harvest.sh). Saem como acervo_f,
        # que vence o "ativou" na deduplicação: sem isto, ativou sem a palavra transcrita é descartado (não vira negativo)
        # e, com detector e verificador >= 0,5, vira POSITIVO.
        for nome in sorted(falsas):
            if re.fullmatch(r"\d{8}_\d{6}\.wav", nome) and os.path.exists(A + "/" + nome):
                yield "acervo_f", A + "/" + nome
            else:
                print(f"ignorado (falsa rotulada inexistente ou inválida): {nome}", file=sys.stderr)
        rot = json.load(open(H + "/vozes/rotulos.json"))
        for chave, src in (("verdadeiras", "acervo_v"), ("falsas", "acervo_f")):
            for nome in sorted(rot[chave]):
                p = H + "/gravacoes/acervo_20261015/" + nome
                if os.path.exists(p):
                    yield src, p
        for p in sorted(glob.glob(H + "/gravacoes/tentativas_friday_*.wav")):
            if not any(os.path.basename(p).startswith(i) for i in IGNORAR):
                yield "sessao", p
        yield "sessao", H + "/vozes/" + os.environ["FRIDAY_OWNER_VOICE_ID"] + "/atual/sessao.wav"
        yield "fala_livre", H + "/vozes/" + os.environ["FRIDAY_OWNER_VOICE_ID"] + "/atual/fala_livre.wav"
    elif modelo == "sexta_feira":
        for p in sorted(glob.glob(H + "/gravacoes/tentativas_sexta_feira_*.wav")):
            yield "sessao", p
        # 27/10: disparos do modo sombra (100% falsos em 26–27/10): negativos difíceis de conversa e TV em português.
        # Se a transcrição achar a palavra, o rótulo cai em "voz não confirmada" e o arquivo fica fora (label()).
        for p in sorted(glob.glob(A + "/sombra_sexta_feira_*.wav")):
            yield "sombra", p
        # 06/11: com a Sexta em ATIVA, disparos e quase-disparos nascem com outros nomes. Sem isto os falsos da ativa nunca
        # viram negativos. O rótulo do dono (rulings.json via harvest.sh) vence: `falsas` saem como acervo_f, `v:` como acervo_v.
        for p in sorted(glob.glob(A + "/sexta_feira_*.wav")):
            b = os.path.basename(p)
            if re.fullmatch(r"sexta_feira_\d{8}_\d{6}\.wav", b):
                yield "ativou", p
            elif b.startswith("sexta_feira_quase_") or b.startswith("sexta_feira_paciencia_"):
                # 09/11: rejeição por paciência = quase-disparo com nota >= limiar; a transcrição decide o rótulo
                yield "quase", p
        for nome in sorted(falsas):
            cru, src = (nome[2:], "acervo_v") if nome.startswith("v:") else (nome, "acervo_f")
            if re.fullmatch(r"sexta_feira_\d{8}_\d{6}\.wav", cru) and os.path.exists(A + "/" + cru):
                yield src, A + "/" + cru
            else:
                print(f"ignorado (ativação rotulada inexistente ou inválida): {nome}", file=sys.stderr)
    else:
        raise SystemExit("modelo desconhecido")


for src, p in candidatos(sys.argv[1] if len(sys.argv) > 1 else "", sys.argv[2:]):
    # 26/10: ativacoes/20261025_180940.wav nasceu com 0 bytes e derrubou as notas e a transcrição; WAV sem áudio fica fora
    if os.path.getsize(p) <= 44:
        print(f"ignorado (vazio): {p}", file=sys.stderr)
        continue
    print(f"{src}\t{p}\t{sha1(p)}")
