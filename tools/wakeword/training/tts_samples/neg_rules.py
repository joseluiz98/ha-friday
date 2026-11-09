"""Filtro dos negativos falados pelo XTTS (06/11/2026). Só a stdlib; é embutido no notebook e testado no HA.
Um negativo não pode virar a própria palavra: o XTTS às vezes erra o texto, e "quinta-feira" que saiu "sexta-feira"
ensinaria o contrário. Só passa fala cuja transcrição não tem a palavra e ficou próxima do texto pedido."""
import difflib, re, unicodedata

PALAVRA = re.compile(r"(sexta|sesta|esta|desta|nesta|testa)[- ]?feira")


def normaliza(t):
    t = unicodedata.normalize("NFKD", t.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 -]", " ", t)).strip()


def aceita(pedido, transcricao, minimo=0.75):
    a, b = normaliza(pedido), normaliza(transcricao)
    if not b or PALAVRA.search(b):
        return False
    return difflib.SequenceMatcher(None, a, b).ratio() >= minimo
