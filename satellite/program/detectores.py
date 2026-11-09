"""Detectores puros do satélite: transformam pontuações e áudio em eventos (testes: test_detectores.py)."""
import array
import math
import threading


class DetectorPalavra:
    """Dispara uma vez ao cruzar o limiar; só rearma quando a pontuação cai abaixo de 0.2.
    Porta de voz (06/10): só vale com `voz` (maior prob. do VAD nos últimos ~0,5 s) >= `limiar_voz`. Medido:
    19 "Friday" reais com voz >= 0,81; 11 disparos falsos com música da Echo com voz <= 0,25 (um com nota 0,99).
    Sem voz o detector segue armado. `limiar_voz=0` desliga a porta.
    Quase-ativação (spec 2026-10-15 D8): um episódio que passou de `limiar_quase` e terminou (nota < 0,2) sem
    disparar fica guardado com a nota e a voz máximas; `pegar_quase()` o devolve uma vez.
    Sussurro (spec 2026-10-20 D2): com `razao` (2-6 kHz / 100-600 Hz, dB) >= `limiar_sussurro` a palavra vale mesmo
    com voz baixa, e `ultimo_sussurro` diz que a ativação foi sussurrada. `limiar_sussurro=None` desliga.
    Paciência (02/11/2026): só dispara depois de `paciencia` quadros SEGUIDOS que passariam sozinhos (nota, voz ou
    sussurro); 1 = o detector de sempre. Medido offline na Sexta: 2 tirou ~60% dos disparos falsos da sombra."""
    def __init__(self, limiar: float, limiar_voz: float = 0.0, limiar_quase: float = 0.3, limiar_sussurro=None,
                 paciencia: int = 1):
        self.limiar, self.limiar_voz, self.limiar_quase, self._armado = limiar, limiar_voz, limiar_quase, True
        self.paciencia, self._seguidos = paciencia, 0
        self.limiar_sussurro, self.ultimo_sussurro = limiar_sussurro, False
        self._episodio, self._quase = None, None

    @property
    def armado(self) -> bool:
        return self._armado

    def alimentar(self, pontuacao: float, voz: float = 1.0, razao=None) -> bool:
        if pontuacao >= self.limiar_quase:
            ep = self._episodio or {"nota": 0.0, "voz": 0.0, "disparou": False}
            ep["nota"], ep["voz"] = max(ep["nota"], pontuacao), max(ep["voz"], voz)
            self._episodio = ep
        sussurro = self.limiar_sussurro is not None and razao is not None and razao >= self.limiar_sussurro
        passa = pontuacao >= self.limiar and (voz >= self.limiar_voz or sussurro)
        self._seguidos = self._seguidos + 1 if passa else 0
        if self._armado and passa and self._seguidos >= self.paciencia:
            self._armado = False
            self.ultimo_sussurro = sussurro
            if self._episodio:
                self._episodio["disparou"] = True
            return True
        if pontuacao < 0.2:
            self._armado = True
            ep, self._episodio = self._episodio, None
            if ep and not ep["disparou"]:
                self._quase = {"nota": round(ep["nota"], 2), "voz": round(ep["voz"], 2)}
        return False

    def pegar_quase(self):
        q, self._quase = self._quase, None
        return q


def escolher_acao(modo: str, disparou: bool, ja_houve_evento: bool):
    """spec 2026-10-24: o que fazer quando uma palavra dispara. 'evento' acorda (uma vez por quadro, mesmo com
    duas palavras ativas); 'sombra' só registra; 'desligada' nunca chega aqui com efeito."""
    if modo == "ativa" and disparou and not ja_houve_evento:
        return "evento"
    if modo == "sombra" and disparou:
        return "sombra"
    return None


class EpisodioVerificador:
    """20/10: o openWakeWord TROCA a nota base (>= limiar do verificador) pela nota do verificador, e a base some;
    um "Friday" que o verificador rejeita cai para perto de zero e não deixava rastro. Um episódio = quadros seguidos
    em que o verificador foi consultado; se termina sem disparar, devolve uma vez a nota máxima dele e a voz máxima.
    Um episódio barrado com nota >= 0,3 também aparece como quase-ativação (são o mesmo "Friday")."""
    def __init__(self):
        self._ep = None

    def alimentar(self, consultado: bool, nota: float, voz: float, disparou: bool):
        if consultado:
            ep = self._ep or {"nota": 0.0, "voz": 0.0, "disparou": False}
            ep["nota"], ep["voz"] = max(ep["nota"], nota), max(ep["voz"], voz)
            ep["disparou"] = ep["disparou"] or disparou
            self._ep = ep
            return None
        ep, self._ep = self._ep, None
        if ep and not ep["disparou"]:
            return {"nota": round(ep["nota"], 2), "voz": round(ep["voz"], 2)}
        return None


class PicoDaFala:
    """20/10 (instrumentação): a nota que o modelo base deu a cada trecho de fala, mesmo abaixo do "quase" (0,3).
    Mostra quanto tirou um "Friday" que não ativou. Quadro com o verificador consultado não entra em `pico_base`
    (o openWakeWord troca a nota base pela dele; consultado = base >= limiar_verificador). `pont=None` = inferência
    pulada (microfone aberto): não conta; trecho sem nenhum quadro julgado não emite."""
    def __init__(self):
        self._t = None

    def alimentar(self, evento, pont, consultado, nota_verificador, voz, disparou, extras=None):
        """`extras` (spec 2026-10-24): {nome: nota} das palavras extras não desligadas neste quadro."""
        if evento == "fala_comecou":
            self._t = {"pico_base": 0.0, "verificador_consultas": 0, "nota_verificador": 0.0, "voz": 0.0,
                       "quadros": 0, "julgados": 0, "ativou": False, "picos_extras": {}}
        t = self._t
        if t is None:
            return None
        if evento == "fala_parou":  # o quadro que fecha o trecho já é silêncio
            self._t = None
            if not t["julgados"]:
                return None
            r = {"pico_base": round(t["pico_base"], 2), "verificador_consultas": t["verificador_consultas"],
                 "nota_verificador": round(t["nota_verificador"], 2), "voz": round(t["voz"], 2),
                 "duracao_s": round(t["quadros"] * 0.08, 2), "ativou": t["ativou"]}
            if t["picos_extras"]:
                r["picos_extras"] = {n: round(v, 2) for n, v in t["picos_extras"].items()}
            return r
        t["quadros"] += 1
        if pont is not None:
            t["julgados"] += 1
            t["voz"] = max(t["voz"], voz)
            t["ativou"] = t["ativou"] or bool(disparou)
            for nome, v in (extras or {}).items():
                t["picos_extras"][nome] = max(t["picos_extras"].get(nome, 0.0), float(v))
            if consultado:
                t["verificador_consultas"] += 1
                t["nota_verificador"] = max(t["nota_verificador"], nota_verificador)
            else:
                t["pico_base"] = max(t["pico_base"], pont)
        return None


class DetectorFala:
    """VAD com histerese, em pedaços de 80 ms: começa após `comeca_apos` pedaços de fala e para após
    `para_apos` pedaços sem fala (padrão: 160 ms e 800 ms)."""
    def __init__(self, limiar=0.5, comeca_apos=2, para_apos=10):
        self.limiar, self.comeca_apos, self.para_apos = limiar, comeca_apos, para_apos
        self.falando, self._seq = False, 0

    def iniciar_falando(self):
        """Conversa sussurrada (spec 2026-10-20 §4.2): a fala já está em andamento; só o fim é emitido."""
        self.falando, self._seq = True, 0

    def alimentar(self, prob: float):
        e_fala = prob >= self.limiar
        if e_fala != self.falando:
            self._seq += 1
            if self._seq >= (self.comeca_apos if e_fala else self.para_apos):
                self.falando, self._seq = e_fala, 0
                return "fala_comecou" if e_fala else "fala_parou"
        else:
            self._seq = 0
        return None


class DetectorMudo:
    """Mudo físico do Jabra = zeros digitais exatos (medido em 04/10). Entra após `pedacos` pedaços só de
    zeros (6 × 80 ms ≈ 0,5 s) e sai no primeiro pedaço com qualquer amostra diferente de zero.
    `desconhecido=True` (partida do programa, revisão final C1 de 08/10): começa em None e decide no primeiro
    sinal — False no primeiro pedaço com som, True após `pedacos` de zeros."""
    def __init__(self, pedacos=6, desconhecido=False):
        self.pedacos, self.mudo, self._zeros = pedacos, (None if desconhecido else False), 0

    def alimentar(self, pcm: bytes):
        if pcm.count(0) == len(pcm):
            self._zeros += 1
            if not self.mudo and self._zeros >= self.pedacos:
                self.mudo = True
                return True
            return None
        self._zeros = 0
        if self.mudo is not False:  # saindo do mudo, ou partida decidida
            self.mudo = False
            return False
        return None


class DetectorBotaoTelefone:
    """Botões de telefone do Jabra 410 (relatório HID 3, byte 1). O byte espelha o estado de telefone:
    bit 0x01 = fora do gancho, 0x10 = sinal de toque ("flash"). Devolve a COR do botão: "verde", "vermelho",
    "ambos" (alarme) ou None. Tabela medida em 06/10 e na bancada de 04/11 (spec 2026-11-04):
      luz comandada                       | verde                      | vermelho
      0x00 repouso                        | relatório com bit 0x01     | 0x00
      bit 0x01 (0x11 ouvindo, 0x01 cérebro,
        0x41 não perturbe)                | relatório com bit 0x10     | 0x00
      0x10 alarme                         | "ambos" (os dois mandam 10, 00: não se distinguem)
    A luz "ouvindo" é 0x11 desde 04/11 (visual idêntica à antiga 0x10, mas com o bit 0 o vermelho é distinguível).
    As luzes ecoam (entrar em 01 → 01; sair de 01 → 00; trocar a luz devolve um 00 igual ao do vermelho): ignorar
    `eco_s` após cada comando. `eco_s=1.2` (revisão final M9, 09/10): 0,6 s era apertado — com a CPU ocupada o eco
    pode chegar depois da janela e virar um toque falso, cortando a conversa recém-aberta; toques reais não
    acontecem a <1,2 s de uma troca de luz (que só ocorre em transição de estado, não em rajada).
    Um toque por `toque_s`."""
    def __init__(self, eco_s=1.2, toque_s=1.0):
        self.eco_s, self.toque_s = eco_s, toque_s
        self._comandado, self._t_comando, self._t_toque = 0x00, -1e9, -1e9

    def comandou(self, byte: int, agora: float):
        self._comandado, self._t_comando = byte, agora

    def relatorio(self, byte: int, agora: float):
        if agora - self._t_comando < self.eco_s or agora - self._t_toque < self.toque_s:
            return None
        if self._comandado == 0x10:
            cor = "ambos"
        elif self._comandado & 0x01:  # 0x11/0x01/0x41 (medido 08/10 e 04/11): o verde liga o 0x10
            cor = "verde" if byte & 0x10 else ("vermelho" if byte == 0x00 else None)
        else:
            cor = "verde" if byte & 0x01 else ("vermelho" if byte == 0x00 else None)
        if cor:
            self._t_toque = agora
        return cor


class RejeicaoEpisodio:
    """Spec 2026-11-03 (detalhes da ativação): descobre a CAUSA real de um "Friday" que não ativou. Um episódio abre num
    quadro com nota >= limiar (ou com o verificador consultado) e fecha num quadro sem consulta e nota < 0,2; o quadro
    de fechamento é avaliado antes. Se nenhum quadro disparou, devolve uma vez: causa "paciencia" (nota e voz/sussurro
    passaram, faltou persistência), "sem_voz" (nota passou, voz não) ou "verificador" (a nota base caiu antes do
    limiar, o verificador rejeitou). Guarda o nome da PRIMEIRA gravação anotada no episódio. `quadros` (09/11) = quantos
    quadros o episódio durou, incluindo o de fechamento."""
    def __init__(self):
        self._ep = None

    def anotar_gravacao(self, nome):
        if self._ep is not None and self._ep["gravacao"] is None and nome is not None:
            self._ep["gravacao"] = nome

    def alimentar(self, nota, voz, limiar, limiar_voz, disparou, consultado=False, nota_verificador=None,
                  sussurro=False, razao=None):
        if self._ep is None and (nota >= limiar or consultado):
            self._ep = {"nota": 0.0, "voz": 0.0, "nv": None, "razao": None, "disparou": False,
                        "passou_nota": False, "passou_tudo": False, "gravacao": None, "quadros": 0}
        ep = self._ep
        if ep is None:
            return None
        ep["nota"], ep["voz"] = max(ep["nota"], nota), max(ep["voz"], voz)
        ep["quadros"] += 1
        if nota_verificador is not None:
            ep["nv"] = nota_verificador if ep["nv"] is None else max(ep["nv"], nota_verificador)
        if razao is not None:
            ep["razao"] = razao if ep["razao"] is None else max(ep["razao"], razao)
        ep["disparou"] = ep["disparou"] or bool(disparou)
        if nota >= limiar:
            ep["passou_nota"] = True
            if voz >= limiar_voz or sussurro:
                ep["passou_tudo"] = True
        if consultado or nota >= 0.2:
            return None
        self._ep = None
        if ep["disparou"]:
            return None
        causa = "paciencia" if ep["passou_tudo"] else ("sem_voz" if ep["passou_nota"] else "verificador")
        return {"causa": causa, "nota": round(ep["nota"], 2), "voz": round(ep["voz"], 2),
                "nota_verificador": None if ep["nv"] is None else round(ep["nv"], 2),
                "razao_sussurro_db": None if ep["razao"] is None else round(ep["razao"], 1),
                "gravacao": ep["gravacao"], "quadros": ep["quadros"]}


def trecho_do_episodio(contexto, quadros, antes=25):
    """09/11: o áudio de um episódio que só se resolve no fim (rejeição por paciência): os `quadros` do episódio mais
    `antes` pedaços de folga (~2 s, a palavra começa antes da nota subir), tirados do fim do contexto."""
    return list(contexto)[-(quadros + antes):]


# ---- spec 2026-11-04: portão de eco (a fala dela voltando pelo microfone), desligado por padrão
def rms_dbfs(pcm: bytes) -> float:
    """Nível RMS de PCM int16 em dBFS (0 = fundo de escala); -120.0 para silêncio digital."""
    a = array.array("h")
    a.frombytes(pcm[:len(pcm) - (len(pcm) % 2)])
    if not a:
        return -120.0
    soma = sum(x * x for x in a)
    if soma == 0:
        return -120.0
    return 20 * math.log10(math.sqrt(soma / len(a)) / 32768.0)


def portao_eco(pcm: bytes, ela_falando: bool, limiar_db) -> bytes:
    """Com ela falando, só deixa passar o que está bem acima do eco esperado (interrupção real). Sem limiar (None) ou
    com ela calada, o áudio passa inalterado; abaixo do limiar vira silêncio do mesmo tamanho."""
    if limiar_db is None or not ela_falando or rms_dbfs(pcm) >= limiar_db:
        return pcm
    return bytes(len(pcm))


class MedidorEco:
    """Mede (em dBFS) o microfone enquanto ela fala, para escolher o limiar do portão. `resumo()` zera."""
    def __init__(self):
        self._niveis, self._trava = [], threading.Lock()

    def alimentar(self, pcm: bytes):
        n = rms_dbfs(pcm)
        with self._trava:
            self._niveis.append(n)

    def resumo(self):
        with self._trava:
            niveis, self._niveis = sorted(self._niveis), []
        if not niveis:
            return None
        pos = lambda p: niveis[min(len(niveis) - 1, int(p * len(niveis)))]
        return {"p50": round(pos(0.5), 1), "p90": round(pos(0.9), 1), "max": round(niveis[-1], 1), "n": len(niveis)}
