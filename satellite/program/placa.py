"""Camada da placa de áudio (spec 2026-11-17 xvf3800-support): o programa monta volume, música, mudo e botão pela
placa que o `friday-audio-select` achou no boot (`/etc/friday-satelite/audio_board`: xvf3800 | jabra | none).

Jabra = o código de hoje (hardware.py), sem mudança de comportamento. XVF3800 = volume pelo controle PCM do card
"Array" (valores crus), mudo pelo pino X0D30 do chip (pelo canal USB único `XvfLink`) e sem botão nem teclas.
Quem escreve no USB do XVF é só o `XvfLink` (Task 4); aqui ele chega pronto, por parâmetro."""
import re
import subprocess

import hardware
from escala import VOLUME_XVF  # curva de altura do XVF; os níveis de hardware são os 61 crus

ARQUIVO_PLACA = "/etc/friday-satelite/audio_board"
PLACAS = ("xvf3800", "jabra", "none")
PINO_MUDO = 30  # X0D30: o botão de mudo da placa corta o microfone em hardware e este pino reflete o estado


def ler_placa(caminho=ARQUIVO_PLACA) -> str:
    """Arquivo ausente ou ilegível = jabra: satélite antigo (antes do audio-select) é o Jabra de sempre."""
    try:
        with open(caminho) as f:
            valor = f.read().strip()
    except OSError:
        return "jabra"
    return valor if valor in PLACAS else "jabra"


class MixerXvf:
    """Controle PCM do card Array: escreve e lê o nível cru 0–60 (1 dB cada)."""
    def __init__(self, card="Array", controle="PCM", executar=subprocess.run):
        self.card, self.controle, self._executar = card, controle, executar

    def ler(self) -> int:
        """Nível cru 0–60 (1 dB cada)."""
        r = self._executar(["amixer", "-c", self.card, "sget", self.controle], capture_output=True, text=True,
                           timeout=5)
        m = re.search(r"Playback (\d+) \[", r.stdout or "")
        if not m:
            raise RuntimeError(f"amixer sem a posição do {self.controle}")
        return int(m.group(1))

    def escrever(self, nivel: int):
        valor = max(0, min(60, int(nivel)))
        self._executar(["amixer", "-q", "-c", self.card, "sset", self.controle, f"{valor},{valor}"], timeout=5)

    def fixar_mestre(self) -> bool:
        """18/11 (dono): o segundo controle de reprodução da placa (PCM índice 1) nasce em −20 dB e o ALSA o restaura
        assim a cada boot; com ele a saída P2 ficava fraca demais. Fica em 0 dB a cada partida; o volume da casa
        segue só no PCM índice 0. Falha vira False (o programa sobe do mesmo jeito)."""
        try:
            r = self._executar(["amixer", "-q", "-c", self.card, "cset", f"name={self.controle} Playback Volume,index=1",
                                str(VOLUME_XVF[-1])], timeout=5)
        except (OSError, subprocess.SubprocessError):
            return False
        return getattr(r, "returncode", 0) == 0


class MudoXvf:
    """Mudo da captura no XVF3800 pelo pino X0D30, pelo mesmo canal USB do anel e da trava de feixe. Botão e HA
    escrevem/leem o mesmo pino: nunca se contradizem. Nunca levanta: o link já registra a falha e devolve False/None."""
    def __init__(self, link):
        self._link = link

    def aplicar(self, mudo: bool) -> bool:
        if self._link is None:
            return False
        return bool(self._link.write("GPO_WRITE_VALUE", [PINO_MUDO, 1 if mudo else 0]))

    def ler(self):
        """True = mudo (pino em 1), False = aberto, None = não deu para ler."""
        if self._link is None:
            return None
        valores = self._link.read("GPO_READ_VALUES")
        if not valores or len(valores) < 2:
            return None
        return bool(valores[1])


def montar(placa: str, link=None) -> dict:
    """{"mixer", "music_mixer", "controle_mudo", "botao"}; `botao` é a classe do botão do Jabra, None no XVF."""
    if placa == "xvf3800":
        return {"mixer": MixerXvf(), "music_mixer": hardware.MusicMixer(card="Array"),
                "controle_mudo": MudoXvf(link), "botao": None, "fine_mixer": hardware.FineMixer(card="Array"),
                "escala": "xvf3800"}
    return {"mixer": hardware.Mixer(), "music_mixer": hardware.MusicMixer(), "controle_mudo": hardware.ControleMudo(),
            "botao": hardware.Botao, "fine_mixer": hardware.FineMixer(), "escala": "jabra"}
