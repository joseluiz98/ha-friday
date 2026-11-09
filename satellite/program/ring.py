"""Anel de LEDs: uma camada "estado -> aparência" e dois drivers (spec 2026-11-05 §11–§12).

Estados (contrato): off, listen (conversa; com `direction` mostra a direção), think (cérebro), speak, dnd, alarm,
setup, updating, error, muted, net_error (erro persistente de rede). O programa nunca cai por causa do anel: falha de driver vira NullRing/registro.
Módulo puro: usb/vendor só são importados dentro de `default_xvf_transport` / `default_usb_find`."""
import contextlib, io, math, os, sys, threading

ESTADOS = ("off", "listen", "think", "speak", "dnd", "alarm", "setup", "updating", "error", "muted", "net_error")

# nomes do maestro (hardware.BITS_DO_ANEL) -> estado
ESTADO_DO_MAESTRO = {"parada": "off", "ouvindo": "listen", "cerebro": "think", "nao_perturbe": "dnd", "alarme": "alarm",
                     "rede_esgotada": "net_error"}

# calibrar na chegada (18–27/11): valores de partida, não medidos
SPEED_FAST = 3
SPEED_SLOW = 1
BRIGHTNESS = 255
LED_OFFSET = 0     # calibrar na chegada: índice do LED 0 em relação a 0° do DOA_VALUE
LED_SENTIDO = 1    # calibrar na chegada: +1 / -1
ERROR_HOLD_S = 10.0  # erro no XVF3800 é momentâneo: depois disso volta ao estado anterior
COR_BASE_CONVERSA = 0x000010
COR_PONTEIRO = 0x0000FF
COR_PONTEIRO_VIZINHO = 0x000060
COR_CONTAGEM = 0xFF8000  # laranja: não confunde com o vermelho do mudo nem do erro

VID_SEEED, PID_XVF3800 = 0x2886, 0x001A


class RingDriver:
    """Protocolo informal dos drivers."""
    def show(self, state: str, direction=None) -> None: ...
    def countdown(self, fraction: float) -> None: ...  # 1.0 cheio -> 0.0 vazio; quem encerra chama reapply
    def close(self) -> None: ...


class Contagem:
    """Contagem regressiva visível (confirmação do reset de fábrica, 06/11). Só tempo: quem desenha é o driver."""
    def __init__(self):
        self._inicio = self._duracao = None

    def iniciar(self, agora, atraso, duracao):
        self._inicio, self._duracao = agora + atraso, duracao

    def cancelar(self) -> bool:
        ativa, self._inicio = self._inicio is not None, None
        return ativa

    def tique(self, agora):
        """None = nada a desenhar; senão a fração restante (0.0 é o último quadro, e a contagem termina)."""
        if self._inicio is None or agora < self._inicio:
            return None
        resta = 1.0 - (agora - self._inicio) / self._duracao
        if resta <= 0:
            self._inicio = None
            return 0.0
        return resta


# Jabra: estado -> nome do relatório HID de hoje (hardware.BITS_DO_ANEL)
_JABRA = {"off": "parada", "listen": "ouvindo", "speak": "ouvindo", "think": "cerebro", "dnd": "nao_perturbe",
          "alarm": "alarme", "setup": "cerebro", "updating": "cerebro", "error": "alarme", "muted": "parada",
          "net_error": "alarme"}


class JabraRing:
    """Usa o `Anel` atual (sem cache: o comportamento tem de ser idêntico ao de antes, inclusive o detector do botão).
    A contagem usa os LEDs de volume (uma posição do PCM por LED): `posicao_fn(k)` escreve no mixer, sem gravar."""
    def __init__(self, anel, posicao_fn=None):
        self._anel = anel
        self._posicao_fn = posicao_fn
        self._ultima = None

    def countdown(self, fraction):
        k = math.ceil(max(0.0, min(1.0, fraction)) * 11)
        if self._posicao_fn is not None and k != self._ultima:
            self._ultima = k
            self._posicao_fn(k)

    def show(self, state, direction=None):
        if state not in _JABRA:
            raise ValueError(f"estado de anel desconhecido: {state}")
        self._anel.mostrar(_JABRA[state])

    def reapply(self):
        self._ultima = None

    def close(self):
        pass


# estado -> (efeito, cor, velocidade). velocidade None = sem respiração (cor fixa)
_XVF = {"speak": (3, 0x0000FF, None), "think": (1, 0x0000FF, SPEED_FAST), "dnd": (1, 0x8000FF, SPEED_SLOW),
        "alarm": (1, 0xFF8000, SPEED_FAST), "setup": (1, 0xFF8000, SPEED_SLOW), "updating": (1, 0xFFFFFF, SPEED_SLOW),
        "error": (1, 0xFF0000, SPEED_SLOW), "muted": (3, 0xFF0000, None),
        "net_error": (1, 0xFF0000, SPEED_SLOW)}


class Xvf3800Ring:
    """Dono único do anel do XVF3800. `transport.write(nome, valores)` (o vendor.xvf_host ou um falso nos testes).
    Escreve a cor antes do efeito; não reenvia o mesmo estado; falha de USB não levanta (reaplica no próximo show)."""
    def __init__(self, transport, log=None, timer_factory=None):
        self._t = transport
        self._timer_factory = timer_factory or self._timer_padrao
        self._timer = None
        self._volta = None    # pedido anterior ao erro, para onde o anel retorna
        self._trava = threading.RLock()
        self._log = log or (lambda *a: print(*a, file=sys.stderr, flush=True))
        self._ultimo = None   # (state, quadro_ou_None) já aplicado com sucesso
        self._pedido = None   # último pedido (state, direction), para reapply

    @staticmethod
    def _timer_padrao(atraso, fn):
        t = threading.Timer(atraso, fn)
        t.daemon = True
        return t

    def _cancelar_volta(self):
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _voltar(self):
        with self._trava:
            self._timer = None
            if self._pedido is None or self._pedido[0] != "error":
                return
            antes, self._volta = self._volta, None
            self.show(*(antes or ("off", None)))

    @staticmethod
    def _quadro(direction):
        centro = (LED_OFFSET + LED_SENTIDO * round((direction % 360) / 30)) % 12
        q = [COR_BASE_CONVERSA] * 12
        q[(centro - 1) % 12] = q[(centro + 1) % 12] = COR_PONTEIRO_VIZINHO
        q[centro] = COR_PONTEIRO
        return q

    def _sequencia(self, state, direction):
        if state == "off":
            return [("LED_EFFECT", [0])]
        if state == "listen":
            if direction is None:
                return [("LED_DOA_COLOR", [COR_BASE_CONVERSA, COR_PONTEIRO]), ("LED_EFFECT", [4])]
            return [("LED_RING_COLOR", self._quadro(direction)), ("LED_EFFECT", [5])]
        efeito, cor, vel = _XVF[state]
        seq = [("LED_COLOR", [cor])]
        if efeito == 1:
            seq += [("LED_SPEED", [vel]), ("LED_BRIGHTNESS", [BRIGHTNESS])]
        return seq + [("LED_EFFECT", [efeito])]

    def show(self, state, direction=None):
        if state not in ESTADOS:
            raise ValueError(f"estado de anel desconhecido: {state}")
        with self._trava:
            self._show(state, direction)

    def _show(self, state, direction):
        self._cancelar_volta()
        if state == "error":
            if self._pedido is not None and self._pedido[0] != "error":
                self._volta = self._pedido
            elif self._pedido is None:
                self._volta = None
            self._timer = self._timer_factory(ERROR_HOLD_S, self._voltar)
            self._timer.start()
        else:
            self._volta = None
        self._pedido = (state, direction)
        chave = (state, None if state != "listen" or direction is None else tuple(self._quadro(direction)))
        if chave == self._ultimo:
            return
        self._aplicar(chave, self._sequencia(state, direction))

    def _aplicar(self, chave, seq):
        try:
            for nome, valores in seq:
                self._t.write(nome, valores)
            self._ultimo = chave
        except Exception as e:
            self._ultimo = None
            self._log(f"anel xvf3800: falha ao escrever ({type(e).__name__}: {e})")

    def countdown(self, fraction):
        n = math.ceil(max(0.0, min(1.0, fraction)) * 12)
        with self._trava:
            chave = ("countdown", n)
            if chave != self._ultimo:
                self._aplicar(chave, [("LED_RING_COLOR", [COR_CONTAGEM] * n + [0] * (12 - n)), ("LED_EFFECT", [5])])

    def reapply(self):
        """Depois de uma reconexão USB o anel volta ao padrão de fábrica: reenvia o último pedido."""
        if self._pedido is None:
            return
        self._ultimo = None
        self.show(*self._pedido)

    def close(self):
        with self._trava:
            self._cancelar_volta()
            self._volta = None
            self._pedido = None
            self._ultimo = None
        try:
            self._t.write("LED_EFFECT", [0])
        except Exception as e:
            self._log(f"anel xvf3800: falha ao apagar ({type(e).__name__}: {e})")


class NullRing:
    def countdown(self, fraction):
        pass

    def show(self, state, direction=None):
        pass

    def reapply(self):
        pass

    def close(self):
        pass


def default_usb_find(**kw):
    import usb.core  # só na Pi
    return usb.core.find(**kw)


def detect_ring(usb_find=None) -> str:
    """"xvf3800" se houver USB 2886:001a, senão "jabra". Erro de USB (sem libusb) = "jabra"."""
    try:
        achou = (usb_find or default_usb_find)(idVendor=VID_SEEED, idProduct=PID_XVF3800)
    except Exception:
        return "jabra"
    return "xvf3800" if achou else "jabra"


class _XvfTransport:
    """Adapta o `vendor/xvf_host.py` (sem editá-lo): timeout curto e sem os prints dele."""
    def __init__(self, dev):
        self._dev = dev
        dev.TIMEOUT = 1000

    def write(self, nome, valores):
        with contextlib.redirect_stdout(io.StringIO()):
            self._dev.write(nome, list(valores))


def default_xvf_transport():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
    try:
        import xvf_host  # vendor: pyusb + libusb_package
    finally:
        sys.path.pop(0)
    dev = xvf_host.find(VID_SEEED, PID_XVF3800)
    if dev is None:
        raise RuntimeError("XVF3800 não encontrado")
    return _XvfTransport(dev)


def make_ring(jabra_factory, xvf_factory=None, usb_find=None, log=None, posicao_fn=None):
    """Cria o driver certo. Falha do XVF cai no Jabra (se houver); sem nenhum, NullRing."""
    log = log or (lambda *a: print(*a, file=sys.stderr, flush=True))
    if detect_ring(usb_find) == "xvf3800":
        try:
            return Xvf3800Ring((xvf_factory or default_xvf_transport)(), log=log)
        except Exception as e:
            log(f"anel: XVF3800 indisponível ({type(e).__name__}: {e}); tentando o Jabra")
    try:
        return JabraRing(jabra_factory(), posicao_fn)
    except Exception as e:
        log(f"anel: sem anel ({type(e).__name__}: {e})")
        return NullRing()
