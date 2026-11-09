"""Volume de fábrica de um Mark novo: 65%, definido pelo dono no teste do MK2 (06/11). Sem isto, a primeira fala sai no
nível que o aparelho guardou, e o "oi" dela pode passar sem ser ouvido. Só roda enquanto `volume` da config é None.

Três caminhos, nesta ordem: Jabra (posição 0–11 do PCM, conferido); XVF3800 (tenta PCM; o nome real do controle se
confere quando o aparelho chegar) e o genérico (qualquer controle de reprodução, preferindo placa USB)."""
import re, subprocess

from volume import posicao_de_percentual

PADRAO_PCT = 65
PREFERIDOS = ("PCM", "Master", "Speaker", "Headphone")


def placas(texto: str) -> list:
    """/proc/asound/cards -> [{"id", "desc"}]."""
    return [{"id": m.group(1), "desc": m.group(2)} for m in re.finditer(r"^\s*\d+\s+\[(\S+)\s*\]:\s*(.+)$", texto, re.M)]


def controles_de_reproducao(scontents: str) -> list:
    """Nomes com volume de reprodução em `amixer scontents`; softvol ("volume" sem o p) fica de fora: é o da música."""
    return [m.group(1) for m in re.finditer(r"Simple mixer control '([^']+)',0\n\s*Capabilities:[^\n]*\bpvolume\b", scontents)]


def _ordenar(nomes: list) -> list:
    return sorted(nomes, key=lambda n: PREFERIDOS.index(n) if n in PREFERIDOS else len(PREFERIDOS))


def _sset(run, card, controle, valor) -> bool:
    try:
        return run(["amixer", "-q", "-c", card, "sset", controle, valor], timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _contents(run, card) -> str:
    try:
        return run(["amixer", "-c", card, "scontents"], capture_output=True, text=True, timeout=5).stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


def aplicar(pct=PADRAO_PCT, cards_texto=None, run=subprocess.run) -> dict:
    """Devolve {"via", "card", "controle"}; via None quando nenhum aparelho aceitou (nunca levanta)."""
    if cards_texto is None:
        try:
            with open("/proc/asound/cards") as f:
                cards_texto = f.read()
        except OSError:
            cards_texto = ""
    todas = placas(cards_texto)
    for c in todas:
        if "jabra" in c["desc"].lower() and _sset(run, c["id"], "PCM", str(posicao_de_percentual(pct))):
            return {"via": "jabra", "card": c["id"], "controle": "PCM"}
    for c in todas:
        if re.search(r"xvf3800|respeaker", c["desc"], re.I) and _sset(run, c["id"], "PCM", f"{int(pct)}%"):
            return {"via": "xvf3800", "card": c["id"], "controle": "PCM"}
    for c in sorted(todas, key=lambda x: "usb" not in x["desc"].lower()):
        for nome in _ordenar(controles_de_reproducao(_contents(run, c["id"]))):
            if _sset(run, c["id"], nome, f"{int(pct)}%"):
                return {"via": "generico", "card": c["id"], "controle": nome}
    return {"via": None, "card": None, "controle": None}
