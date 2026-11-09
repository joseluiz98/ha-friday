"""Spec 2026-11-03: monta o payload (chaves em português) do evento "ativacao" com todos os detalhes.
O agente do satélite traduz para os atributos em inglês. Ausente = não se aplica; nunca inventado."""
import os


def _r(x, n=2):
    return None if x is None else round(float(x), n)


def payload(palavra, nota, resultado, *, modo, limiar, limiar_voz, voz=None, causa=None, nota_verificador=None,
            limiar_verificador=None, razao_sussurro_db=None, sussurro=None, paciencia=None, gravacao=None):
    p = {"palavra": palavra, "nota": _r(nota), "resultado": resultado, "duracao_s": None, "modo": modo,
         "limiar": _r(limiar), "limiar_voz": _r(limiar_voz)}
    opc = {"voz": _r(voz), "causa": causa, "nota_verificador": _r(nota_verificador),
           "limiar_verificador": _r(limiar_verificador), "razao_sussurro_db": _r(razao_sussurro_db, 1),
           "sussurro": None if sussurro is None else bool(sussurro), "paciencia": paciencia,
           "gravacao": os.path.basename(gravacao) if gravacao else None}
    p.update({k: v for k, v in opc.items() if v is not None})
    return p
