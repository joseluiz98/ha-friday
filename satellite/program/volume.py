"""Volume do satélite (spec 2026-10-12 §1 e §2). Lógica pura: quem escreve no Jabra e quem grava a configuração
são funções injetadas. Testes: test_volume.py.

O Jabra SPEAK 410 tem o controle PCM com 12 posições (0–11); cada posição acende um LED do anel (teste de
12/10). Botão físico e Pi mexem no MESMO ajuste, mas o Linux não fica sabendo do aperto: a Pi conta as teclas
de volume e regrava o nível real (o driver USB não envia um valor igual ao último que ele mesmo gravou)."""

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


def descrever(k: int) -> dict:
    """Posição em termos que a Friday entende: o número da escala falada (None na posição 10, que não tem
    número próprio) e o percentual."""
    numero = k if k <= 9 else (10 if k == MAX_POSICAO else None)
    return {"posicao": k, "volume": numero, "percentual": round(k * 100 / MAX_POSICAO)}


class ControleVolume:
    def __init__(self, escrever, gravar, posicao_inicial, alertas_numero=6, avisar=None):
        self._escrever = escrever  # fn(posicao): grava no mixer do Jabra
        self._gravar = gravar  # fn(posicao): grava `volume` no config.json
        # spec 2026-10-21 §6.2 (ponte de volume): fn(posicao) a cada mudança do volume NORMAL, para o espelho no HA
        # (o Music Assistant usa o espelho como controle de volume). Falha do aviso nunca derruba o volume.
        self._avisar = avisar
        self.normal = posicao_inicial  # volume da casa (None = ainda não se sabe)
        self.atual = posicao_inicial  # o que está valendo no aparelho agora
        self.alertas_numero = alertas_numero  # escala falada 1–10 (config `volume_alertas`)
        self.em_alerta = False
        self._pendente = False
        self._ultima_mudanca = None  # instante do último aperto; None = gravar no próximo tique
        self._rampa = None  # (início, duração_s, alvo): alarme crescente (spec 2026-10-21 §11, alarm_ramp_s)

    def iniciar(self):
        """Na partida: aplica o volume gravado. Escreve um vizinho antes, para o driver enviar mesmo que ele
        ache que o valor já é esse (botões apertados com o programa parado)."""
        if self.atual is None:
            return
        vizinho = self.atual - 1 if self.atual > 0 else self.atual + 1
        self._escrever(vizinho)
        self._escrever(self.atual)
        self._aviso(self.atual)

    def _aviso(self, k):
        if self._avisar is None:
            return
        try:
            self._avisar(k)
        except Exception as e:
            print(f'{{"volume_aviso_falhou": "{type(e).__name__}"}}', flush=True)

    def _mudou_normal(self, k, agora=None):
        self.normal = k
        self._aviso(k)
        self._pendente, self._ultima_mudanca = True, agora

    def definir(self, k) -> int:
        """Voz ou PUT /config. Vale também durante um alerta: passa a ser o volume normal."""
        if not _inteiro(k) or not 0 <= k <= MAX_POSICAO:
            raise ValueError("posição de 0 a 11")
        self.atual = k
        self._rampa = None
        self._escrever(k)
        self._mudou_normal(k)
        return k

    def passo(self, delta: int) -> int:
        """"Mais alto"/"mais baixo" pela voz: uma posição a partir do nível real, entre 1 e 11."""
        base = self.atual if self.atual is not None else 1
        return self.definir(min(MAX_POSICAO, max(1, base + delta)))

    def apertou(self, delta: int, agora: float):
        """Botão +/− do Jabra: o aparelho já mudou; regrava o mesmo nível só para sincronizar o driver."""
        if self.atual is None:
            return
        self.atual = min(MAX_POSICAO, max(0, self.atual + delta))
        self._escrever(self.atual)
        if not self.em_alerta:
            self._mudou_normal(self.atual, agora)

    def _tique_rampa(self, agora: float):
        inicio, duracao, alvo = self._rampa
        frac = min(1.0, max(0.0, (agora - inicio) / duracao))
        pos = 1 + int((alvo - 1) * frac)
        if pos != self.atual:
            self.atual = pos
            self._escrever(pos)
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
            self.atual = 1
        else:
            self.atual = alvo
        self._escrever(self.atual)

    def sair_alerta(self):
        if not self.em_alerta:
            return
        self.em_alerta = False
        self._rampa = None
        if self.normal is not None:
            self.atual = self.normal
            self._escrever(self.normal)
