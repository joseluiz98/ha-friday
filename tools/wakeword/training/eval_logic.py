"""Lógica pura da prova (spec/plano 2026-10-24, Task 6): detector de hoje sobre notas por quadro, acertos e disparos
falsos. Sem numpy. evaluate.py faz o IO e o modelo. Quadro k cobre o áudio que termina em (k+1)*0.08 s."""
from dataset_rules import FRAME_S, window_max

REARMA_ABAIXO = 0.2


def disparos(notas, limiar):
    """Índices dos quadros em que o detector de hoje dispara: cruza `limiar` armado; rearma com nota < 0,2."""
    armado, saida = True, []
    for k, n in enumerate(notas):
        if armado and n >= limiar:
            armado = False
            saida.append(k)
        elif n < REARMA_ABAIXO:
            armado = True
    return saida


def avaliar_arquivo(notas, pos_do_arquivo, limiar):
    """pos_do_arquivo: TODOS os itens pos do arquivo (qualquer lado) [{"id","ini","fim","split"}].
    detectados = pos da RESERVA com window_max >= limiar; falso = disparo fora de [ini-1.0, fim+1.5] de todo pos."""
    detectados = [p["id"] for p in pos_do_arquivo
                  if p["split"] == "holdout" and window_max(notas, p["ini"], p["fim"]) >= limiar]
    falsos = 0
    for k in disparos(notas, limiar):
        t = (k + 1) * FRAME_S
        if not any(p["ini"] - 1.0 <= t <= p["fim"] + 1.5 for p in pos_do_arquivo):
            falsos += 1
    return {"detectados": detectados, "falsos": falsos}


def comparar(ids_pos, detectados_atual, detectados_cand):
    return {"regressoes": [i for i in ids_pos if i in detectados_atual and i not in detectados_cand],
            "ganhos": [i for i in ids_pos if i not in detectados_atual and i in detectados_cand]}
