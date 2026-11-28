"""Volume do satélite (spec 2026-10-12 §1 e §2; escala fina, spec 2026-11-27). Lógica pura: quem escreve na placa e no
softvol `Fine` é o `mixer_fino.MixerFino` (injetado), e quem grava a configuração é uma função injetada. Testes:
test_volume.py.

O volume é um percentual 0–100. As posições 0–11 são a escala falada (1–10) e os LEDs do anel do Jabra SPEAK 410
(controle PCM com 12 posições; cada uma acende um LED do anel, teste de 12/10). Botão físico e Pi mexem no MESMO ajuste,
mas o Linux não fica sabendo do aperto: a Pi conta as teclas de volume e regrava o nível real (o driver USB não envia um
valor igual ao último que ele mesmo gravou)."""
import escala

MAX_POSICAO = 11
ESPERA_GRAVACAO_S = 3.0  # grava quando os apertos param por este tempo (poupa o cartão numa rajada)


def _inteiro(x):
    return isinstance(x, int) and not isinstance(x, bool)


def posicao_de_numero(n) -> int:
    """Número falado (1–10) = número de LEDs; o 10 é o máximo (posição 11)."""
    if not _inteiro(n) or not 1 <= n <= 10:
        raise ValueError("volume de 1 a 10")
    return MAX_POSICAO if n == 10 else n


def posicao_de_percentual(p) -> int:
    if isinstance(p, bool) or not isinstance(p, (int, float)) or not 0 <= p <= 100:
        raise ValueError("percentual de 0 a 100")
    return min(MAX_POSICAO, max(1, round(p * MAX_POSICAO / 100)))


def descrever(pct: int) -> dict:
    """O volume em termos que a Friday entende: a posição (LEDs), o número da escala falada (None na posição 10, que
    não tem número próprio) e o percentual exato."""
    k = escala.posicao_de_pct(pct)
    numero = k if k <= 9 else (10 if k == MAX_POSICAO else None)
    return {"posicao": k, "volume": numero, "percentual": pct}


class ControleVolume:
    def __init__(self, mixer, gravar, pct_inicial, alertas_numero=6):
        self._mixer = mixer  # mixer_fino.MixerFino
        self._gravar = gravar  # fn(pct): grava o volume no config.json
        self.normal = pct_inicial  # volume da casa em % (None = ainda não se sabe)
        self.atual = pct_inicial  # o que está valendo no aparelho agora
        self.alertas_numero = alertas_numero  # escala falada 1–10 (config `volume_alertas`)
        self.em_alerta = False
        self._pendente = False
        self._ultima_mudanca = None  # instante da última mudança; None = gravar no próximo tique
        self._rampa = None  # (início, duração_s, posição alvo): alarme crescente (spec 2026-10-21 §11, alarm_ramp_s)

    @property
    def posicao(self):
        return None if self.atual is None else escala.posicao_de_pct(self.atual)

    def iniciar(self):
        """Na partida: aplica o volume gravado."""
        if self.atual is not None:
            self._mixer.forcar(self.atual)

    def _mudou_normal(self, pct, agora=None):
        self.normal = pct
        self._pendente, self._ultima_mudanca = True, agora

    def definir(self, pct, agora=None) -> int:
        """Voz ou PUT /config. Vale também durante um alerta: passa a ser o volume normal. Com `agora` (arraste no
        Music Assistant) a gravação espera as mudanças pararem."""
        if not _inteiro(pct) or not 0 <= pct <= 100:
            raise ValueError("percentual de 0 a 100")
        self.atual = pct
        self._rampa = None
        self._mixer.escrever(pct)
        self._mudou_normal(pct, agora)
        return pct

    def passo(self, delta: int) -> int:
        """"Mais alto"/"mais baixo" pela voz: uma posição, mantendo a distância até o degrau; entre as posições 1 e 11.
        "Mais baixo" nunca sobe e "mais alto" nunca desce."""
        base = self.atual if self.atual is not None else escala.grade(1)
        placa = self._mixer.placa
        k = escala.posicao_de_pct(base)
        novo = min(MAX_POSICAO, max(1, k + delta))
        folga = escala.alvo_db(base, placa) - escala.ANCORAS[placa][k]
        pct = escala.pct_de_db(escala.ANCORAS[placa][novo] + folga, placa)
        return self.definir(max(pct, base) if delta > 0 else min(pct, base))

    def apertou(self, delta: int, agora: float):
        """Botão +/− do Jabra (só o Jabra tem): o aparelho já mudou um degrau; regrava para sincronizar o driver."""
        if self.atual is None:
            return
        # o aparelho parte do nível que está na placa, não do pedido: numa subida pendente os dois diferem
        base = self._mixer.na_placa if self._mixer.na_placa is not None else self._mixer.nivel
        self._mixer.sincronizar(min(len(escala.NIVEIS[self._mixer.placa]) - 1, max(0, base + delta)))
        # recalcula sempre: mesmo no fim da escala o aperto pode ter cancelado uma subida pendente
        self.atual = escala.pct_de_db(self._mixer.db(), self._mixer.placa)
        if not self.em_alerta:
            self._mudou_normal(self.atual, agora)

    def _tique_rampa(self, agora: float):
        inicio, duracao, alvo = self._rampa
        frac = min(1.0, max(0.0, (agora - inicio) / duracao))
        pct = escala.grade(1 + int((alvo - 1) * frac))
        if pct != self.atual:
            self.atual = pct
            self._mixer.escrever(pct)
        if frac >= 1.0:
            self._rampa = None

    def tique(self, agora: float):
        if self._rampa is not None and self.em_alerta:
            self._tique_rampa(agora)
        if not self._pendente or self.normal is None:
            return
        if self._ultima_mudanca is not None and agora - self._ultima_mudanca < ESPERA_GRAVACAO_S:
            return
        self._gravar(self.normal)
        self._pendente = False

    def entrar_alerta(self, rampa_s: float = 0, agora=None):
        """`rampa_s` > 0 (e `agora`): alarme crescente — começa na posição 1 e sobe até o volume de alertas em
        `rampa_s` segundos (os degraus saem do tique). 0 = o comportamento de sempre."""
        if self.em_alerta:
            return
        self.em_alerta = True
        alvo = posicao_de_numero(self.alertas_numero)
        if rampa_s and rampa_s > 0 and agora is not None and alvo > 1:
            self._rampa = (agora, float(rampa_s), alvo)
            self.atual = escala.grade(1)
        else:
            self.atual = escala.grade(alvo)
        self._mixer.escrever(self.atual)

    def sair_alerta(self):
        if not self.em_alerta:
            return
        self.em_alerta = False
        self._rampa = None
        if self.normal is not None:
            self.atual = self.normal
            self._mixer.escrever(self.normal)
