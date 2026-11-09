"""Registro permanente dos eventos da palavra (spec 2026-10-15 D9): uma linha JSON por evento, sem áudio.
O journal só guarda 50 MB; este arquivo é a linha de base antes e depois do verificador."""
import datetime, json

ARQUIVO = "/home/jose/gravacoes/eventos_palavra.ndjson"
# 20/10: barrada = o verificador rejeitou; 09/11: paciencia = nota e voz passaram num quadro, faltou o seguinte
TIPOS = ("ativou", "sem_voz", "quase", "barrada_verificador", "sussurro", "sombra", "paciencia")


def linha(tipo, nota, voz, arquivo=None, agora=None, whisper_ratio_db=None, palavra=None) -> str:
    if tipo not in TIPOS:
        raise ValueError(f"tipo é um de {TIPOS}")
    agora = agora or datetime.datetime.now()
    d = {"ts": agora.strftime("%Y-%m-%dT%H:%M:%S"), "tipo": tipo, "nota": round(float(nota), 2),
         "voz": round(float(voz), 2)}
    if arquivo:
        d["arquivo"] = arquivo
    if whisper_ratio_db is not None:
        d["whisper_ratio_db"] = round(float(whisper_ratio_db), 1)
    if palavra is not None:  # spec 2026-10-24: só as palavras extras; a Friday segue sem o campo
        d["palavra"] = palavra
    return json.dumps(d, ensure_ascii=False)


def prefixo_quase_extra(nome: str) -> str:
    """26/10: prefixo do áudio do "quase" de uma palavra extra. Começa pelo nome da palavra, nunca por "quase_":
    o harvest da Friday trata todo "quase_*" como dela."""
    return f"{nome}_quase_"


def prefixo_paciencia_extra(nome: str) -> str:
    """09/11: prefixo do áudio de uma rejeição por paciência de uma palavra extra (mesma regra do "quase")."""
    return f"{nome}_paciencia_"


ARQUIVO_FALAS = "/home/jose/gravacoes/falas_palavra.ndjson"  # 20/10: pico do modelo base por trecho de fala


def linha_fala(trecho, agora=None) -> str:
    agora = agora or datetime.datetime.now()
    return json.dumps({"ts": agora.strftime("%Y-%m-%dT%H:%M:%S"), **trecho}, ensure_ascii=False)


def quase_vale(q, limiar, limiar_voz) -> bool:
    """D8: só conta como quase-ativação abaixo do limiar e com voz humana (acima do limiar sem voz = sem_voz)."""
    return q is not None and q["nota"] < limiar and q["voz"] >= limiar_voz


class Registro:
    def __init__(self, caminho=ARQUIVO, avisar=None):
        self.caminho, self._falhou = caminho, False
        self._avisar = avisar or (lambda t: print(t, flush=True))

    def escrever(self, texto):
        try:
            with open(self.caminho, "a") as f:
                f.write(texto + "\n")
        except OSError as e:
            if not self._falhou:  # falha de disco não derruba a captura; avisa uma vez
                self._falhou = True
                self._avisar(json.dumps({"registro_palavra_falhou": f"{type(e).__name__}: {e}"}))
