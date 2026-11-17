"""Taxa da captura (spec 2026-11-17 xvf3800-support): amostras recebidas por janela sobre o tempo real.

O programa alimenta cada leitura da captura; a cada janela de `janela_s` fecha a taxa (1,0 = tempo real). O agente
lê `captura_taxa`/`captura_ts` no /status e decide (vigia da USB do XVF3800). A primeira leitura só marca o início:
as amostras dela vieram de antes do relógio começar."""
import time


class TaxaCaptura:
    def __init__(self, janela_s=10.0, taxa_hz=16000, relogio=time.time):
        self.janela_s, self.taxa_hz, self._relogio = janela_s, taxa_hz, relogio
        self._inicio = None
        self._amostras = 0
        self._taxa = None
        self.ts = None  # epoch (relogio) em que a última janela fechou

    def alimentar(self, n_amostras: int, agora: float):
        """`agora` é um relógio monotônico qualquer (segundos); só a diferença entre chamadas importa."""
        if self._inicio is None:
            self._inicio = agora
            return
        self._amostras += n_amostras
        decorrido = agora - self._inicio
        if decorrido >= self.janela_s:
            self._taxa = self._amostras / (decorrido * self.taxa_hz)
            self.ts = self._relogio()
            self._inicio, self._amostras = agora, 0

    def atual(self):
        """Taxa da última janela fechada, ou None antes da primeira."""
        return self._taxa
