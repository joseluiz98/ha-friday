"""Anel de LEDs: uma camada "estado -> aparência" e dois drivers (spec 2026-11-05 §11–§12).

Estados (contrato): off, listen (conversa; com `direction` mostra a direção), respond (ela respondendo na conversa),
think (cérebro), speak, dnd, alarm,
setup, updating, error, muted, net_error (erro persistente de rede), update_pending (update achado e não instalado).
Composição (spec 2026-11-16-ring-state-priority): o anel guarda CAMADAS (a luz do maestro, os comandos de fora, o erro
momentâneo, o update pendente) e desenha a de maior prioridade (`PRIORIDADE`); o MUDO é um indicador à parte, por cima
de qualquer base, nos dois aparelhos. `muted` como estado só existe na prévia (um estado por vez, sem composição).
O erro momentâneo (só XVF3800) dura ERROR_HOLD_S (10 s) quando vem de fora (`show("error")`) e FLASH_ERROR_S (2 s)
quando é a piscada do bipe de erro do programa (`flash_error`, spec 2026-11-21); no Jabra a piscada não faz nada.
O programa nunca cai por causa do anel: falha de driver vira NullRing/registro.
Módulo puro: usb/vendor só são importados dentro de `default_xvf_transport` / `default_usb_find`."""
import contextlib, io, math, os, sys, threading

ESTADOS = ("off", "listen", "respond", "think", "speak", "dnd", "alarm", "setup", "updating", "error", "muted", "net_error",
           "update_pending")

# o que os comandos de fora (agente: OTA, setup) põem na camada externa; o resto só como prévia (M7)
ESTADOS_DE_FORA = ("off", "setup", "updating", "error")

# a base de maior prioridade primeiro (spec 2026-11-16); o mudo não entra: é indicador por cima
PRIORIDADE = ("alarm", "updating", "error", "setup", "speak", "respond", "listen", "think", "net_error", "dnd", "update_pending",
              "off")

# nomes do maestro (hardware.BITS_DO_ANEL) -> estado
ESTADO_DO_MAESTRO = {"parada": "off", "ouvindo": "listen", "respondendo": "respond", "falando": "speak", "cerebro": "think", "nao_perturbe": "dnd", "alarme": "alarm",
                     "rede_esgotada": "net_error"}

# calibrar na chegada (18–27/11): valores de partida, não medidos
SPEED_FAST = 3
SPEED_SLOW = 1
SPEED_RESPOND = 2  # 18/11: pulso da resposta, como a Echo de 5ª geração (calibrar no MK2)
COR_RESPOSTA_FATOR = 0.7  # 18/11 (dono, visto no MK2): 30% mais fraca; o LED_BRIGHTNESS no efeito 1 mexe na velocidade
BRIGHTNESS = 255
LED_OFFSET = 5     # 17/11 (bancada): índice do LED 0 em relação a 0° do DOA (LED 0 às 7h, USB às 12h)
LED_SENTIDO = 1    # 17/11 (bancada): sentido horário
ERROR_HOLD_S = 10.0  # erro no XVF3800 é momentâneo: depois disso volta ao estado anterior
FLASH_ERROR_S = 2.0  # 21/11: piscada vermelha a cada bipe de erro do programa (flash_error); só o XVF
COR_BASE_CONVERSA = 0x000010
COR_PONTEIRO = 0x0000FF
COR_PONTEIRO_VIZINHO = 0x000060
COR_CONTAGEM = 0xFF8000  # laranja: não confunde com o vermelho do mudo nem do erro

COR_MUDO = 0xFF0000
MUTE_LEDS = (10, 11, 0)   # 17/11 (bancada, opção B do dono): LEDs vermelhos com o mudo (XVF3800)
DND_LOW = 0.25            # 17/11 (dono: roxo a 25%) não perturbe discreto: brilho baixo (cor escalada) a maior parte do tempo
DND_PULSE_EVERY_S = 30.0  # ... e um "relapso" de brilho cheio, respirando, de tempos em tempos
DND_PULSE_S = 2.0

VID_SEEED, PID_XVF3800 = 0x2886, 0x001A


def escalar(cor: int, f: float) -> int:
    r, g, b = (cor >> 16) & 0xFF, (cor >> 8) & 0xFF, cor & 0xFF
    return (round(r * f) << 16) | (round(g * f) << 8) | round(b * f)


# 18/11 (dono, visto no MK2): o pensando do XVF é um anel passando, como a Echo: base azul e um trecho ciano com rastro,
# 3/4 de volta por segundo (12 LEDs, 9 passos/s), as duas cores no brilho da luz de resposta
GIRO_PASSO_S = 1 / 9
COR_GIRO_BASE = escalar(0x0000FF, COR_RESPOSTA_FATOR)
# 23/11 (dono): luzes do sistema (update achado, instalando, setup) num laranja fraco; o amarelo fica para as
# notificações que virão. Instalando pulsa mais rápido que o update achado, e mais devagar que o alarme (laranja forte)
COR_SISTEMA_FATOR = 0.4
COR_SISTEMA = escalar(0xFF8000, COR_SISTEMA_FATOR)
SPEED_UPDATING = 2
COR_GIRO = (escalar(0x00C8FF, COR_RESPOSTA_FATOR), escalar(0x00C8FF, 0.4), escalar(0x00C8FF, 0.15))  # cabeça, meio, cauda


def quadro_giro(i: int) -> list:
    q = [COR_GIRO_BASE] * 12
    for k, cor in enumerate(COR_GIRO):
        q[(i - k) % 12] = cor
    return q


class RingState:
    """Camadas -> (base, mudo). Puro: `resolve()` é a única regra de quem aparece."""
    CAMADAS = ("maestro", "externo")

    def __init__(self):
        self._camadas = dict.fromkeys(self.CAMADAS)
        self.muted = False
        self.pendente = False

    def set_layer(self, nome: str, state) -> None:
        if state is not None and state not in ESTADOS:
            raise ValueError(f"estado de anel desconhecido: {state}")
        self._camadas[nome] = None if state in (None, "off") else state

    def set_muted(self, on: bool) -> None:
        self.muted = bool(on)

    def set_pending(self, on: bool) -> None:
        self.pendente = bool(on)

    def resolve(self) -> tuple:
        cand = [v for v in self._camadas.values() if v in PRIORIDADE]
        if self.pendente:
            cand.append("update_pending")
        return (min(cand, key=PRIORIDADE.index) if cand else "off"), self.muted


class _Composto:
    """Entrada comum dos drivers. `show` = comandos de fora (OTA, porta local); `show_maestro` = a luz da conversa;
    `preview` = um estado cru, sem composição (botão de prévia). Cada driver só implementa `_draw(base, mudo, dir)`."""
    ERRO_MOMENTANEO = False
    MUDO_NA_PREVIA = False

    def _init_composto(self):
        self._estado = RingState()
        self._direcao = None
        self._dir_fixa = None     # 17/11 (DOA v0): direção travada, desenhada na escuta enquanto a trava durar
        self._cru = None          # prévia / `muted` legado: desenhado como está
        self._iniciado = False    # nada desenha antes do primeiro show

    @property
    def pendente(self):
        return self._estado.pendente

    def _validar(self, state):
        if state not in ESTADOS:
            raise ValueError(f"estado de anel desconhecido: {state}")

    def show(self, state, direction=None):
        self._validar(state)
        with self._trava:
            if state != "error":
                self._fim_do_erro()
            if state == "muted":  # só a prévia pede `muted` como estado
                self._cru = "muted"
            else:
                self._cru = None
                if state == "error" and self.ERRO_MOMENTANEO:
                    self._comecar_erro()  # o erro toma o lugar do pedido de fora e devolve-o depois de 10 s
                self._estado.set_layer("externo", state)
                if state == "listen":
                    self._direcao = direction
            self._redesenhar()

    def flash_error(self, hold_s=FLASH_ERROR_S):
        """21/11 (spec error-light): o erro momentâneo por `hold_s` s, igual a `show("error")` mas curto; o programa
        chama a cada bipe de erro. No Jabra o `error` seria persistente e ele não tem vermelho fora do LED do mudo:
        não faz nada."""
        if not self.ERRO_MOMENTANEO:
            return
        with self._trava:
            self._cru = None
            self._comecar_erro(hold_s)
            self._estado.set_layer("externo", "error")
            self._redesenhar()

    def show_maestro(self, state, direction=None):
        self._validar(state)
        with self._trava:
            self._estado.set_layer("maestro", state)
            if state == "listen":
                self._direcao = direction
            self._cru = None  # uma luz da conversa encerra a prévia (prévia interrompida não manda o fim)
            self._redesenhar()

    def preview(self, state):
        with self._trava:
            if state is None:
                self._cru = None
            else:
                self._validar(state)
                self._fim_do_erro(devolver=True)  # o erro momentâneo não fica preso na camada
                self._cru = state
            self._redesenhar()

    def _mudar(self, fn):
        with self._trava:
            antes = self._estado.resolve()
            fn()
            if self._iniciado and self._cru is None and self._estado.resolve() != antes:
                self._redesenhar()

    def fixar_direcao(self, graus) -> None:
        """DOA v0 (17/11): ponteiro na direção travada durante a conversa; None apaga (em repouso o anel segue apagado)."""
        with self._trava:
            self._dir_fixa = graus
            if self._iniciado:
                self._redesenhar()

    def set_muted(self, on: bool) -> None:
        self._mudar(lambda: self._estado.set_muted(on))

    def set_pending(self, on: bool) -> None:
        self._mudar(lambda: self._estado.set_pending(on))

    def _redesenhar(self):
        self._iniciado = True
        if self._cru is not None:  # prévia: um estado cru; no Jabra o bit do mudo vai junto (sem ele, desmutaria)
            self._draw(self._cru, self.MUDO_NA_PREVIA and self._estado.muted,
                       self._direcao if self._cru == "listen" else None)
            return
        base, mudo = self._estado.resolve()
        direcao = self._direcao if self._direcao is not None else self._dir_fixa
        self._draw(base, mudo, direcao if base == "listen" else None)

    def _comecar_erro(self, hold=ERROR_HOLD_S):  # só no driver com erro momentâneo
        pass

    def _fim_do_erro(self, devolver=False):
        pass


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
# speak = recado do cérebro com o microfone fechado (M4): a luz do cérebro, não a de ouvindo
_JABRA = {"off": "parada", "listen": "ouvindo", "respond": "ouvindo", "speak": "cerebro", "think": "cerebro", "dnd": "nao_perturbe",
          "alarm": "alarme", "setup": "cerebro", "updating": "cerebro", "error": "alarme", "muted": "parada",
          "net_error": "alarme", "update_pending": "cerebro"}  # o Jabra não tem amarelo: update_pending = luz do setup


class JabraRing(_Composto):
    """Usa o `Anel` atual (sem cache: o comportamento tem de ser idêntico ao de antes, inclusive o detector do botão).
    Com mudo, todo relatório leva o bit do LED vermelho do próprio Jabra: uma luz nova nunca tira o mudo.
    A contagem usa os LEDs de volume (uma posição do PCM por LED): `posicao_fn(k)` escreve no mixer, sem gravar."""
    MUDO_NA_PREVIA = True
    def __init__(self, anel, posicao_fn=None):
        self._anel = anel
        self._posicao_fn = posicao_fn
        self._ultima = None
        self._trava = threading.RLock()
        self._init_composto()

    def countdown(self, fraction):
        k = math.ceil(max(0.0, min(1.0, fraction)) * 11)
        if self._posicao_fn is not None and k != self._ultima:
            self._ultima = k
            self._posicao_fn(k)

    def _draw(self, base, mudo, direction):
        if mudo:
            self._anel.mostrar(_JABRA[base], mudo=True)
        else:
            self._anel.mostrar(_JABRA[base])

    def reapply(self):
        self._ultima = None

    def close(self):
        pass


# estado -> (efeito, cor, velocidade). velocidade None = sem respiração (cor fixa)
_XVF = {"speak": (3, 0x0000FF, None), "respond": (1, escalar(0x00C8FF, COR_RESPOSTA_FATOR), SPEED_RESPOND), "think": (5, COR_GIRO_BASE, None), "dnd": (1, 0x8000FF, SPEED_SLOW),
        "alarm": (1, 0xFF8000, SPEED_FAST), "setup": (1, COR_SISTEMA, SPEED_SLOW), "updating": (1, COR_SISTEMA, SPEED_UPDATING),
        "error": (1, 0xFF0000, SPEED_SLOW), "muted": (3, 0xFF0000, None),
        "net_error": (1, 0xFF0000, SPEED_SLOW), "update_pending": (1, COR_SISTEMA, SPEED_SLOW)}


class Xvf3800Ring(_Composto):
    """Dono único do anel do XVF3800. `transport.write(nome, valores)` (o vendor.xvf_host ou um falso nos testes).
    Escreve a cor antes do efeito; não reenvia o mesmo quadro; falha de USB não levanta (reaplica no próximo show).
    Mudo: quadro parado (efeito 5) na cor da base com `MUTE_LEDS` em vermelho — os efeitos do firmware não sobrepõem
    LEDs isolados, então a animação congela enquanto durar o mudo (decisão de 16/11)."""
    ERRO_MOMENTANEO = True

    def __init__(self, transport, log=None, timer_factory=None):
        self._t = transport
        self._timer_factory = timer_factory or self._timer_padrao
        self._timer = None      # volta do erro
        self._volta = None      # pedido de fora anterior ao erro
        self._timer_dnd = None  # pulso do não perturbe
        self._timer_giro = None  # passo do anel do pensando
        self._giro_i = 0
        self._trava = threading.RLock()
        self._log = log or (lambda *a: print(*a, file=sys.stderr, flush=True))
        self._ultimo = None     # chave do quadro já aplicado com sucesso
        self._init_composto()

    @staticmethod
    def _timer_padrao(atraso, fn):
        t = threading.Timer(atraso, fn)
        t.daemon = True
        return t

    def _agendar(self, atraso, fn):
        t = self._timer_factory(atraso, fn)
        t.start()
        return t

    # ---- erro momentâneo
    def _comecar_erro(self, hold=ERROR_HOLD_S):
        if self._timer is not None:
            self._timer.cancel()  # erro repetido: a volta continua sendo a de antes do primeiro
        else:
            self._volta = self._estado._camadas["externo"]
        self._timer = self._agendar_proprio(hold, self._voltar, "_timer")

    def _fim_do_erro(self, devolver=False):
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
            if devolver:
                self._estado.set_layer("externo", self._volta)
        self._volta = None

    def _agendar_proprio(self, atraso, fn, campo):
        """O callback só age se o seu timer ainda for o atual em `campo` (um callback velho pode estar parado na
        trava enquanto o timer é trocado)."""
        caixa = []
        t = self._timer_factory(atraso, lambda: fn(caixa[0]))
        caixa.append(t)
        t.start()
        return t

    def _voltar(self, meu=None):
        with self._trava:
            if self._timer is None or (meu is not None and meu is not self._timer):
                return  # cancelado: um estado novo já ocupou o lugar
            self._timer = None
            self._estado.set_layer("externo", self._volta)
            self._volta = None
            if self._cru is None:
                self._redesenhar()

    # ---- não perturbe discreto
    def _parar_animacoes(self):
        if self._timer_dnd is not None:
            self._timer_dnd.cancel()
            self._timer_dnd = None
        if self._timer_giro is not None:
            self._timer_giro.cancel()
            self._timer_giro = None

    def _giro_vale(self):
        return self._cru == "think" or (self._cru is None and self._estado.resolve() == ("think", False))

    def _passo_giro(self, meu=None):
        with self._trava:
            if meu is not None and meu is not self._timer_giro:
                return
            self._timer_giro = None
            if not self._giro_vale():
                return
            self._giro_i += 1
            self._aplicar(("think", "giro"), [("LED_RING_COLOR", quadro_giro(self._giro_i))])
            self._timer_giro = self._agendar_proprio(GIRO_PASSO_S, self._passo_giro, "_timer_giro")

    def _dnd_vale(self):
        return self._cru is None and self._estado.resolve() == ("dnd", False)

    def _pulso(self, meu=None):
        with self._trava:
            if meu is not None and meu is not self._timer_dnd:
                return
            self._timer_dnd = None
            if not self._dnd_vale():
                return
            efeito, cor, vel = _XVF["dnd"]
            self._aplicar(("dnd", "pulso"), [("LED_COLOR", [cor]), ("LED_SPEED", [vel]),
                                             ("LED_BRIGHTNESS", [BRIGHTNESS]), ("LED_EFFECT", [1])])
            self._timer_dnd = self._agendar_proprio(DND_PULSE_S, self._baixo, "_timer_dnd")

    def _baixo(self, meu=None):
        with self._trava:
            if meu is not None and meu is not self._timer_dnd:
                return
            self._timer_dnd = None
            if not self._dnd_vale():
                return
            self._aplicar(("dnd", "baixo"), [("LED_COLOR", [escalar(_XVF["dnd"][1], DND_LOW)]), ("LED_EFFECT", [3])])
            self._timer_dnd = self._agendar_proprio(DND_PULSE_EVERY_S, self._pulso, "_timer_dnd")

    # ---- desenho
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

    @staticmethod
    def _cor_da_base(base):
        if base == "off":
            return 0
        if base == "listen":
            return COR_PONTEIRO
        if base == "dnd":  # M9: o mesmo brilho baixo do não perturbe sem mudo
            return escalar(_XVF["dnd"][1], DND_LOW)
        return _XVF[base][1]

    def _draw(self, base, mudo, direction):
        if mudo:
            self._parar_animacoes()
            q = [self._cor_da_base(base)] * 12
            for i in MUTE_LEDS:
                q[i % 12] = COR_MUDO
            self._aplicar(("mudo", tuple(q)), [("LED_RING_COLOR", q), ("LED_EFFECT", [5])])
            return
        if base == "dnd" and self._cru is None:
            if self._ultimo in (("dnd", "baixo"), ("dnd", "pulso")) and self._timer_dnd is not None:
                return  # já no ciclo do não perturbe
            self._parar_animacoes()
            self._ultimo = None if self._ultimo == ("dnd", "pulso") else self._ultimo
            self._aplicar(("dnd", "baixo"), [("LED_COLOR", [escalar(_XVF["dnd"][1], DND_LOW)]), ("LED_EFFECT", [3])])
            self._timer_dnd = self._agendar_proprio(DND_PULSE_EVERY_S, self._pulso, "_timer_dnd")
            return
        if base == "think" and self._ultimo == ("think", "giro") and self._timer_giro is not None:
            return  # já girando
        self._parar_animacoes()
        if base == "think":
            self._giro_i = 0
            self._aplicar(("think", "giro"), [("LED_RING_COLOR", quadro_giro(0)), ("LED_EFFECT", [5])])
            self._timer_giro = self._agendar_proprio(GIRO_PASSO_S, self._passo_giro, "_timer_giro")
            return
        chave = (base, None if base != "listen" or direction is None else tuple(self._quadro(direction)))
        if chave == self._ultimo:
            return
        self._aplicar(chave, self._sequencia(base, direction))

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
                self._parar_animacoes()
                self._aplicar(chave, [("LED_RING_COLOR", [COR_CONTAGEM] * n + [0] * (12 - n)), ("LED_EFFECT", [5])])

    def reapply(self):
        """Depois de uma reconexão USB o anel volta ao padrão de fábrica (e quem encerra a contagem): redesenha."""
        with self._trava:
            if not self._iniciado:
                return
            self._ultimo = None
            self._parar_animacoes()
            self._redesenhar()

    def close(self):
        with self._trava:
            self._fim_do_erro()
            self._parar_animacoes()
            self._init_composto()
            self._ultimo = None
        try:
            self._t.write("LED_EFFECT", [0])
        except Exception as e:
            self._log(f"anel xvf3800: falha ao apagar ({type(e).__name__}: {e})")


class NullRing(_Composto):
    def __init__(self):
        self._trava = threading.RLock()
        self._init_composto()

    def countdown(self, fraction):
        pass

    def _draw(self, base, mudo, direction):
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
