"""Modo em segundo plano da voz (01/10): lógica pura, sem Pipecat, testada em test_segundo_plano.py.

O Gemini Live da família 3.x não tem chamada de função não bloqueante (só a 2.5 tem). Então o bot
espera o cérebro por uma janela curta; se ele não responder nela, a voz avisa que vai contar depois e
a conversa segue. Quando a resposta chega, ela é entregue no primeiro silêncio, ou por push ao dono
se a conversa já terminou.
"""

import asyncio
import re
import time

import local_config


async def esperar_com_janela(tarefa, janela):
    """Espera a tarefa por até `janela` segundos, sem cancelá-la. Devolve (pronta, resultado)."""
    try:
        return True, await asyncio.wait_for(asyncio.shield(tarefa), janela)
    except asyncio.TimeoutError:
        return False, None


class EstadoDaConversa:
    """Quem está falando numa conexão de voz e quantos pedidos ao cérebro estão pendentes.

    O Gemini Live não emite "usuário começou/parou de falar"; os sinais disponíveis são o início e o
    fim da fala da voz e a transcrição da fala do dono.
    """

    def __init__(self, relogio=time.monotonic):
        self._relogio = relogio
        self.aberta = True
        self.voz_falando = False
        self.pendentes = 0
        self.ultima_atividade = relogio()

    def _marca(self):
        self.ultima_atividade = self._relogio()

    def voz_comecou(self):
        self.voz_falando = True
        self._marca()

    def voz_parou(self):
        self.voz_falando = False
        self._marca()

    def jose_falou(self):
        self._marca()

    def silencio(self, minimo):
        return not self.voz_falando and self._relogio() - self.ultima_atividade >= minimo

    def manter_aberta(self):
        """No tempo limite de inatividade: não encerrar enquanto houver resposta a caminho."""
        return self.pendentes > 0


async def esperar_silencio(estado, minimo=2.0, passo=0.3, dormir=asyncio.sleep):
    """True no primeiro silêncio com a conversa aberta; False se ela fechar antes."""
    while estado.aberta:
        if estado.silencio(minimo):
            return True
        await dormir(passo)
    return False


def texto_para_gemini(pedido, resposta):
    return (
        f'[Resposta do cérebro ao pedido "{pedido}"]\n{resposta}\n\n'
        "Conte isso ao " + local_config.owner() + " agora, resumido e com as suas palavras."
    )


def texto_push(pedido, resposta, limite=700):
    sufixo = "… (resposta completa no app da Sexta-feira)"
    limpo = resposta.replace("**", "").replace("`", "")
    limpo = re.sub(r"(?m)^\s*#+\s*", "", limpo)
    limpo = re.sub(r"\s+", " ", limpo).strip()
    texto = f"Sobre “{pedido[:80]}”: {limpo}"
    if len(texto) > limite:
        texto = texto[: limite - len(sufixo)].rstrip() + sufixo
    return texto
