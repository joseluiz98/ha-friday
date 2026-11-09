"""Retenção do áudio de entrada até o Gemini Live ficar pronto (06/10/2026).

O GeminiLiveLLMService do Pipecat 1.12 DESCARTA o áudio que chega antes de `_ready_for_realtime_input`
(~1 s depois da conexão). A pré-gravação do satélite ("Friday, apaga a luz" emendado) chega justamente
nessa janela e se perdia inteira (medido). Aqui o áudio fica guardado e sai na ordem assim que fica pronto.
Só vale para o começo da conexão: depois da primeira vez pronto, passa direto. Lógica pura (sem Pipecat).
"""
from collections import deque


class RetencaoDeAudio:
    def __init__(self, max_quadros: int = 500):  # 500 quadros de 20 ms = 10 s
        self._fila = deque(maxlen=max_quadros)
        self._ja_pronto = False

    def entrar(self, quadro, pronto: bool) -> list:
        if self._ja_pronto:
            return [quadro]
        if pronto:
            self._ja_pronto = True
            saida = list(self._fila) + [quadro]
            self._fila.clear()
            return saida
        self._fila.append(quadro)
        return []

    def liberar_se_pronto(self, pronto: bool) -> list:
        if self._ja_pronto or not pronto:
            return []
        self._ja_pronto = True
        saida = list(self._fila)
        self._fila.clear()
        return saida
