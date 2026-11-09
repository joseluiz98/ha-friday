"""Acesso ao Jabra Speak 410 (spec §2.1). Saída SEMPRE pelo default a 48 kHz (/etc/asound.conf); captura
16 kHz mono. O anel usa o relatório HID 3; o bit 0x05 corta o microfone e é proibido aqui (D4)."""
import glob, os, queue, re, struct, subprocess, threading, time, wave

# nao_perturbe 0x41 = 3 verdes piscando sem toque (Task 3, 08/10). NUNCA 0x40 sem o 01: toca a campainha.
# alarme (spec 2026-10-12 §2) = 0x10, o mesmo verde piscando de "ouvindo": é o único padrão visível SEM o bit 0, e o
# bit 0 ecoa no relatório do botão (piscar por software deixaria o botão surdo justo no alarme). 0x02 e 0x20 não
# acendem nada (06/10); 0x08 e 0x40 tocam a campainha do Jabra.
# ouvindo = 0x11 desde 04/11 (bancada do botão): visual idêntica à antiga 0x10, mas com o bit 0 o vermelho (00) se
# distingue do verde (bit 0x10). O alarme segue 0x10: lá qualquer botão para o alarme ("ambos").
BITS_DO_ANEL = {"parada": 0x00, "ouvindo": 0x11, "cerebro": 0x01, "nao_perturbe": 0x41, "alarme": 0x10}


class Captura:
    def __init__(self, chunk=2560):
        self.chunk = chunk
        self._p = subprocess.Popen(["arecord", "-q", "-D", "default", "-f", "S16_LE", "-r", "16000", "-c", "1",
                                    "-t", "raw"], stdout=subprocess.PIPE)

    def ler(self) -> bytes:
        b = self._p.stdout.read(self.chunk)
        if len(b) < self.chunk:
            raise EOFError("captura do Jabra terminou (desconectado?)")
        return b

    def fechar(self):
        self._p.terminate()


# 09/11: o aplay fica em underrun depois do último som; ao fechar, o ALSA repetia o último trecho do buffer (o fim do
# bipe de erro voltava ~1,6 s depois). 0,5 s de silêncio (> buffer de 0,4 s) antes de fechar: o que se repete é mudo.
SILENCIO_AO_FECHAR = bytes(48000 * 2 * 2 // 2)


class AltoFalante:
    """Um aplay do programa por vez (sons e voz dela entram na mesma fila); a mistura com a música é do dmix
    do ALSA (PCM "voice", ver asound.conf) — o Jabra não é mais aberto direto. O aplay abre SÓ quando há som e fecha após `ocioso_s` sem nada (06/10: com a saída sempre ativa
    a nota da palavra "Friday" caiu para a beira do limiar). Buffer de 100 ms para o som sair rápido."""
    def __init__(self, ocioso_s=2.0):
        self._fila = queue.Queue()
        self._ocioso_s = ocioso_s
        self._p = None
        self._vivo = True
        self.ultimo_envio = None  # monotônico do último write no aplay (o ducking olha isto)
        self._geracao = 0  # sobe a cada `parar_ja`: distingue aplay morto de propósito de Jabra fora
        threading.Thread(target=self._laco, daemon=True).start()

    def _abrir(self):
        self._p = subprocess.Popen(["aplay", "-q", "-D", "voice", "-t", "raw", "-f", "S16_LE", "-r", "48000",
                                    "-c", "2", "--buffer-time=400000", "--start-delay=100000"],
                                   stdin=subprocess.PIPE)  # 06/10: 100 ms dava "underrun" e voz robótica

    def _fechar_aplay(self):
        if self._p:
            try:
                self._p.stdin.write(SILENCIO_AO_FECHAR); self._p.stdin.flush()
            except Exception:
                pass  # aplay já fora: fecha do mesmo jeito
            try:
                self._p.stdin.close()
                self._p.wait(timeout=2)
            except Exception:
                self._p.kill()
            self._p = None

    def _laco(self):
        while self._vivo:
            try:
                dados = self._fila.get(timeout=self._ocioso_s if self._p else 0.5)
            except queue.Empty:
                self._fechar_aplay()
                continue
            geracao = self._geracao
            if self._p is None:
                self._abrir()
            p = self._p
            try:
                p.stdin.write(dados); p.stdin.flush()
                self.ultimo_envio = time.monotonic()
            except (BrokenPipeError, ValueError, OSError, AttributeError):
                if geracao != self._geracao:
                    continue  # `parar_ja` matou este aplay de propósito (botão vermelho): não é o Jabra fora
                os._exit(3)  # aplay morreu (Jabra fora): o systemd religa

    def tocar_pcm48(self, pcm: bytes):
        self._fila.put(pcm)

    def tocar_wav(self, caminho: str):
        with wave.open(caminho) as w:
            self._fila.put(w.readframes(w.getnframes()))

    def limpar(self):
        """Descarta o que ainda não foi para o aplay (pausar/parar o alarme). O que já está no buffer do aplay
        (até 0,4 s) ainda toca."""
        while True:
            try:
                self._fila.get_nowait()
            except queue.Empty:
                return

    def parar_ja(self):
        """Botão vermelho (spec 2026-11-04): descarta a fila E mata o aplay agora, sem esperar o buffer de até 0,4 s.
        O próximo som reabre o aplay normalmente."""
        self._geracao += 1
        self.limpar()
        p, self._p = self._p, None
        if p:
            try:
                p.kill()
                p.wait(timeout=1)
            except Exception:
                pass

    def fechar(self):
        self._vivo = False
        self._fechar_aplay()


def caminho_do_jabra(raiz="/sys/class/hidraw") -> str:
    """hidraw do Jabra pelo HID_NAME, não pelo número: em 25/10 um receptor Dell ligado na Pi ficou com o hidraw0
    e o programa não subia (PermissionError em loop)."""
    for d in sorted(glob.glob(os.path.join(raiz, "hidraw*"))):
        try:
            with open(os.path.join(d, "device", "uevent")) as f:
                if "HID_NAME=Jabra" in f.read():
                    return "/dev/" + os.path.basename(d)
        except OSError:
            continue
    raise RuntimeError("Jabra não encontrado em " + raiz)


class Anel:
    def __init__(self, caminho=None, bits=None, detector=None):
        self._fd = os.open(caminho or caminho_do_jabra(), os.O_WRONLY)
        self._bits = dict(BITS_DO_ANEL, **(bits or {}))
        self._detector = detector  # DetectorBotaoTelefone: o bit 0 do comando ecoa no relatório do botão

    def mostrar(self, nome: str):
        b = self._bits[nome]
        assert b != 0x05, "0x05 corta o microfone (proibido)"
        if self._detector:
            self._detector.comandou(b, time.monotonic())
        os.write(self._fd, bytes([3, b, 0]))


class Botao:
    """Botão de telefone: relatório HID 3, bit 0 (medido em 04/10). O detector separa toque real do eco das
    luzes (06/10: acender 03 01 gerava "toques" fantasmas em ciclo)."""
    def __init__(self, detector, caminho=None):
        self._fd = os.open(caminho or caminho_do_jabra(), os.O_RDONLY)
        self._detector = detector

    def esperar_toque(self) -> str:
        """Bloqueia até um toque e devolve a cor: "verde", "vermelho" ou "ambos" (alarme)."""
        while True:
            r = os.read(self._fd, 64)
            if len(r) >= 2 and r[0] == 3:
                print('{"hid_bruto": "%s"}' % r[:4].hex(" "), flush=True)  # 06/10: diagnóstico do botão verde
                cor = self._detector.relatorio(r[1], time.monotonic())
                if cor:
                    return cor


class Mixer:
    """Controle PCM do Jabra (0–11, uma posição por LED do anel; teste de 12/10). Card "USB" em /proc/asound/cards."""
    def __init__(self, card="USB", controle="PCM"):
        self.card, self.controle = card, controle

    def ler(self) -> int:
        r = subprocess.run(["amixer", "-c", self.card, "sget", self.controle], capture_output=True, text=True,
                           timeout=5)
        m = re.search(r"Playback (\d+) \[", r.stdout)
        if not m:
            raise RuntimeError(f"amixer sem a posição do {self.controle}")
        return int(m.group(1))

    def escrever(self, posicao: int):
        subprocess.run(["amixer", "-q", "-c", self.card, "sset", self.controle, str(int(posicao))], timeout=5)


class MusicMixer:
    """Controle `Music` (softvol do asound.conf) no card do Jabra: o ducking mexe só nele (spec 2026-10-21 §6.2).
    O controle só nasce no primeiro uso do PCM `music`; `prime` toca 0,1 s de silêncio para ele existir."""
    def __init__(self, card="USB", control="Music"):
        self.card, self.control = card, control

    def write(self, percent: int):
        subprocess.run(["amixer", "-q", "-c", self.card, "sset", self.control, f"{int(percent)}%"], timeout=5,
                       check=True)

    def prime(self):
        subprocess.run(["aplay", "-q", "-D", "music", "-t", "raw", "-f", "S16_LE", "-r", "48000", "-c", "2"],
                       input=b"\0" * (48000 // 10 * 4), timeout=5)


class ControleMudo:
    """Mudo por software: liga/desliga a captura do Jabra no mixer (controle "Headset", conferido na Pi em
    29/10: `Capture` não existe nesse card). Com a captura cortada o DetectorMudo vê silêncio e o maestro trata
    como o mudo de sempre. `executar` é injetável para os testes."""
    def __init__(self, card="USB", control="Headset", executar=subprocess.run):
        self.card, self.control, self._executar = card, control, executar

    def argv(self, mudo: bool) -> list:
        return ["amixer", "-q", "-c", self.card, "sset", self.control, "nocap" if mudo else "cap"]

    def aplicar(self, mudo: bool) -> bool:
        r = self._executar(self.argv(bool(mudo)), timeout=5)
        return getattr(r, "returncode", 0) == 0


KEY_VOLUMEDOWN, KEY_VOLUMEUP = 114, 115  # conferido em 12/10 no "Jabra SPEAK 410 USB Consumer Control"


def delta_da_tecla(tipo: int, codigo: int, valor: int):
    """+1/−1 para o APERTO (valor 1) das teclas de volume; None para o resto (soltar, mudo, sincronia)."""
    if tipo != 1 or valor != 1:
        return None
    return {KEY_VOLUMEUP: +1, KEY_VOLUMEDOWN: -1}.get(codigo)


def achar_teclado_do_jabra(base="/sys/class/input", nome="Jabra SPEAK 410 USB Consumer Control"):
    for p in sorted(glob.glob(f"{base}/event*/device/name")):
        with open(p) as f:
            if f.read().strip() == nome:
                return "/dev/input/" + p.split("/")[-3]
    return None


class TeclasVolume:
    """Lê os apertos de +/− do Jabra (o aparelho muda o volume sozinho e também avisa o computador)."""
    FORMATO = "llHHi"

    def __init__(self, caminho=None):
        caminho = caminho or achar_teclado_do_jabra()
        if not caminho:
            raise RuntimeError("teclado do Jabra não encontrado")
        self._f = open(caminho, "rb")
        self._tam = struct.calcsize(self.FORMATO)

    def esperar(self) -> int:
        while True:
            d = self._f.read(self._tam)
            if len(d) < self._tam:
                raise EOFError("teclado do Jabra sumiu")
            _, _, tipo, codigo, valor = struct.unpack(self.FORMATO, d)
            delta = delta_da_tecla(tipo, codigo, valor)
            if delta is not None:
                return delta
