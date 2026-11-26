"""Maestro do satélite (spec 2026-10-05 §1): decide estados, sons, luz e microfone.

Lógica pura: recebe eventos com o horário (segundos, relógio monotônico) e devolve ações. Não toca em
hardware nem em rede, então é testado sem o Jabra (test_maestro.py).
"""
import unicodedata
from dataclasses import dataclass

PARADA, CONECTANDO, OUVINDO, CORTADA, AGUARDANDO = "parada", "conectando", "ouvindo", "cortada", "aguardando"
ALARME = "alarme"  # spec 2026-10-12 §2: timer vencido tocando
CADASTRO = "cadastro"  # spec 2026-10-15 D15–D20: a Pi conduz a gravação do programa de aprendizagem de voz
TESTE_PALAVRA = "teste_palavra"  # spec 2026-11-05 §6.4: a Pi confere a palavra de ativação (duas vezes) na instalação
ANUNCIO = "anuncio"  # spec 2026-11-20: anúncio da casa (campainha + fala) tocando; só a palavra ativa e o botão cortam
ABERTO = (CONECTANDO, OUVINDO)
TESTE_SEM_PALAVRA_S = 15.0  # sem nenhuma palavra por tanto tempo: "o microfone está mudo?" (uma vez)
RECADO_PAUSA_S = 1.5  # v0.3.7: pausa dentro do recado (bot-stopped/started na mesma resposta)


@dataclass
class Ajustes:
    espera_inicial_s: float = 4.0
    continuacao_s: float = 8.0
    resposta_max_s: float = 20.0
    # Spec 2026-10-15 D1: depois da fala humana, quanto ela tem para começar a responder numa conversa aberta pela
    # palavra ou pelo botão. O Gemini 3.8 tem "áudio proativo": quando decide não responder, não manda nada.
    espera_resposta_s: float = 8.0
    # M10 (revisão 08/10): tolerância para a fala dela COMEÇAR depois do cérebro devolver "0" pendentes.
    # Reaproveitava continuacao_s (tolerância para a fala CONTINUAR em uma conversa já aberta) por acaso
    # os dois terem o mesmo valor hoje — encurtar um pelo outro motivo também encurtava este sem querer.
    cerebro_fim_s: float = 8.0
    # Spec 2026-11-24: o timer só toca. `volta_alarme_s` = duração de uma volta do som do alarme (a Pi põe a do WAV);
    # `rampa_s` = alarm_ramp_s; o aviso falado do timer vem depois da rampa mais `voltas_antes_do_nome` voltas.
    # Tempo máximo tocando; quanto uma entrega pode esperar na fila (o bot desiste em ~90 s).
    volta_alarme_s: float = 1.65
    rampa_s: float = 0.0
    voltas_antes_do_nome: int = 3
    alarme_max_s: float = 300.0
    fila_entrega_max_s: float = 90.0
    anuncio_max_s: float = 300.0  # spec 2026-11-20: quanto um anúncio pode esperar (contado do pedido ao bot, F6)
    # Spec 2026-10-21 R13: fala contínua (sem pausa) por mais que isto deixa de contar como "alguém falando" e a
    # conversa fecha (`fala_longa`). Música cantada que o VAD confunde com voz não segura o microfone aberto.
    speech_max_s: float = 15.0  # 10/11: 0 desliga o teto (ditado longo no MK1), com ou sem música
    # Spec 2026-10-21 R11: quanto esperar a música do alarme começar antes de seguir como um alarme normal
    music_wait_s: float = 10.0
    # 04/11 (teste do dono): fecha o microfone quando a pessoa para de falar e reabre quando ela começa a responder,
    # como a Alexa; a fala que vier nesse intervalo (conversa da sala) não estica a espera curta
    close_mic_awaiting_reply: bool = False
    # 10/11 (MK1, ditado do lembrete): com o cérebro pendente, o prazo que vence com a pessoa falando espera a próxima
    # pausa em vez de fechar o microfone. False = como antes (fechava no meio da fala)
    keep_listening_while_speaking: bool = True
    # 04/11: sem continuação com música tocando (a Pi consulta o Sendspin quando ela começa a responder) nem depois de
    # um turno só de comando da casa (o bot avisa com `encerrar comando_casa`)
    no_follow_up_while_music: bool = True
    no_follow_up_after_home_commands: bool = True
    # 21/11: while she answers no audio goes to Gemini; only the wake word interrupts her (the answer becomes a "recado").
    # The dataclass default stays False so that tests of other behavior keep today's flow; the program passes the config.
    only_wake_word_interrupts: bool = False


def _chave(nome):
    """Nome de timer sem acento, caixa nem espaço sobrando (o mesmo critério de `timers._chave`)."""
    t = unicodedata.normalize("NFKD", nome or "").encode("ascii", "ignore").decode()
    return " ".join(t.lower().split())


class Maestro:
    def __init__(self, ajustes: Ajustes, mudo_inicial=False):
        self.a = ajustes
        # None = ainda não se sabe (partida do programa): escrever luz com o mudo físico ligado RELIGA o
        # microfone (medido em 06/10), então nada de luz, palavra ou botão até a captura decidir (revisão C1).
        self.mudo = mudo_inicial
        self.nao_perturbe = False  # D11: fora de _zerar — sobrevive ao fim das conversas
        # spec 2026-10-20: som de confirmação do não perturbe que mudou durante o alarme ou a aprendizagem; toca quando
        # o maestro volta a `parada` (no lugar do `fim`). Fora de _zerar: o fechamento é justamente quando toca.
        self._som_np = None
        # 20/10 (dono): mudou com a conversa aberta → o som toca na hora e o `fim` some até voltar a `parada`
        self._sem_fim = False
        # spec 2026-10-20 D1/D5: conversa sussurrada (sem sons de início/fim). Fora de _zerar de propósito: o som de
        # fim do próprio fechamento ainda é filtrado; a próxima conversa nova a partir de `parada` redefine.
        self.whisper = False
        self.ultima_abertura = None
        # spec 2026-10-12: fora de _zerar, sobrevivem ao fim das conversas
        self._alarme = None  # {"timer", "inicio"} do alarme em curso
        self._fila_alarmes, self._fila_entregas = [], []  # timers vencidos esperando; (id, instante) de entregas
        self._fila_anuncios, self._anuncio = [], None  # (id, instante) de anúncios esperando; id do que está tocando
        self._cadastro = None  # {"nome", "id"} agendado na conversa ou em gravação; fora de _zerar
        self._teste = None  # {"inicio", "fim", "contagem", "avisou"} do teste da palavra; fora de _zerar
        # spec 2026-11-08 (rede), 2026-11-21: sem rede há >= 2 min (o netwatch registra o início da queda, com o Pi
        # em falta ou não); luz de erro fixa em repouso. O nome `rede_esgotada` ficou. Fora de _zerar.
        self.rede_esgotada = False
        # o mudo foi apertado com a luz de rede acesa (tocou o som de erro): ao voltar a rede, desfaz o mudo
        self._mudo_na_falha = False
        self._zerar()

    def _zerar(self):
        self.estado = PARADA
        self.pendentes = 0
        self._recado = False  # v0.3.7: resposta pendente/entrega sendo falada: mic fechado, só a palavra interrompe
        self._recado_fim = None  # v0.3.7: pausa dentro do recado: o mic só abre se ela não voltar em RECADO_PAUSA_S
        # 21/11: the recado is a normal answer closed by only_wake_word_interrupts (light stays "respondendo")
        self._resposta_normal = False
        # 21/11: the bot told us Gemini completed the turn (`resposta_completa`): the mic reopens at her stop
        self._completa = False
        self.mudo_software = False  # v0.3.7: mudo pela config; o anel leva o bit do mudo, então as luzes podem passar
        self._prazo = None  # (motivo, instante)
        self.pi_turns = False  # two-beam: the Pi's speech start/stop drive the bot's turns (set by turnos_pi at connect)
        self._alguem_falando = False
        self._fala_desde = None  # R13: instante em que a fala contínua atual começou
        # 25/11: what the speech detector hears now, in any state (even while a recado ignores it), and since when; a
        # conversation opening or a mic reopening inherits it, and her own close waits for speech begun after she stopped
        self._detector_falando, self._detector_desde, self._ela_parou_em = False, None, None
        self._ela_falando = False
        self._abertura = None
        self._motivo_corte = None
        self.ferramentas = 0  # D2: ferramentas da voz em andamento (aviso do bot)
        self._t_fala_parou = None  # D4: para medir quanto ela leva para começar a falar
        self._resposta_ate = None  # 19/10: resposta do cérebro devolvida e ainda não falada (teto cerebro_fim_s)
        self._mic_fechado = False  # 04/11: microfone fechado esperando a resposta (close_mic_awaiting_reply)
        self._musica_tocando = False  # 04/11: havia música tocando quando ela começou a responder

    def publico(self) -> dict:
        return {"estado": self.estado, "mudo": self.mudo, "nao_perturbe": self.nao_perturbe, "pendentes": self.pendentes,
                "ultima_abertura": self.ultima_abertura,
                "alarme": (self._alarme["timer"].get("nome") or "sem nome") if self._alarme else None,
                "alarmes": self._timers_tocando(),
                "cadastro": self._cadastro["nome"] if self._cadastro else None,
                "fila_entregas": len(self._fila_entregas), "fila_anuncios": len(self._fila_anuncios),
                "net_exhausted": self.rede_esgotada}

    def evento(self, nome: str, agora: float, valor=None) -> list:
        acoes = self._trata(nome, agora, valor)
        if self._sem_fim:
            acoes = [a for a in acoes if a != ("som", "fim")]
            self._sem_fim = self.estado != PARADA
        if self._som_np and self.estado == PARADA:
            acoes = self._com_som_np(acoes)
        return self._filtrar(acoes)

    def _com_som_np(self, acoes):
        """Spec 2026-10-20: o som do não perturbe pendente substitui o `fim` desta chamada (ou entra no começo)."""
        som, self._som_np = ("som", self._som_np), None
        if ("som", "fim") in acoes:
            return [som if a == ("som", "fim") else a for a in acoes]
        return [som] + acoes

    def _filtrar(self, acoes):
        if self.mudo is not False and not self.mudo_software:  # mudo do botão (ou desconhecido): o anel é do Jabra
            acoes = [a for a in acoes if a[0] != "luz"]
        if self.whisper:  # D5: sem sons de início e fim (erro e os demais continuam)
            acoes = [a for a in acoes if a not in (("som", "inicio"), ("som", "fim"))]
        return acoes

    def palavra_agiria(self, valor=None) -> bool:
        """Spec 2026-11-16 §5.1: o `evento("palavra", ...)` devolveria ações agora? Pura (não muda estado) e
        espelha as guardas de `_trata` para a palavra; quem muda lá muda aqui (test_palavra_agiria_matches_evento).
        O teste da palavra e a gravação do cadastro são da Pi e nunca disputam, embora o `evento` os trate."""
        e = self.estado
        if e in (TESTE_PALAVRA, CADASTRO) or self.mudo is not False:
            return False
        if e in (ALARME, ANUNCIO):  # a palavra encerra o alarme/anúncio e abre a conversa
            return True
        if self._recado and e in ABERTO:  # v0.3.7/b287504: só a palavra ativa corta o recado
            return True
        return e not in ABERTO  # PARADA, CORTADA e AGUARDANDO abrem/reabrem; aberta sem recado ignora

    def campainha(self, entrega_id: str, agora: float, ocupado=False):
        """POST /doorbell (spec 2026-10-12 §3): ("calado" | "na_fila" | "vou_abrir", ações). `ocupado`: o programa
        espera o veredito da disputa de uma palavra (spec 2026-11-16): a palavra vem antes, a entrega espera na fila."""
        if self.nao_perturbe:
            return "calado", []
        if ocupado or self.estado != PARADA or self._alarme is not None or self.mudo is None:
            self._fila_entregas.append((entrega_id, agora))
            return "na_fila", []
        return "vou_abrir", self._filtrar(self._abrir_entrega(agora, entrega_id))

    def anuncio(self, anuncio_id: str, agora: float, ocupado=False, idade_s: float = 0):
        """POST /announce (spec 2026-11-20): ("calado" | "na_fila" | "vou_tocar" | "expirado", ações). Mesmo contrato da
        campainha: `ocupado` = o programa espera o veredito da disputa de uma palavra. Fila ordenada: alarmes, entregas,
        anúncios. `idade_s` = há quanto tempo o bot recebeu o pedido (reenvios incluídos): os 5 min contam de lá."""
        if self.tem_anuncio(anuncio_id):  # reenvio do mesmo id (o POST do add-on estourou o prazo): já é nosso
            return "na_fila", []
        if self.nao_perturbe:
            return "calado", []
        idade = max(0.0, float(idade_s or 0))
        if idade >= self.a.anuncio_max_s:  # chegou velho (reenvio tardio): não toca nem enfileira
            return "expirado", [("anuncio_apagar", anuncio_id),
                                ("registrar_anuncio", {"id": anuncio_id, "resultado": "expirado"})]
        if (ocupado or self.estado != PARADA or self._alarme is not None or self.mudo is None
                or self._fila_alarmes or self._fila_entregas or self._fila_anuncios):
            self._fila_anuncios.append((anuncio_id, agora - idade))
            return "na_fila", []
        return "vou_tocar", self._filtrar(self._tocar_anuncio(anuncio_id))

    def tem_anuncio(self, anuncio_id: str) -> bool:
        """O anúncio está na fila ou tocando agora (o arquivo <id>.wav tem dono)."""
        return anuncio_id == self._anuncio or any(aid == anuncio_id for aid, _ in self._fila_anuncios)

    def _tocar_anuncio(self, anuncio_id):
        self.estado, self._anuncio = ANUNCIO, anuncio_id
        return [("volume_alerta", True), ("luz", "falando"), ("anuncio_tocar", anuncio_id)]

    def _proximo_anuncio(self, agora):
        """Mais antigo primeiro; vencido (anuncio_max_s) ou em não perturbe é descartado e registrado. Com o mudo ainda
        desconhecido (partida do programa) os anúncios esperam, dentro do prazo."""
        acoes, espera = [], []
        for n, (aid, desde) in enumerate(self._fila_anuncios):
            if agora - desde > self.a.anuncio_max_s:
                resultado = "expirado"
            elif self.nao_perturbe:
                resultado = "calado"
            elif self.mudo is None:
                espera.append((aid, desde))
                continue
            else:
                self._fila_anuncios = espera + self._fila_anuncios[n + 1:]
                return acoes + self._filtrar(self._tocar_anuncio(aid))
            acoes += [("anuncio_apagar", aid), ("registrar_anuncio", {"id": aid, "resultado": resultado})]
        self._fila_anuncios = espera
        return acoes

    def _fim_anuncio(self, agora, anuncio_id):
        if anuncio_id != self._anuncio:
            return []  # resto de um anúncio já cortado (palavra/botão)
        self._anuncio, self.estado = None, PARADA
        return [("volume_alerta", False), ("luz", self._repouso()), ("anuncio_apagar", anuncio_id)] + \
            self._proxima_da_fila(agora)

    def _parar_anuncio(self):
        aid, self._anuncio, self.estado = self._anuncio, None, PARADA
        return aid

    # ---- transições
    def _abrir(self, agora, como, whisper=False, wake_word=None, extras=None):
        if self._abertura is None:
            self._abertura = {"inicio": agora, "como_abriu": como}
            if self.estado == PARADA:
                self.whisper = bool(whisper)  # D1: decidido na abertura; reabrir não muda
                if self.whisper:
                    self._abertura["whisper"] = True
        self._prazo = ("espera_inicial", agora + self.a.espera_inicial_s)
        self._mic_fechado = False
        acoes = [("som", "inicio"), ("luz", "ouvindo")]
        if self.estado == PARADA:
            # conversa nova: nada herdado de uma conexão anterior (revisão final I1)
            self.pendentes, self._ela_falando, self._ela_parou_em = 0, False, None
            # 25/11: "Friday, <request>" in one breath: the detector's segment began with the word and emits no new
            # fala_comecou, so the speech already running counts (the continuous sound cutoff counts from here)
            self._alguem_falando = self._detector_falando
            self._fala_desde = agora if self._alguem_falando else None
            if self.whisper:  # a captura começa o DetectorFala "falando" (a frase segue depois da palavra)
                self._alguem_falando, self._fala_desde = True, agora
            self.estado = CONECTANDO
            # 28/10: a palavra que abriu vai ao bot (Jarvis Logbook); sem palavra e sem sussurro = None, como antes
            dados = {k: v for k, v in (("whisper", self.whisper), ("wake_word", wake_word)) if v}
            dados.update(extras or {})
            acoes.append(("conectar", dados or None))
        else:
            self.estado = OUVINDO
            acoes.append(("microfone", True))
        return acoes

    def _fechar(self, agora, motivo, som="fim"):
        self._recado, self._recado_fim, self._resposta_normal = False, None, False
        como = self._abertura["como_abriu"] if self._abertura else None
        acoes = [("som", som)] if som else []
        acoes += [("luz", self._repouso()), ("desconectar", None)]
        if self._abertura is not None:
            r = dict(self._abertura, duracao_s=round(agora - self._abertura["inicio"], 1), como_fechou=motivo)
            self.ultima_abertura = r
            acoes.append(("registrar", r))
        self._zerar()
        acoes += self._apos_conversa(agora, como)
        if self._cadastro is not None:  # D15/D20: a conversa que pediu a aprendizagem acabou
            if motivo in ("falha", "mudo") or self.mudo is True:  # revisão final I3: mudo = não gravar silêncio
                self._cadastro = None
                acoes.append(("cadastro", "cancelar_agendado"))
            elif self.estado == PARADA:
                self.estado = CADASTRO
                acoes += [("luz", "ouvindo"), ("cadastro", "iniciar")]
        return acoes

    def _sair_teste(self, resultado, motivo=None):
        """Fim do teste da palavra: volta a `parada` e avisa o agente (onboarding)."""
        self._teste, self.estado, self._prazo = None, PARADA, None
        dados = {"resultado": resultado}
        if motivo:
            dados["motivo"] = motivo
        return [("luz", self._repouso()), ("onboarding", dados)]

    def _iniciar_teste(self, agora, timeout_s):
        if self.mudo is True:
            return [("frase", "mic_muted"), ("onboarding", {"resultado": "wake_word_failed", "motivo": "mudo"})]
        self.estado = TESTE_PALAVRA
        self._teste = {"inicio": agora, "fim": agora + float(timeout_s), "contagem": 0, "avisou": False}
        return [("frase", "say_wake_word"), ("luz", "ouvindo")]

    def _tique_teste(self, agora):
        t = self._teste
        if agora >= t["fim"]:
            return self._sair_teste("wake_word_failed", "tempo_esgotado")
        if t["contagem"] == 0 and not t["avisou"] and agora - t["inicio"] >= TESTE_SEM_PALAVRA_S:
            t["avisou"] = True
            return [("frase", "mic_muted")]
        return []

    def _sair_cadastro(self, motivo):
        self._cadastro, self.estado, self._prazo = None, PARADA, None
        acoes = [("luz", self._repouso())]
        if motivo:
            acoes.append(("cadastro", motivo))
        return acoes

    # ---- timers e entregas (spec 2026-10-12)
    def _apos_conversa(self, agora, como):
        if como == "entrega":
            return [("volume_alerta", False)]
        if como == "palavra" and self._alarme:  # a conversa da palavra acabou sem parar o timer: o alarme volta
            if agora >= self._alarme["inicio"] + self.a.alarme_max_s:
                return self._encerrar_alarme(agora, "nao_atendido")
            self.estado = ALARME
            if self._alarme.get("music"):
                self._prazo = ("music_wait", agora + self.a.music_wait_s)
            elif not self._alarme.get("aviso"):
                self._prazo = ("nome_do_timer", self._quando_o_nome(agora))
            return [("volume_alerta", True), ("alarme", "tocar"), ("luz", "alarme")]
        return []

    @property
    def musical_alarm(self) -> bool:
        """True enquanto o alarme em curso espera/pediu a música (R11)."""
        return bool(self._alarme and self._alarme.get("music"))

    def _iniciar_alarme(self, agora, timer):
        self._alarme = {"timer": timer, "inicio": agora}
        self.estado = ALARME
        acoes = [("volume_alerta", True), ("alarme", "tocar"), ("luz", "alarme")]
        if timer.get("music"):  # R11: campainha + pedido de música; sem música em music_wait_s, segue como sempre
            self._alarme["music"] = True
            self._prazo = ("music_wait", agora + self.a.music_wait_s)
            acoes.append(("request_music", {"timer_id": timer.get("id"), "query": timer["music"]}))
        else:
            self._prazo = ("nome_do_timer", self._quando_o_nome(agora))
        return acoes

    def _encerrar_alarme(self, agora, como):
        t, inicio = self._alarme["timer"], self._alarme["inicio"]
        em_conversa = self.estado != ALARME and self.estado != PARADA  # parado pela ferramenta, com a conversa aberta
        self._alarme = None
        if not em_conversa:
            self.estado, self._prazo = PARADA, None
        r = {"id": t.get("id"), "timer": t.get("nome"), "como_parou": como, "tocou_s": round(agora - inicio, 1)}
        acoes = [("alarme", "parar"), ("volume_alerta", False)]
        if not em_conversa:
            acoes.append(("luz", self._repouso()))
        acoes.append(("registrar_timer", r))
        if como == "nao_atendido":
            acoes.append(("aviso_nao_atendido", t))
        return acoes

    def _timers_tocando(self):
        lista = ([self._alarme["timer"]] if self._alarme else []) + list(self._fila_alarmes)
        return [{"id": t.get("id"), "nome": t.get("nome")} for t in lista]

    def parar_alarmes(self, agora, nome=None):
        """Comando `timers_parar` (a ferramenta do bot): para o alarme em curso e os da fila, todos ou só os de `nome`.
        Devolve ([{"id","nome"}, ...] parados, ações). Com uma conversa aberta ela segue; sem nada tocando, ([], [])."""
        alvo = (_chave(nome) or None) if isinstance(nome, str) else None

        def casa(t):
            return alvo is None or _chave(t.get("nome")) == alvo

        parados, acoes = [], []
        if self._alarme and casa(self._alarme["timer"]):
            t = self._alarme["timer"]
            parados.append({"id": t.get("id"), "nome": t.get("nome")})
            acoes += self._encerrar_alarme(agora, "palavra")
        resto = []
        for t in self._fila_alarmes:
            if casa(t):
                parados.append({"id": t.get("id"), "nome": t.get("nome")})
                acoes.append(("registrar_timer", {"id": t.get("id"), "timer": t.get("nome"), "como_parou": "palavra",
                                                  "tocou_s": 0.0}))
            else:
                resto.append(t)
        self._fila_alarmes = resto
        return parados, acoes

    def _abrir_propria(self, agora, como, extras):
        """Conversa aberta pela Pi (timer ou entrega): a Friday fala primeiro, então a espera inicial é a de
        resposta, não os 4 s de quem acabou de dizer a palavra."""
        self._abertura = {"inicio": agora, "como_abriu": como}
        self.whisper = False  # entrega e timer têm os sons de sempre
        self.pendentes, self._ela_falando, self._alguem_falando = 0, False, False
        self._fala_desde = None
        self.estado = CONECTANDO
        self._prazo = ("espera_inicial", agora + self.a.resposta_max_s)
        return [("luz", "ouvindo"), ("conectar", extras)]

    def _quando_o_nome(self, agora):
        """24/11: o aviso falado do timer vem depois da rampa e de `voltas_antes_do_nome` voltas do alarme."""
        return agora + self.a.rampa_s + self.a.voltas_antes_do_nome * self.a.volta_alarme_s

    def _abrir_entrega(self, agora, entrega_id):
        return self._fechar_mic_do_recado([("volume_alerta", True), ("som", "lembrete")] + self._abrir_propria(
            agora, "entrega", {"entrega": entrega_id}))

    def _proxima_da_fila(self, agora):
        if self._fila_alarmes:
            return self._iniciar_alarme(agora, self._fila_alarmes.pop(0))
        while self._fila_entregas:
            eid, desde = self._fila_entregas.pop(0)
            if agora - desde <= self.a.fila_entrega_max_s and not self.nao_perturbe and self.mudo is not None:
                return self._abrir_entrega(agora, eid)
        return self._proximo_anuncio(agora)

    def _cortar(self, agora, motivo):
        self._recado, self._recado_fim, self._resposta_normal, self._completa = False, None, False, False
        self.estado = CORTADA
        self._motivo_corte = motivo
        self._prazo = ("resposta", agora + self.a.resposta_max_s)
        return [("som", "fim"), ("luz", self._repouso()), ("cortar", None)]

    def _cancelar(self, agora):
        """Botão vermelho: para a fala e as tarefas do bot (`cancelar_conversa`, ANTES do `desconectar`) e fecha. Entrega
        de lembrete/timer conta como atendida, como no verde."""
        acoes = [("avisar_bot", "atendida")] if self._da_conversa("entrega") else []
        acoes.append(("cancelar_conversa", None))
        return acoes + self._fechar(agora, "botao_vermelho", som="fim")

    def _apos_resposta_cortada(self, agora):
        if self.pendentes > 0:
            self.estado = AGUARDANDO
            self._prazo = None
            return [("luz", "cerebro")]
        return self._fechar(agora, self._motivo_corte, som=None)

    def _repouso(self):
        if self.rede_esgotada:  # sem rede há >= 2 min (spec 2026-11-21): a luz de erro de rede vence o não perturbe
            return "rede_esgotada"
        return "nao_perturbe" if self.nao_perturbe else "parada"

    def _luz_do_estado(self):
        if self._recado and self.estado in ABERTO:
            return "respondendo" if self._resposta_normal else "falando"
        if self._ela_falando and self.estado == OUVINDO:
            return "respondendo"
        return {CONECTANDO: "ouvindo", OUVINDO: "ouvindo", AGUARDANDO: "cerebro", ALARME: "alarme", ANUNCIO: "falando",
                CADASTRO: "ouvindo", TESTE_PALAVRA: "ouvindo"}.get(
            self.estado, self._repouso())

    @staticmethod
    def _sussurro(valor):
        return bool(isinstance(valor, dict) and valor.get("whisper"))

    @staticmethod
    def _palavra(valor):
        return valor.get("wake_word") if isinstance(valor, dict) else None

    def turnos_pi(self, ativo: bool) -> None:
        """Two-beam: the program calls it right after deciding the mode at `conectar`; `_zerar` clears it."""
        self.pi_turns = bool(ativo)

    def _aviso(self, e, tipo):
        """D7: em conversa sussurrada aberta, o início/fim da fala vai ao bot (o Live roda sem VAD do servidor)."""
        # revisão final (20/10): só com a conexão aberta; o que aconteceu antes vai no `conectou`
        return [("avisar_bot", tipo)] if (self.whisper or self.pi_turns) and e == OUVINDO else []

    def _da_conversa(self, como):
        return self._abertura is not None and self._abertura["como_abriu"] == como

    def _espera_curta_vale(self):
        """D1/D2: só em conversa aberta pela palavra ou pelo botão, e sem ferramenta em andamento. Timer e entrega
        mantêm os 20 s (regra do dono: nada muda na entrega dos timers)."""
        return (self._abertura is not None and self._abertura["como_abriu"] in ("palavra", "botao")
                and self.ferramentas == 0)

    # ---- eventos
    def _trata(self, nome, agora, valor):
        e = self.estado
        if nome == "tique":
            if self._recado_fim is not None and agora >= self._recado_fim:
                # parou = quando ela de fato parou: os prazos contam dali
                return self._reabrir(self._recado_fim - RECADO_PAUSA_S) + self._tique(agora)
            return self._tique(agora)
        if nome == "nao_perturbe_inicial":  # arranque da Pi: aplica o estado gravado, sem som
            self.nao_perturbe = bool(valor)
            return [("luz", self._luz_do_estado())]
        if nome == "nao_perturbe":  # D11: só cala o que ela diria por conta própria; palavra e microfone normais
            novo = bool(valor)
            mudou = novo != self.nao_perturbe
            self.nao_perturbe = novo
            som = None
            if mudou:  # spec 2026-10-20: som só quando muda de fato
                som = "nao_perturbe_ligou" if novo else "nao_perturbe_desligou"
                if e in (ALARME, ANUNCIO, CADASTRO, TESTE_PALAVRA):  # não toca por cima do alarme nem na gravação: fica pendente
                    self._som_np, som = som, None
                elif e != PARADA:  # conversa aberta: toca na hora e este som é o encerramento dela
                    self._sem_fim = True
            luz = [("luz", self._luz_do_estado())]
            return ([("som", som)] if som else []) + luz
        if nome == "rede_esgotada":  # spec 2026-11-08/21: "sem rede há >= 2 min", do arquivo de estado do netwatch
            novo = bool(valor)
            mudou = novo != self.rede_esgotada
            self.rede_esgotada = novo
            acoes = []
            if mudou and not novo:
                if self._mudo_na_falha and self.mudo is True:
                    acoes.append(("desfazer_mudo", None))
                self._mudo_na_falha = False
            if mudou and e == PARADA:
                acoes.append(("luz", self._luz_do_estado()))
            return acoes
        if nome == "mudo":
            self.mudo = bool(valor)
            extra = []
            if self.mudo and self.rede_esgotada:  # o vermelho parece mudo: o som de erro mostra que é a rede
                extra, self._mudo_na_falha = [("som", "erro")], True
            elif not self.mudo:
                self._mudo_na_falha = False
            if self.mudo and e == TESTE_PALAVRA:
                return self._sair_teste("wake_word_failed", "mudo") + extra
            if self.mudo and e == CADASTRO:
                return self._sair_cadastro("cancelar") + extra
            if self.mudo and e in ABERTO:
                return self._cortar(agora, "mudo") + extra
            return extra if self.mudo else [("luz", self._luz_do_estado())]
        if nome in ("music_started", "music_failed"):  # R11: resposta do pedido de música do alarme
            if not self.musical_alarm or valor != self._alarme["timer"].get("id"):
                return []  # tarde demais (já seguiu como alarme normal) ou de outro timer
            if nome == "music_started":
                return self._encerrar_alarme(agora, "music") + [("music_ramp", None)]
            self._alarme["music"] = False
            self._prazo = ("nome_do_timer", max(agora, self._quando_o_nome(self._alarme["inicio"])))
            return []
        if nome == "timer_venceu":
            if self._alarme is not None or e == ANUNCIO:  # durante um anúncio o timer espera (e toca antes do próximo)
                self._fila_alarmes.append(valor)
                return []
            if e == TESTE_PALAVRA:  # o timer tem prioridade; o teste é cancelado
                acoes = self._sair_teste("wake_word_failed", "timer")
            elif e == CADASTRO:  # D20: o timer tem prioridade; a gravação é interrompida
                acoes = self._sair_cadastro("interromper")
            else:
                acoes = []
                if self._cadastro is not None:
                    self._cadastro = None
                    acoes.append(("cadastro", "cancelar_agendado"))
                acoes += self._fechar(agora, "timer", som=None) if e != PARADA else []
            return acoes + self._iniciar_alarme(agora, valor)
        if nome == "teste_palavra":  # só a partir de `parada`
            return self._iniciar_teste(agora, valor if valor else 30) if e == PARADA and self._alarme is None else []
        if nome == "abrir_apresentacao":  # a Pi abre a conversa de apresentação (spec 2026-11-05 §6.4)
            if e != PARADA or self._alarme is not None or self.mudo is not False:
                return []
            return self._fechar_mic_do_recado(self._abrir_propria(agora, "apresentacao", {"presentation": valor}))
        if e == TESTE_PALAVRA:
            if nome == "botao":
                return self._sair_teste("wake_word_failed", "botao_vermelho") if valor == "vermelho" else []
            if nome == "palavra":
                if self.mudo is not False:
                    return []
                self._teste["contagem"] += 1
                if self._teste["contagem"] == 1:
                    return [("frase", "heard_it")]
                return self._sair_teste("wake_word_ok")
            return []
        if e == CADASTRO:  # D20: só o botão, o mudo, o timer e o fim mexem na gravação
            if nome == "botao":
                return self._sair_cadastro("cancelar")
            if nome == "cadastro_fim":
                return self._sair_cadastro(None)
            return []
        if nome == "cadastro_agendado":
            if e in (CONECTANDO, OUVINDO, CORTADA, AGUARDANDO) and self._cadastro is None:
                self._cadastro = valor
            return []
        if e == ANUNCIO:  # spec 2026-11-20: só o fim, a palavra ativa e o botão mexem; fala da sala não corta
            if nome == "anuncio_fim":
                return self._fim_anuncio(agora, valor)
            if nome == "botao":
                aid = self._parar_anuncio()
                return [("anuncio_parar", aid), ("volume_alerta", False), ("luz", self._repouso()),
                        ("anuncio_apagar", aid)]
            if nome == "palavra" and self.mudo is False:
                aid = self._parar_anuncio()
                return [("anuncio_parar", aid), ("volume_alerta", False), ("anuncio_apagar", aid)] + self._abrir(
                    agora, "palavra", self._sussurro(valor), self._palavra(valor))
            return []
        if e == ALARME:
            if nome == "botao":
                return self._encerrar_alarme(agora, "botao")
            if nome == "palavra" and self.mudo is False:
                # 24/11: a palavra pausa o alarme e abre uma conversa que já sabe o que está tocando; quem para o
                # timer é o comando `parar_alarmes` (a ferramenta do bot). Sem parar, o alarme volta ao fechar.
                extras = {"timers_tocando": self._timers_tocando()}
                self._alarme["music"] = False  # o pedido de música cai: ao voltar, segue como um timer normal
                self.estado, self._prazo = PARADA, None
                return [("alarme", "pausar"), ("volume_alerta", False)] + self._abrir(
                    agora, "palavra", self._sussurro(valor), self._palavra(valor), extras=extras)
            return []
        if nome == "botao" and self._alarme is not None and e in (CONECTANDO, OUVINDO, CORTADA, AGUARDANDO):
            # 24/11 (revisão I1): o botão também para o timer cuja palavra abriu esta conversa (alarme pausado)
            acoes = self._encerrar_alarme(agora, "botao")
            return acoes + (self._cancelar(agora) if valor == "vermelho" else self._cortar(agora, "botao"))
        if nome == "botao" and valor == "vermelho":
            # Spec 2026-11-04: o vermelho corta tudo e fecha, em qualquer estado de conversa aberta. Parado, só o
            # verde abre. (Alarme e cadastro já foram tratados acima: qualquer cor os encerra como antes.)
            return self._cancelar(agora) if e in (CONECTANDO, OUVINDO, CORTADA, AGUARDANDO) else []
        if nome == "botao" and e in ABERTO and self._da_conversa("entrega"):
            return [("avisar_bot", "atendida")] + self._cortar(agora, "botao")
        if nome in ("cerebro_pendente", "ela_comecou", "ela_parou", "ferramenta", "musica", "resposta_completa") \
                and e == PARADA:
            return []  # resto de uma conexão já descartada (revisão final I1)
        if nome == "resposta_completa":  # 21/11: Gemini completed the turn; only open conversations care
            if e not in ABERTO:
                return []
            self._completa = True
            if self._recado_fim is not None and self.a.only_wake_word_interrupts:  # pause running: no need to wait
                return self._reabrir(self._recado_fim - RECADO_PAUSA_S)
            return []
        if nome == "ferramenta":  # D2
            self.ferramentas = max(0, int(valor or 0))
            if e not in ABERTO:
                return []
            if self.ferramentas > 0:
                if self._prazo and self._prazo[0] == "resposta_curta":
                    self._prazo = ("resposta", agora + self.a.resposta_max_s)  # teto: nunca sem prazo
            elif (not self._alguem_falando and not self._ela_falando and self._espera_curta_vale()
                  and (self._prazo is None or self._prazo[0] == "resposta")):
                self._prazo = ("resposta_curta", agora + self.a.espera_resposta_s)
            return []
        if nome == "palavra":
            if self._recado and e in ABERTO and self.mudo is False:  # só a palavra ativa corta o recado
                self._recado, self._recado_fim, self._resposta_normal, self._completa = False, None, False, False
                herdada = []
                if self.pi_turns and self._detector_falando and not self._alguem_falando:
                    # two-beam: "Friday, <request>" in one breath cuts her with the speech already running
                    self._alguem_falando, self._fala_desde = True, agora
                    herdada = [("avisar_bot", "speech_started")]
                return [("parar_fala", None), ("microfone", True), ("luz", "ouvindo")] + herdada \
                    + [("fim_da_resposta", None)]
            if self.mudo is not False or e in ABERTO:
                return []
            return self._abrir(agora, "palavra" if e == PARADA else self._abertura["como_abriu"],
                               self._sussurro(valor), self._palavra(valor))
        if nome == "botao":
            if e in ABERTO:
                return self._cortar(agora, "botao")
            if self.mudo is not False:
                return []
            return self._abrir(agora, "botao" if e == PARADA else self._abertura["como_abriu"])
        if nome == "conectou":
            if e == CONECTANDO:
                self.estado = OUVINDO
            if self.whisper and e in ABERTO:
                # §4.2 + revisão final (20/10): a conversa começa com a fala em andamento. Se a frase já acabou
                # enquanto a conexão abria, o fim vai junto (e o prazo de resposta do fala_parou continua).
                if self._alguem_falando:
                    if self._prazo and self._prazo[0] in ("espera_inicial", "continuacao"):
                        self._prazo = None
                    return [("avisar_bot", "speech_started")]
                return [("avisar_bot", "speech_started"), ("avisar_bot", "speech_stopped")]
            if self.pi_turns and e in ABERTO:  # two-beam: the turn opens only if someone is speaking right now
                return [("avisar_bot", "speech_started")] if self._alguem_falando else []
            return []
        if nome == "fala_quem_chamou":  # two-beam: the caller is speaking: never ages into the continuous-sound cutoff
            if e in ABERTO and self._alguem_falando:
                self._fala_desde = agora
            return []
        if nome in ("falha_conexao", "caiu"):
            if e == PARADA:
                return []
            # conversa do timer que falhou: o alarme volta sozinho, sem o som de erro por cima
            return self._fechar(agora, "falha", som=None if self._alarme is not None else "erro")
        if nome == "fala_comecou":
            self._detector_falando, self._detector_desde = True, agora
            if self._recado and e in ABERTO:  # 19/11 (revisão I2): fala da sala no recado não chega a ela nem conta prazo
                return []
            self._completa = False  # a new utterance: a leftover `resposta_completa` must not reopen the next answer
            self._alguem_falando = True
            self._fala_desde = agora
            if self._prazo and self._prazo[0] in ("espera_inicial", "continuacao"):
                self._prazo = None
            return self._aviso(e, "speech_started")
        if nome == "fala_parou":
            self._detector_falando = False
            self._alguem_falando = False
            self._fala_desde = None
            if self._recado and e in ABERTO:  # 19/11 (revisão I2): idem; nem mede espera nem fecha o microfone
                return []
            if self._mic_fechado:  # 04/11: fala da sala com o microfone fechado: não muda nada
                return []
            self._t_fala_parou = agora
            if e in ABERTO and not self._ela_falando:
                if self._espera_curta_vale():
                    self._prazo = ("resposta_curta", agora + self.a.espera_resposta_s)
                else:
                    self._prazo = ("resposta", agora + self.a.resposta_max_s)
                if e == OUVINDO and self.a.close_mic_awaiting_reply:
                    self._mic_fechado = True
                    return self._aviso(e, "speech_stopped") + [("cortar", None), ("luz", "cerebro")]
            return self._aviso(e, "speech_stopped")
        if nome == "ela_comecou":
            self._ela_falando = True
            reabrir = []
            pausa, self._recado_fim = self._recado_fim is not None, None  # voltou a falar na pausa: o recado continua
            if e == AGUARDANDO:  # a resposta pendente chegou dentro da janela: é um recado também
                self._recado = True
            elif e in ABERTO and self._da_conversa("apresentacao"):  # 19/11: na apresentação toda fala dela é recado
                self._recado = True
            elif e == OUVINDO and self.a.only_wake_word_interrupts and not self._recado:
                self._recado, self._resposta_normal = True, True  # 21/11: toda resposta fecha o mic; só a palavra corta
            if self._recado:
                self._mic_fechado = False
                # 19/11 (revisão I1): a luz acompanha o mic
                reabrir = [("microfone", False), ("luz", "respondendo" if self._resposta_normal else "falando")]
            elif self._mic_fechado:  # 04/11: ela vai responder: o microfone volta (interrupção e continuação)
                self._mic_fechado = False
                reabrir = [("microfone", True), ("luz", "respondendo")]
            elif e == OUVINDO:  # 18/11: luz de resposta, como a Echo, enquanto ela fala
                reabrir = [("luz", "respondendo")]
            self._resposta_ate = None
            medida = []
            if self._t_fala_parou is not None and self._abertura is not None:  # D4
                medida = [("registrar_espera", {"espera_resposta": round(agora - self._t_fala_parou, 1),
                                                "como_abriu": self._abertura["como_abriu"]})]
                self._t_fala_parou = None
            if e == AGUARDANDO:  # resposta do cérebro chegando sem "cerebro_entregando" (dentro da janela)
                acoes = self._fechar_mic_do_recado(self._abrir(agora, self._abertura["como_abriu"]))
                self._prazo = None
                return medida + acoes + [a for a in reabrir if a not in acoes]
            if self._prazo:
                self._prazo = None
            return medida + reabrir + ([("guardar_pausa", False)] if pausa else [])
        if nome == "musica":  # 04/11: resultado da consulta ao Sendspin, feita quando ela começou a responder
            self._musica_tocando = bool(valor)
            return []
        if nome == "ela_parou":
            self._ela_falando = False
            self._ela_parou_em = agora
            if self._recado and e in ABERTO:  # pausa ou fim do recado: o mic abre se ela não voltar logo (_tique)
                if not self.a.only_wake_word_interrupts:  # switch off: the recado as before 21/11
                    self._recado_fim = agora + RECADO_PAUSA_S
                    return []
                if self._completa:  # 21/11: Gemini already completed the turn: this is the end, not a pause
                    return self._reabrir(agora)
                self._recado_fim = agora + RECADO_PAUSA_S
                return [("guardar_pausa", True)]  # the audio of the wait is kept for when the mic opens
            if self._cadastro is not None and e == OUVINDO:  # D15: a confirmação dela terminou: a Pi assume
                return self._fechar(agora, "aprendizagem", som=None)
            if e == OUVINDO and self.a.no_follow_up_while_music and self._musica_tocando:
                return self._fechar(agora, "musica", som="fim")
            if e == OUVINDO:
                self._prazo = ("continuacao", agora + self.a.continuacao_s)
                self._completa = False
                return [("luz", "ouvindo"), ("fim_da_resposta", None)]
            if e == CORTADA:
                return self._apos_resposta_cortada(agora)
            return []
        if nome == "cerebro_pendente":
            antes, self.pendentes = self.pendentes, int(valor)
            if antes > 0 and self.pendentes == 0 and e in ABERTO and not self._ela_falando:
                # 19/10 (caso real 10:46): devolvida com a conversa aberta, a resposta ainda não foi falada; um prazo
                # que vença antes da fala dela não fecha a conversa (ver _tique)
                self._resposta_ate = agora + self.a.cerebro_fim_s
            if self.pendentes == 0 and e == AGUARDANDO:
                # o "0" chega logo que o cérebro devolve; a fala dela vem ~1 s depois: dá um tempo antes de fechar
                self._prazo = ("cerebro_fim", agora + self.a.cerebro_fim_s)
            return []
        if nome == "encerrar":  # 07/10: "parar" ou conversa que não é com ela; 15/10: motivo "agradecimento" (D6)
            if e == PARADA:
                return []
            if valor == "comando_casa" and (not self.a.no_follow_up_after_home_commands or self._alguem_falando):
                return []  # 04/11: switch desligado ou alguém já falando: a conversa segue normal
            if valor in (None, "agradecimento") and self._fala_nova():  # 25/11: she closed over a new sentence
                self._abertura["encerrar_ignorado"] = self._abertura.get("encerrar_ignorado", 0) + 1
                return []
            if valor == "agradecimento" and self.pendentes > 0:  # revisão final I1: a resposta do cérebro ainda vem
                return [] if e == AGUARDANDO else self._aguardar_cerebro()
            return self._fechar(agora, valor or "encerrar", som=None if e == CORTADA else "fim")
        if nome == "cerebro_entregando":
            if e != AGUARDANDO:
                return []
            if self.nao_perturbe:  # resposta atrasada = fala espontânea: calada (o bot registra no NDJSON)
                return self._fechar(agora, "nao_perturbe", som=None)
            acoes = self._fechar_mic_do_recado(self._abrir(agora, self._abertura["como_abriu"]))
            self._prazo = ("resposta", agora + self.a.resposta_max_s)
            return acoes
        return []

    def _reabrir(self, parou):
        """End of a recado (21/11): the mic opens as before v0.3.7 and the program learns the answer ended (the beam lock
        releases). `parou` = when she really stopped: the follow-up deadlines count from there."""
        self._recado, self._recado_fim, self._resposta_normal, self._completa = False, None, False, False
        herdada = []
        if self._detector_falando and not self._alguem_falando:  # 25/11: speech begun during the recado now counts
            self._alguem_falando, self._fala_desde = True, parou
            herdada = [("avisar_bot", "speech_started")] if (self.whisper or self.pi_turns) else []
        acoes = [("microfone", True), ("luz", "ouvindo")] + herdada + self._trata("ela_parou", parou, None)
        if ("fim_da_resposta", None) not in acoes:
            acoes.append(("fim_da_resposta", None))
        return acoes

    def _fechar_mic_do_recado(self, acoes):
        """v0.3.7: a resposta pendente sai com o microfone fechado (a palavra segue ouvida pela Pi) e a luz diz que ela
        está falando, não ouvindo (M4): fala no cômodo não é ouvida até o fim do recado."""
        self._recado, self._resposta_normal = True, False
        troca = (((("microfone", True)), ("microfone", False)), (("luz", "ouvindo"), ("luz", "falando")))
        acoes = [next((novo for velho, novo in troca if a == velho), a) for a in acoes]
        if not any(a[0] == "microfone" for a in acoes):  # 19/11: conectar abre o microfone; o recado já começa fechado
            i = next((n for n, a in enumerate(acoes) if a[0] == "conectar"), None)
            if i is not None:
                acoes.insert(i + 1, ("microfone", False))
        return acoes

    def _falando_segura(self, motivo):
        """Com o cérebro pendente (ou a resposta ainda não falada), quem está falando segura o microfone aberto; o
        próximo `fala_parou` rearma o prazo. Os prazos de resposta só seguravam com keep_listening_while_speaking."""
        return self._alguem_falando and (motivo not in ("resposta", "resposta_curta")
                                         or self.a.keep_listening_while_speaking)

    def _fala_nova(self):
        """25/11: someone began speaking after her last answer ended and is still speaking (sound already running
        when she stopped, such as TV, does not count)."""
        return (self._detector_falando and not self._ela_falando and self._abertura is not None
                and self._ela_parou_em is not None and self._detector_desde >= self._ela_parou_em)

    def _aguardar_cerebro(self):
        self.estado = AGUARDANDO
        self._prazo = None
        return [("som", "fim"), ("luz", "cerebro"), ("microfone", False)]

    def _tique(self, agora):
        if self._alarme and self.estado == ALARME and agora >= self._alarme["inicio"] + self.a.alarme_max_s:
            return self._encerrar_alarme(agora, "nao_atendido")
        if self.estado == TESTE_PALAVRA:
            return self._tique_teste(agora)
        if self.estado == PARADA and self._alarme is None:
            return self._proxima_da_fila(agora)
        if (self.estado in ABERTO and self._alguem_falando and self.pendentes == 0 and not self._ela_falando
                and self.a.speech_max_s > 0
                and self._fala_desde is not None and agora - self._fala_desde >= self.a.speech_max_s):
            return self._fechar(agora, "fala_longa")
        if not self._prazo or agora < self._prazo[1]:
            return []
        motivo, e = self._prazo[0], self.estado
        self._prazo = None
        if motivo == "music_wait":  # a música não começou a tempo: o alarme segue como um timer normal
            if self._alarme:
                self._alarme["music"] = False
            motivo = "nome_do_timer"
        if motivo == "nome_do_timer":  # 24/11: um aviso falado por timer, sem abrir conversa
            if self._alarme is None or self.estado != ALARME:
                return []
            self._alarme["aviso"] = True
            return [("aviso_timer", self._alarme["timer"])]
        # Com o cérebro pendente a conversa nunca fecha por prazo: volta a aguardar (06/10, resposta perdida).
        if self.pendentes > 0 and e in ABERTO and motivo in ("espera_inicial", "continuacao", "resposta",
                                                             "resposta_curta"):
            return [] if self._falando_segura(motivo) else self._aguardar_cerebro()
        # 19/10: a resposta devolvida e não falada segura a conversa como um pendente, só até cerebro_fim_s. O
        # microfone fecha neste mesmo instante (aguardar), então não fica aberto mais tempo do que antes.
        if (self._resposta_ate is not None and agora < self._resposta_ate and e in ABERTO
                and motivo in ("espera_inicial", "continuacao", "resposta", "resposta_curta")):
            if self._falando_segura(motivo):
                return []
            fim, self._resposta_ate = self._resposta_ate, None
            acoes = self._aguardar_cerebro()
            self._prazo = ("cerebro_fim", fim)
            return acoes
        if motivo == "resposta_curta":  # D1/D3
            return [] if self._alguem_falando else self._fechar(agora, "calada")
        if motivo == "espera_inicial":
            return [] if self._alguem_falando else self._fechar(agora, "sem_fala")
        if motivo == "continuacao":
            if self._alguem_falando:
                return []
            return self._fechar(agora, "silencio")
        if motivo == "cerebro_fim":
            return self._fechar(agora, "cerebro_concluido", som=None)
        if motivo == "resposta":
            return self._fechar(agora, "sem_resposta", som=None if e == CORTADA else "fim")
        return []
