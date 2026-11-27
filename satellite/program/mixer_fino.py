"""Placa + softvol `Fine` como um só volume (spec 2026-11-27). Lógica pura: quem escreve na placa (`hw`), no Fine
(`fino`) e quem agenda (`agendar(segundos, fn) -> objeto com cancel()`) são injetados.

A placa muda na hora; o Fine só se ouve depois do buffer (~0,4 s). Para nunca soar mais alto que o volume antigo e o
novo: ao SUBIR de nível, grava primeiro o Fine (mais baixo) e a placa depois do atraso; ao DESCER, a placa primeiro.
Só o valor mais novo vale: nada entra em fila."""
import json

import escala


class MixerFino:
    def __init__(self, hw, fino, placa, agendar, atraso_s=0.4, log=print):
        self._hw, self._fino, self.placa = hw, fino, placa
        self._agendar, self.atraso_s, self._log = agendar, atraso_s, log
        self.nivel = None  # último nível pedido
        self.trim = 0.0
        self._na_placa = None  # nível já gravado na placa
        self._pendente = None

    def _gravar_fino(self, db):
        try:
            self._fino.write(db)
        except Exception as e:  # sem o Fine o volume fica no degrau da placa; nunca derruba o programa
            self._log(json.dumps({"fine_error": f"{type(e).__name__}: {e}"}))

    def _gravar_placa(self, nivel):
        self._hw.escrever(nivel)
        self._na_placa = nivel

    def _cancelar(self):
        if self._pendente is not None:
            self._pendente.cancel()
            self._pendente = None

    def _disparar(self):
        self._pendente = None
        self._gravar_placa(self.nivel)

    def escrever(self, pct, forcar_placa=False):
        nivel, trim = escala.nivel_de_pct(pct, self.placa), escala.trim_db(pct, self.placa)
        trim_antes = self.trim
        self.nivel, self.trim = nivel, trim
        if self._na_placa is not None and nivel > self._na_placa:
            self._gravar_fino(trim)
            if self.atraso_s <= 0:
                self._cancelar()
                self._gravar_placa(nivel)
            elif self._pendente is None or trim < trim_antes:  # o atraso conta do último Fine que BAIXOU
                self._cancelar()
                self._pendente = self._agendar(self.atraso_s, self._disparar)
            return
        self._cancelar()
        if nivel != self._na_placa or forcar_placa:
            self._gravar_placa(nivel)
        self._gravar_fino(trim)

    def reaplicar(self, pct):
        """Grava Fine e placa mesmo sem mudança (a contagem do Jabra escreve direto na placa)."""
        self.escrever(pct, forcar_placa=True)

    def forcar(self, pct):
        """Partida: o driver USB não envia um valor igual ao último que gravou; escreve um vizinho antes."""
        self._cancelar()
        nivel = escala.nivel_de_pct(pct, self.placa)
        self.nivel, self.trim = nivel, escala.trim_db(pct, self.placa)
        self._gravar_fino(self.trim)
        self._gravar_placa(nivel - 1 if nivel > 0 else 1)
        self._gravar_placa(nivel)

    def sincronizar(self, nivel):
        """Botão físico: o aparelho já mudou de nível; regrava o mesmo nível. O trim fica."""
        self._cancelar()
        self.nivel = nivel
        self._gravar_placa(nivel)

    def db(self) -> float:
        return escala.NIVEIS[self.placa][self.nivel] + self.trim

    def ler(self) -> int:
        return self._hw.ler()
