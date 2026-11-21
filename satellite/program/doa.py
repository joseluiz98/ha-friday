"""DOA v0 do XVF3800: direção de quem disse a palavra e trava de feixe (spec 2026-11-17-xvf3800-support).

Leitura: `AUDIO_MGR_SELECTED_AZIMUTHS[0]` (radianos; NaN quando nenhum feixe tem fala) a ~8 Hz, buffer de 3 s. A palavra é
reconhecida depois que termina, por isso a direção é a média circular das amostras com fala do último 1,5 s; só vale
com ≥ 3 amostras e ≥ 70% delas a até 30° da média (melhor não travar do que travar na TV).

Regra (dono, 17/11; a mesma do projeto ESPHome da comunidade, revista em 21/11): trava na palavra e fica travada durante
a primeira troca (o pedido E a resposta dela, nos dois canais); solta quando a primeira resposta termina (o microfone
reabre ou a palavra a corta: `resposta_acabou`); no resto da conversa vale o comportamento de fábrica (segue quem anda).
Solta também ao fechar a conversa, em erro, no teto de 90 s e na partida do programa (xvf_link.apply_startup).
Travar = `AEC_FIXEDBEAMSAZIMUTH_VALUES [θ, θ]` → `AEC_FIXEDBEAMSONOFF 1` → canal 1 do feixe travado (`AUDIO_MGR_OP_R
7 0`, seguro qualquer que seja o comportamento do auto-select) → canal 0, o da conversa, também (`AUDIO_MGR_OP_L 6 0`,
feixe focado processado 0), conferido por leitura; se a placa não confirmar `6 0`, o canal 0 volta a `8 0` e a trava
segue só no canal 1. Soltar = `AEC_FIXEDBEAMSONOFF 0` → `AUDIO_MGR_OP_R 7 3` → `AUDIO_MGR_OP_L 8 0`.
"""
import collections
import math
import threading
import time

JANELA_BUFFER_S = 3.0
JANELA_DECISAO_S = 1.5
MIN_AMOSTRAS = 3
FRACAO_MIN = 0.7
TOLERANCIA_GRAUS = 30.0
TETO_S = 90.0
PERIODO_LEITURA_S = 0.125


def _dif_graus(a: float, b: float) -> float:
    d = abs(math.degrees(a - b)) % 360
    return min(d, 360 - d)


class Direcao:
    def __init__(self, janela_s: float = JANELA_BUFFER_S):
        self._amostras = collections.deque()
        self._janela = janela_s
        self._lock = threading.Lock()

    def alimentar(self, t: float, az_rad) -> None:
        fala = az_rad is not None and not (isinstance(az_rad, float) and math.isnan(az_rad))
        with self._lock:
            self._amostras.append((t, float(az_rad) if fala else None))
            while self._amostras and self._amostras[0][0] < t - self._janela:
                self._amostras.popleft()

    def decidir(self, agora: float, janela_s: float = JANELA_DECISAO_S):
        with self._lock:
            az = [a for t, a in self._amostras if a is not None and agora - janela_s <= t <= agora]
        if len(az) < MIN_AMOSTRAS:
            return None
        theta = math.atan2(sum(math.sin(a) for a in az), sum(math.cos(a) for a in az))
        perto = sum(1 for a in az if _dif_graus(a, theta) <= TOLERANCIA_GRAUS)
        return theta if perto / len(az) >= FRACAO_MIN else None


class TravaFeixe:
    def __init__(self, link, log, teto_s: float = TETO_S, relogio=time.monotonic):
        self._link, self._log, self._teto, self._relogio = link, log, teto_s, relogio
        self.travado, self._desde = False, None

    def travar(self, theta: float) -> bool:
        theta = theta % (2 * math.pi)  # o firmware espera [0, 2π); a média circular devolve (−π, π]
        ok = (self._link.write("AEC_FIXEDBEAMSAZIMUTH_VALUES", [theta, theta])
              and self._link.write("AEC_FIXEDBEAMSONOFF", [1])
              and self._link.write("AUDIO_MGR_OP_R", [7, 0]))
        if ok:
            # the conversation audio is channel 0 (since 18/11): lock it too, and confirm the board took it
            if not (self._link.write("AUDIO_MGR_OP_L", [6, 0]) and self._link.read("AUDIO_MGR_OP_L") == [6, 0]):
                self._link.write("AUDIO_MGR_OP_L", [8, 0])
                self._log("trava de feixe: canal 0 ficou no 8 0")
        if not ok:
            self._log("trava de feixe: falha ao travar; volta ao modo de fábrica")
            self.travado = True  # para o soltar escrever
            self.soltar()
            return False
        self.travado, self._desde = True, self._relogio()
        self._log(f"trava de feixe: travada em {math.degrees(theta) % 360:.0f}°")
        return True

    def soltar(self) -> bool:
        if not self.travado:
            return True
        ok = self._link.write("AEC_FIXEDBEAMSONOFF", [0])
        ok = self._link.write("AUDIO_MGR_OP_R", [7, 3]) and ok
        ok = self._link.write("AUDIO_MGR_OP_L", [8, 0]) and ok
        self.travado, self._desde = False, None
        self._log("trava de feixe: solta" + ("" if ok else " (com falha de USB)"))
        return ok

    def tique(self) -> None:
        if self.travado and self._desde is not None and self._relogio() - self._desde > self._teto:
            self._log("trava de feixe: teto de 90 s")
            self.soltar()


class Regra:
    """Quando travar e soltar (regra do ESPHome). `ligada()` lê o switch Beam lock."""
    def __init__(self, direcao: Direcao, trava: TravaFeixe, ligada):
        self.direcao, self.trava, self._ligada = direcao, trava, ligada

    def palavra(self, agora: float):
        """Na palavra: trava se o switch estiver ligado e a direção for consistente; devolve os graus para o anel."""
        if not self._ligada():
            return None
        theta = self.direcao.decidir(agora)
        if theta is None or not self.trava.travar(theta):
            return None
        return math.degrees(theta) % 360

    def resposta_acabou(self) -> None:
        """The first answer ended (mic reopened, or the word cut her): release the lock, if any."""
        if self.trava.travado:
            self.trava.soltar()

    def conversa_fechou(self) -> None:
        self.trava.soltar()


def ler_continuo(link, direcao: Direcao, parar: threading.Event, relogio=time.monotonic,
                 periodo_s: float = PERIODO_LEITURA_S) -> None:
    """Thread de leitura (só com XVF): alimenta o buffer; falha de USB vira silêncio (o link já registra)."""
    while not parar.wait(periodo_s):
        v = link.read("AUDIO_MGR_SELECTED_AZIMUTHS")
        direcao.alimentar(relogio(), v[0] if v else None)
