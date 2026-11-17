"""Corte da fala pela palavra (v0.3.7, M6): o resto da resposta que o Gemini ainda gera não volta ao satélite.

O recado sai com o microfone fechado; a palavra abre a escuta e a Pi corta a fala. O Live só percebe a interrupção
quando ouve a pessoa, e até lá continua mandando a resposta antiga, que voltava a tocar depois da primeira pausa.
Com o corte armado, o áudio e o texto da geração em curso são descartados até o próprio Gemini marcar o fim dela
(interrompida, gerada ou turno completo). Se nada estava sendo gerado (a resposta já tinha saído toda), não arma:
a próxima resposta é nova. Lógica pura, sem pipecat: testada em test_speech_cut.py."""

_FIM = ("interrupted", "generation_complete", "turn_complete")


class SpeechCut:
    def __init__(self):
        self.gerando = False  # o Gemini está mandando uma geração que ainda não terminou
        self.ativo = False

    def cut(self) -> bool:
        """A palavra cortou a fala. True se havia geração em curso (o corte armou)."""
        self.ativo = self.gerando
        return self.ativo

    def filter(self, msg):
        """Chamado para cada LiveServerMessage antes do pipecat. Tira o conteúdo da geração cortada (model_turn e
        output_transcription) e devolve None se não sobrou nada para o pipecat; senão a própria mensagem."""
        sc = getattr(msg, "server_content", None)
        if sc is None:
            return msg
        fim = any(getattr(sc, c, None) for c in _FIM)
        conteudo = getattr(sc, "model_turn", None) is not None or getattr(sc, "output_transcription", None) is not None
        if conteudo and not fim:
            self.gerando = True
        if self.ativo and conteudo:
            sc.model_turn = None
            sc.output_transcription = None
        if fim:
            self.gerando = False
            self.ativo = False
            return msg
        if conteudo and sc.model_turn is None and sc.output_transcription is None and self._vazia(msg, sc):
            return None
        return msg

    @staticmethod
    def _vazia(msg, sc):
        """Nada além do conteúdo descartado: nenhum outro campo preenchido na mensagem nem no server_content."""
        resto = [v for k, v in vars(sc).items() if k not in ("model_turn", "output_transcription")]
        resto += [v for k, v in vars(msg).items() if k != "server_content"]
        return all(v is None for v in resto)
