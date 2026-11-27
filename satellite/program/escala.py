"""Escala fina de volume (spec 2026-11-27). Lógica pura, sem hardware.

O volume é um percentual inteiro 0–100. A CURVA de altura (ANCORAS) liga 12 percentuais de grade (a posição 0–11: a
escala falada e o LED do Jabra) a um valor em dB. Os NIVEIS são o que cada placa tem de verdade: 12 degraus de 4 dB no
Jabra, 61 níveis de 1 dB no XVF3800 (índice = valor cru do controle). A placa fica no nível mais baixo que alcança o
alvo e o softvol `Fine` atenua a diferença (trim, sempre <= 0)."""

MAX_POSICAO = 11
# XVF3800: valor cru de cada posição na curva de altura (a posição 0 era 0, quase mudo; 26 desde 27/11)
VOLUME_XVF = [26, 30, 33, 37, 40, 43, 47, 50, 53, 57, 58, 60]

ANCORAS = {"jabra": tuple(-36 + 4 * k for k in range(12)), "xvf3800": tuple(v - 60 for v in VOLUME_XVF)}
NIVEIS = {"jabra": ANCORAS["jabra"], "xvf3800": tuple(range(-60, 1))}


def grade(k: int) -> int:
    """Percentual de grade da posição k."""
    return round(k * 100 / MAX_POSICAO)


GRADE = tuple(grade(k) for k in range(MAX_POSICAO + 1))


def _valido(pct) -> None:
    if not isinstance(pct, int) or isinstance(pct, bool) or not 0 <= pct <= 100:
        raise ValueError("percentual de 0 a 100")


def posicao_de_pct(pct: int) -> int:
    """Menor posição cuja grade alcança o percentual."""
    _valido(pct)
    return next(k for k in range(MAX_POSICAO + 1) if GRADE[k] >= pct)


def alvo_db(pct: int, placa: str) -> float:
    k = posicao_de_pct(pct)
    a = ANCORAS[placa]
    if GRADE[k] == pct:
        return a[k]
    return a[k - 1] + (a[k] - a[k - 1]) * (pct - GRADE[k - 1]) / (GRADE[k] - GRADE[k - 1])


def nivel_de_pct(pct: int, placa: str) -> int:
    alvo = alvo_db(pct, placa) - 1e-9  # folga de ponto flutuante
    return next(i for i, db in enumerate(NIVEIS[placa]) if db >= alvo)


def trim_db(pct: int, placa: str) -> float:
    return min(0.0, alvo_db(pct, placa) - NIVEIS[placa][nivel_de_pct(pct, placa)])


def pct_de_db(db: float, placa: str) -> int:
    """O percentual cuja altura é a mais próxima de `db` (já limitado a 0..100)."""
    return min(range(101), key=lambda p: abs(alvo_db(p, placa) - db))
