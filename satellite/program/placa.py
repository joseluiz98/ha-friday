"""Camada da placa de áudio (spec 2026-11-17 xvf3800-support): o programa monta volume, música, mudo e botão pela
placa que o `friday-audio-select` achou no boot (`/etc/friday-satelite/audio_board`: xvf3800 | jabra | none).

Jabra = o código de hoje (hardware.py), sem mudança de comportamento. XVF3800 = volume pelo controle PCM do card
"Array" (valores crus), mudo pelo pino X0D30 do chip (pelo canal USB único `XvfLink`) e sem botão nem teclas.
Quem escreve no USB do XVF é só o `XvfLink` (Task 4); aqui ele chega pronto, por parâmetro."""
import re
import subprocess

import hardware

ARQUIVO_PLACA = "/etc/friday-satelite/audio_board"
PLACAS = ("xvf3800", "jabra", "none")
# posição 0–11 do volume.py -> valor cru do controle PCM do XVF (0–60, −60…0 dB). A posição 11 (= número 10) é 0 dB;
# a 10 não tem número (volume.py as pula). 1 = −30 dB … 60 = 0 dB: acima disso seria só corte digital.
VOLUME_XVF = [0, 30, 33, 37, 40, 43, 47, 50, 53, 57, 58, 60]
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
    """Controle PCM do card Array. Mesma interface do hardware.Mixer: posição 0–11 <-> valor cru da tabela."""
    def __init__(self, card="Array", controle="PCM", executar=subprocess.run):
        self.card, self.controle, self._executar = card, controle, executar

    def ler(self) -> int:
        r = self._executar(["amixer", "-c", self.card, "sget", self.controle], capture_output=True, text=True,
                           timeout=5)
        m = re.search(r"Playback (\d+) \[", r.stdout or "")
        if not m:
            raise RuntimeError(f"amixer sem a posição do {self.controle}")
        cru = int(m.group(1))
        return min(range(len(VOLUME_XVF)), key=lambda i: (abs(VOLUME_XVF[i] - cru), i))

    def escrever(self, posicao: int):
        valor = VOLUME_XVF[max(0, min(len(VOLUME_XVF) - 1, int(posicao)))]
        self._executar(["amixer", "-q", "-c", self.card, "sset", self.controle, f"{valor},{valor}"], timeout=5)


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
                "controle_mudo": MudoXvf(link), "botao": None}
    return {"mixer": hardware.Mixer(), "music_mixer": hardware.MusicMixer(), "controle_mudo": hardware.ControleMudo(),
            "botao": hardware.Botao}
