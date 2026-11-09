"""Gravação contínua do ambiente (02/11/2026): horas de TV, rádio, música e conversa como o microfone do satélite as
ouve, para o retreino das palavras (negativos e prova de disparos falsos por hora). Liga com
`PUT /config {"gravar_ambiente_ate": "AAAA-MM-DDTHH:MM"}` (hora local, no máximo 24 h à frente) e para sozinha nessa
hora; `null` para já. 09/11: `{"gravar_ambiente_janela": "HH:MM-HH:MM"}` grava TODO DIA nessa janela pelo relógio local,
guardada na config da Pi: depois de ligada, não depende mais de rede (em 09/11 a renovação diária pelo HA caiu com a Pi
sem Wi-Fi e o dia ficou sem gravação). As duas chaves valem juntas: grava se qualquer uma mandar. Blocos WAV de `bloco_s` segundos em ~/gravacoes/ambiente/ (16 kHz mono), que o envio noturno leva
para a VM. A transcrição na VM tira os trechos com alguma palavra de ativação antes de virarem negativos.
Testes: test_ambiente.py."""
import datetime, os, wave

PASTA = "/home/jose/gravacoes/ambiente"
MAX_A_FRENTE = datetime.timedelta(hours=24)


def interpretar_ate(valor, agora):
    """Valida `gravar_ambiente_ate`: None, ou "AAAA-MM-DDTHH:MM" no futuro e no máximo 24 h à frente."""
    if valor is None:
        return None
    if not isinstance(valor, str):
        raise ValueError("gravar_ambiente_ate: texto AAAA-MM-DDTHH:MM ou null")
    try:
        ate = datetime.datetime.strptime(valor, "%Y-%m-%dT%H:%M")
    except ValueError:
        raise ValueError("gravar_ambiente_ate: formato AAAA-MM-DDTHH:MM") from None
    if not (agora < ate <= agora + MAX_A_FRENTE):
        raise ValueError("gravar_ambiente_ate: entre agora e 24 h à frente")
    return valor


def interpretar_janela(valor):
    """Valida `gravar_ambiente_janela`: None, ou "HH:MM-HH:MM" (início diferente do fim; pode passar da meia-noite)."""
    if valor is None:
        return None
    if not isinstance(valor, str) or len(valor) != 11 or valor[5] != "-":
        raise ValueError("gravar_ambiente_janela: texto HH:MM-HH:MM ou null")
    try:
        ini, fim = (datetime.datetime.strptime(x, "%H:%M").time() for x in (valor[:5], valor[6:]))
    except ValueError:
        raise ValueError("gravar_ambiente_janela: horas HH:MM") from None
    if ini == fim:
        raise ValueError("gravar_ambiente_janela: início igual ao fim")
    return valor


def dentro_da_janela(janela, agora):
    """True se `agora` (hora local) está em [início, fim) da janela diária; janela None = nunca."""
    if not janela:
        return False
    ini, fim, t = janela[:5], janela[6:], agora.strftime("%H:%M")
    return ini <= t < fim if ini < fim else (t >= ini or t < fim)


class GravadorAmbiente:
    """Recebe cada pedaço de áudio do laço de captura; grava enquanto `agora < ate` ou dentro da janela diária, em blocos
    de `bloco_s`."""

    def __init__(self, pasta=PASTA, bloco_s=300, taxa=16000):
        self.pasta, self.bloco_s, self.taxa = pasta, bloco_s, taxa
        self._wav, self._inicio, self._ate_txt, self._ate = None, None, None, None

    @property
    def gravando(self) -> bool:
        return self._wav is not None

    def alimentar(self, pcm: bytes, agora: datetime.datetime, ate_txt, janela=None):
        if ate_txt != self._ate_txt:  # mudou pela porta: interpreta uma vez
            self._ate_txt = ate_txt
            try:
                self._ate = datetime.datetime.strptime(ate_txt, "%Y-%m-%dT%H:%M") if ate_txt else None
            except (TypeError, ValueError):
                self._ate = None
        try:
            na_janela = dentro_da_janela(janela, agora)
        except (TypeError, IndexError):
            na_janela = False
        if not na_janela and (self._ate is None or agora >= self._ate):
            self.fechar()
            return
        if self._wav is not None and (agora - self._inicio).total_seconds() >= self.bloco_s:
            self.fechar()
        if self._wav is None:
            os.makedirs(self.pasta, mode=0o700, exist_ok=True)
            self._inicio = agora
            w = wave.open(os.path.join(self.pasta, f"ambiente_{agora:%Y%m%d_%H%M%S}.wav"), "wb")
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(self.taxa)
            self._wav = w
        self._wav.writeframes(pcm)

    def fechar(self):
        if self._wav is not None:
            self._wav.close()
            self._wav = None
