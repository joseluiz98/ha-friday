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
import json
import math
import os
import threading
import time

JANELA_BUFFER_S = 3.0
JANELA_DECISAO_S = 1.5
MIN_AMOSTRAS = 3
FRACAO_MIN = 0.7
TOLERANCIA_GRAUS = 30.0
TETO_S = 90.0
PERIODO_LEITURA_S = 0.125
JANELA_REAPONTA_S = 0.6
REAPONTA_ESTAVEL_GRAUS = 20.0
REAPONTA_DISTANCIA_GRAUS = 30.0
TV_SETOR_GRAUS = 30.0
HISTORICO_S = 7 * 86400.0
MIN_HISTORICO = 10


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

    def decidir(self, agora: float, janela_s: float = JANELA_DECISAO_S, excluir=None):
        """`excluir` = (theta_rad, meia_largura_graus): amostras dentro desse setor (a TV) são descartadas antes das regras."""
        with self._lock:
            az = [a for t, a in self._amostras if a is not None and agora - janela_s <= t <= agora]
        if excluir is not None:
            centro, meia = excluir
            az = [a for a in az if _dif_graus(a, centro) > meia]
        if len(az) < MIN_AMOSTRAS:
            return None
        theta = math.atan2(sum(math.sin(a) for a in az), sum(math.cos(a) for a in az))
        perto = sum(1 for a in az if _dif_graus(a, theta) <= TOLERANCIA_GRAUS)
        return theta if perto / len(az) >= FRACAO_MIN else None


class Reaponta:
    """Re-aim helper (spec D4): quem fala fora dos dois feixes, com energia livre, 3 de 4 leituras, estável a ±20°."""
    def __init__(self):
        self._polls = collections.deque(maxlen=16)
        self.ultimo_polls = 0  # how many polls backed the last positive decision (for `two_beam_reaim`)

    def alimentar(self, t: float, az_livre, e_livre) -> None:
        self._polls.append((t, az_livre, e_livre))

    def zerar(self) -> None:
        self._polls.clear()

    def decidir(self, agora: float, theta_a: float, theta_t):
        ultimas = [p for p in self._polls if agora - JANELA_REAPONTA_S <= p[0] <= agora][-4:]
        validas = [az for _, az, e in ultimas
                   if az is not None and not math.isnan(az) and e is not None and e > 0]
        if len(validas) < 3:
            return None
        media = math.atan2(sum(math.sin(a) for a in validas), sum(math.cos(a) for a in validas))
        if any(_dif_graus(a, media) > REAPONTA_ESTAVEL_GRAUS for a in validas):
            return None
        if _dif_graus(media, theta_a) < REAPONTA_DISTANCIA_GRAUS:
            return None
        if theta_t is not None and _dif_graus(media, theta_t) < REAPONTA_DISTANCIA_GRAUS:
            return None
        self.ultimo_polls = len(validas)
        return media


class TravaFeixe:
    def __init__(self, link, log, teto_s: float = TETO_S, relogio=time.monotonic):
        """`log` aceita str (linha de diário) ou dict (evento; o programa imprime como JSON de topo)."""
        self._link, self._log, self._teto, self._relogio = link, log, teto_s, relogio
        self.travado, self._desde = False, None
        self.theta_a, self.theta_t = None, None
        self.travada_desde, self.motivo_soltura = None, None
        self.soltada_em = None  # when the beams were last released (the lock duration ends there, not at the close)

    def _sufixo_tv(self) -> str:
        return "" if self.theta_t is None else f", beam_t_deg={math.degrees(self.theta_t) % 360:.0f}"

    def travar(self, theta_a: float, theta_t=None, elev_t: float = 0.0) -> bool:
        theta_a = theta_a % (2 * math.pi)  # o firmware espera [0, 2π); a média circular devolve (−π, π]
        if theta_t is not None:
            theta_t = theta_t % (2 * math.pi)
        ok = self._link.write("AEC_FIXEDBEAMSAZIMUTH_VALUES", [theta_a, theta_a if theta_t is None else theta_t])
        if ok and theta_t is not None:
            ok = self._link.write("AEC_FIXEDBEAMSELEVATION_VALUES", [0.0, elev_t])
        ok = (ok and self._link.write("AEC_FIXEDBEAMSONOFF", [1])
              and self._link.write("AUDIO_MGR_OP_R", [7, 0]))
        if ok:
            # the conversation audio is channel 0 (since 18/11): lock it too, and confirm the board took it
            if not (self._link.write("AUDIO_MGR_OP_L", [6, 0]) and self._link.read("AUDIO_MGR_OP_L") == [6, 0]):
                self._link.write("AUDIO_MGR_OP_L", [8, 0])
                self._log("trava de feixe: canal 0 ficou no 8 0")
        if not ok:
            self._log("trava de feixe: falha ao travar; volta ao modo de fábrica")
            self.travado = True  # para o soltar escrever
            self.soltar("error")
            return False
        self.theta_a, self.theta_t = theta_a, theta_t
        self.travado, self._desde = True, self._relogio()
        self.travada_desde, self.motivo_soltura, self.soltada_em = self._desde, None, None
        self._log(f"trava de feixe: travada em {math.degrees(theta_a) % 360:.0f}°{self._sufixo_tv()}")
        return True

    def reapontar(self, theta_a: float, causa: str = "follow_up", polls: int = 0) -> bool:
        """Re-aim beam A only: rewrite the azimuth pair [θa, current θt]; the TV beam stays."""
        if not self.travado:
            return False
        theta_a = theta_a % (2 * math.pi)
        ok = self._link.write("AEC_FIXEDBEAMSAZIMUTH_VALUES",
                              [theta_a, theta_a if self.theta_t is None else self.theta_t])
        if ok:
            antes = self.theta_a
            self.theta_a = theta_a
            self._log({"two_beam_reaim": {"cause": causa,
                                          "from_deg": round(math.degrees(antes) % 360, 1),
                                          "to_deg": round(math.degrees(theta_a) % 360, 1),
                                          "polls": polls}})
        return ok

    def renovar(self) -> None:
        """Re-arm the ceiling from now (a turn is still going on)."""
        if self.travado:
            self._desde = self._relogio()

    def soltar(self, motivo: str = "closed") -> bool:
        if not self.travado:
            return True
        sufixo = self._sufixo_tv()
        ok = self._link.write("AEC_FIXEDBEAMSONOFF", [0])
        ok = self._link.write("AEC_FIXEDBEAMSELEVATION_VALUES", [0.0, 0.0]) and ok
        ok = self._link.write("AUDIO_MGR_OP_R", [7, 3]) and ok
        ok = self._link.write("AUDIO_MGR_OP_L", [8, 0]) and ok
        self.travado, self._desde = False, None
        self.theta_a, self.theta_t = None, None
        self.motivo_soltura, self.soltada_em = motivo, self._relogio()
        self._log("trava de feixe: solta" + sufixo + ("" if ok else " (com falha de USB)"))
        return ok

    def tique(self) -> None:
        if self.travado and self._desde is not None and self._relogio() - self._desde > self._teto:
            self._log("trava de feixe: teto de 90 s")
            self.soltar("ceiling90")


class Regra:
    """Quando travar e soltar (regra do ESPHome). `ligada()` lê o switch Beam lock.

    Dois feixes (`dois_feixes()` verdadeiro): trava o feixe do falante e o da TV (`tv()` = (theta_rad, elev_rad) ou None)
    durante a conversa inteira, e ignora o switch Beam lock (os feixes fazem parte do recurso)."""
    def __init__(self, direcao: Direcao, trava: TravaFeixe, ligada, dois_feixes=lambda: False, tv=lambda: None):
        self.direcao, self.trava, self._ligada = direcao, trava, ligada
        self._dois_feixes, self._tv = dois_feixes, tv
        self.dois_feixes_ativo = False
        self.ultima_decisao = ""
        self.ocupada = False  # the TV calibration owns the board: no lock on a wake word meanwhile

    def palavra(self, agora: float):
        """Na palavra: trava se a direção for consistente; devolve os graus para o anel."""
        if self.ocupada:
            self.ultima_decisao = "board_busy"
            return None
        if self._dois_feixes():
            return self._palavra_dois_feixes(agora)
        if not self._ligada():
            return None
        theta = self.direcao.decidir(agora)
        if theta is None or not self.trava.travar(theta):
            self.ultima_decisao = "lock_failed"
            return None
        self.ultima_decisao = "locked"
        return math.degrees(theta) % 360

    def _palavra_dois_feixes(self, agora: float):
        tv = self._tv()
        theta = self.direcao.decidir(agora)
        excluida = False
        if theta is None and tv is not None:
            theta = self.direcao.decidir(agora, excluir=(tv[0], TV_SETOR_GRAUS))
            excluida = theta is not None
        ok = theta is not None and (self.trava.travar(theta, tv[0], tv[1]) if tv is not None
                                    else self.trava.travar(theta))
        if not ok:
            self.ultima_decisao = "lock_failed"
            return None
        self.ultima_decisao = "locked_tv_excluded" if excluida else "locked"
        self.dois_feixes_ativo = True
        return math.degrees(theta) % 360

    def resposta_acabou(self) -> None:
        """The first answer ended (mic reopened, or the word cut her): release the lock, unless it is a whole-conversation one."""
        if self.dois_feixes_ativo:
            return
        if self.trava.travado:
            self.trava.soltar()

    def conversa_fechou(self) -> None:
        self.trava.soltar()
        self.dois_feixes_ativo = False


def registrar_direcao(caminho: str, theta: float, agora: float) -> None:
    """Append one caller direction (degrees) to an NDJSON file, pruning lines older than 7 days."""
    mantidas = [r for r in _ler_direcoes(caminho) if r["t"] >= agora - HISTORICO_S]
    mantidas.append({"t": agora, "deg": round(math.degrees(theta) % 360, 1)})
    tmp = caminho + ".tmp"
    with open(tmp, "w") as f:
        f.write("".join(json.dumps(r) + "\n" for r in mantidas))
    os.replace(tmp, caminho)


def _ler_direcoes(caminho: str) -> list:
    try:
        with open(caminho) as f:
            linhas = f.read().splitlines()
    except OSError:
        return []
    out = []
    for ln in linhas:
        try:
            r = json.loads(ln)
            out.append({"t": float(r["t"]), "deg": float(r["deg"])})
        except (ValueError, KeyError, TypeError):
            continue
    return out


def mediana_direcoes(caminho: str, agora: float):
    """Circular median (radians, [0, 2π)) of the last 7 days; None with fewer than 10 lines."""
    graus = [r["deg"] for r in _ler_direcoes(caminho) if r["t"] >= agora - HISTORICO_S]
    if len(graus) < MIN_HISTORICO:
        return None
    melhor = min(graus, key=lambda c: sum(_dif_graus(math.radians(c), math.radians(g)) for g in graus))
    return math.radians(melhor) % (2 * math.pi)


def direcao_da_palavra(direcao: Direcao, agora: float, tv=None, theta_a=None):
    """Direction (radians) of a wake word said inside a two-beam conversation: the TV sector never counts. `direcao` is
    fed by the free-running beam (the fixed beams only ever report theta_a or theta_t). Within REAPONTA_DISTANCIA_GRAUS
    of the current beam A there is nothing to re-aim: None."""
    if tv is not None:
        theta = direcao.decidir(agora, excluir=(tv[0], TV_SETOR_GRAUS))
    else:
        theta = direcao.decidir(agora)
    if theta is not None and theta_a is not None and _dif_graus(theta, theta_a) < REAPONTA_DISTANCIA_GRAUS:
        return None
    return theta


def ler_continuo(link, direcao: Direcao, parar: threading.Event, relogio=time.monotonic,
                 periodo_s: float = PERIODO_LEITURA_S, energia=None, reaponta=None, ativo=None, ao_ciclo=None, livre=None) -> None:
    """Thread de leitura (só com XVF): alimenta o buffer; falha de USB vira silêncio (o link já registra).
    Dois feixes (`ativo()` verdadeiro): lê também as energias e o azimute livre, com leitura de prazo, e alimenta
    `energia` e `reaponta`; `ao_ciclo(t)` roda a cada leitura nesse modo (o programa decide o re-aim ali)."""
    while not parar.wait(periodo_s):
        v = link.read("AUDIO_MGR_SELECTED_AZIMUTHS")
        t = relogio()
        direcao.alimentar(t, v[0] if v else None)
        if ativo is None or not ativo():
            continue
        e = link.read_bounded("AEC_SPENERGY_VALUES")
        az = link.read_bounded("AEC_AZIMUTH_VALUES")
        if energia is not None:
            energia.atualizar(t, e)
        az_livre = az[2] if az and len(az) > 2 else None
        e_livre = e[2] if e and len(e) > 2 else None
        if reaponta is not None:
            reaponta.alimentar(t, az_livre, e_livre)
        if livre is not None:  # the free-running beam: the only source that still follows a second speaker
            livre.alimentar(t, az_livre if e_livre is not None and e_livre > 0 else None)
        if ao_ciclo is not None:
            try:
                ao_ciclo(t)
            except Exception:  # noqa: BLE001 - the poll thread never dies from the re-aim
                pass


CALIB_AZIMUTE_S = 20.0
CALIB_ELEVACAO_S = 3.0
CALIB_MIN_QUADROS = 40
CALIB_DISPERSAO_MAX_GRAUS = 30.0
CALIB_PERTO_DO_FALANTE_GRAUS = 45.0
CALIB_ELEVACOES = (0, 15, 30, 45, 60, 75)


def _mediana_circular(rads):
    return min(rads, key=lambda c: sum(_dif_graus(c, r) for r in rads)) % (2 * math.pi)


def _percentil90(valores):
    v = sorted(valores)
    return v[max(0, math.ceil(0.9 * len(v)) - 1)]


def aprender_tv(link, agora, dormir, parar, historico_rad):
    """Learn where the TV is (spec D5). Blocking: the program runs it in a thread. Free beam azimuth for 20 s (circular
    median of the frames with energy), then a sweep of the fixed beam's elevation (3 s each, highest mean energy wins).
    Always releases the board at the end. Returns result/reason/azimuth_deg/elevation_deg/spread_deg/frames and
    energy_by_elevation (the means, rounded; empty if the sweep did not run)."""
    r = {"result": "refused", "reason": None, "azimuth_deg": None, "elevation_deg": None, "spread_deg": None,
         "frames": 0, "energy_by_elevation": []}
    try:
        link.write("AEC_FIXEDBEAMSONOFF", [0])
        azimutes, t0 = [], agora()
        while agora() - t0 < CALIB_AZIMUTE_S:
            if parar():
                r["reason"] = "aborted"
                return r
            e, az = link.read_bounded("AEC_SPENERGY_VALUES"), link.read_bounded("AEC_AZIMUTH_VALUES")
            if e and az and len(e) > 2 and len(az) > 2 and e[2] > 0 and not math.isnan(az[2]):
                azimutes.append(az[2] % (2 * math.pi))
            dormir(PERIODO_LEITURA_S)
        r["frames"] = len(azimutes)
        if len(azimutes) < CALIB_MIN_QUADROS:
            r["reason"] = "too_few_frames"
            return r
        theta = _mediana_circular(azimutes)
        dispersao = _percentil90([_dif_graus(a, theta) for a in azimutes])
        r["azimuth_deg"], r["spread_deg"] = round(math.degrees(theta) % 360, 1) % 360, round(dispersao, 1)
        if dispersao > CALIB_DISPERSAO_MAX_GRAUS:
            r["reason"] = "spread"
            return r
        if historico_rad is not None and _dif_graus(theta, historico_rad) <= CALIB_PERTO_DO_FALANTE_GRAUS:
            r["reason"] = "near_caller"
            return r
        medias = []
        for fi in CALIB_ELEVACOES:
            if parar():
                r["reason"] = "aborted"
                return r
            phi = math.radians(fi)
            link.write("AEC_FIXEDBEAMSAZIMUTH_VALUES", [theta, theta])
            link.write("AEC_FIXEDBEAMSELEVATION_VALUES", [phi, phi])
            link.write("AEC_FIXEDBEAMSONOFF", [1])
            soma, n, t1 = 0.0, 0, agora()
            while agora() - t1 < CALIB_ELEVACAO_S:
                if parar():
                    r["reason"] = "aborted"
                    return r
                e = link.read_bounded("AEC_SPENERGY_VALUES")
                if e:
                    soma, n = soma + e[0], n + 1
                dormir(PERIODO_LEITURA_S)
            medias.append(soma / n if n else 0.0)
            r["energy_by_elevation"] = [round(m, 4) for m in medias]
        r["elevation_deg"] = CALIB_ELEVACOES[max(range(len(medias)), key=medias.__getitem__)]
        r["result"] = "stored"
        return r
    except Exception:  # noqa: BLE001 - a USB failure ends the routine; the cleanup below still runs
        r.update(result="refused", reason="error", elevation_deg=None)
        return r
    finally:
        for nome, valores in (("AEC_FIXEDBEAMSONOFF", [0]), ("AEC_FIXEDBEAMSELEVATION_VALUES", [0.0, 0.0]),
                              ("AUDIO_MGR_OP_R", [7, 3]), ("AUDIO_MGR_OP_L", [8, 0])):
            try:
                link.write(nome, valores)
            except Exception:  # noqa: BLE001
                pass
