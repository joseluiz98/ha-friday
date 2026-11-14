"""Maestro do satélite (spec 2026-10-05 §1): decide estados, sons, luz e microfone.

Lógica pura: recebe eventos com o horário (segundos, relógio monotônico) e devolve ações. Não toca em
hardware nem em rede, então é testado sem o Jabra (test_maestro.py).
"""
from dataclasses import dataclass

PARADA, CONECTANDO, OUVINDO, CORTADA, AGUARDANDO = "parada", "conectando", "ouvindo", "cortada", "aguardando"
ALARME = "alarme"  # spec 2026-10-12 §2: timer vencido tocando
CADASTRO = "cadastro"  # spec 2026-10-15 D15–D20: a Pi conduz a gravação do programa de aprendizagem de voz
TESTE_PALAVRA = "teste_palavra"  # spec 2026-11-05 §6.4: a Pi confere a palavra de ativação (duas vezes) na instalação
ABERTO = (CONECTANDO, OUVINDO)
TESTE_SEM_PALAVRA_S = 15.0  # sem nenhuma palavra por tanto tempo: "o microfone está mudo?" (uma vez)


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
    # Spec 2026-10-12: 1ª volta do alarme antes de a Friday falar o timer; tempo máximo tocando; quanto uma
    # entrega pode esperar na fila (o bot desiste em ~90 s).
    volta_alarme_s: float = 1.5
    alarme_max_s: float = 300.0
    fila_entrega_max_s: float = 90.0
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
        self._alarme = None  # {"timer", "inicio", "atendido"} do alarme em curso
        self._fila_alarmes, self._fila_entregas = [], []  # timers vencidos esperando; (id, instante) de entregas
        self._cadastro = None  # {"nome", "id"} agendado na conversa ou em gravação; fora de _zerar
        self._teste = None  # {"inicio", "fim", "contagem", "avisou"} do teste da palavra; fora de _zerar
        # spec 2026-11-08 (rede): a cadeia de recuperação esgotou; luz de erro fixa em repouso. Fora de _zerar.
        self.rede_esgotada = False
        # o mudo foi apertado com a rede esgotada (tocou o som de erro): ao voltar a rede, desfaz o mudo
        self._mudo_na_falha = False
        self._zerar()

    def _zerar(self):
        self.estado = PARADA
        self.pendentes = 0
        self._prazo = None  # (motivo, instante)
        self._alguem_falando = False
        self._fala_desde = None  # R13: instante em que a fala contínua atual começou
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
                "cadastro": self._cadastro["nome"] if self._cadastro else None,
                "fila_entregas": len(self._fila_entregas), "net_exhausted": self.rede_esgotada}

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
        if self.mudo is not False:  # mudo físico (ou ainda desconhecido): o anel é do Jabra, não escrever
            acoes = [a for a in acoes if a[0] != "luz"]
        if self.whisper:  # D5: sem sons de início e fim (erro e os demais continuam)
            acoes = [a for a in acoes if a not in (("som", "inicio"), ("som", "fim"))]
        return acoes

    def campainha(self, entrega_id: str, agora: float):
        """POST /doorbell (spec 2026-10-12 §3): ("calado" | "na_fila" | "vou_abrir", ações)."""
        if self.nao_perturbe:
            return "calado", []
        if self.estado != PARADA or self._alarme is not None or self.mudo is None:
            self._fila_entregas.append((entrega_id, agora))
            return "na_fila", []
        return "vou_abrir", self._filtrar(self._abrir_entrega(agora, entrega_id))

    # ---- transições
    def _abrir(self, agora, como, whisper=False, wake_word=None):
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
            self.pendentes, self._ela_falando, self._alguem_falando = 0, False, False
            self._fala_desde = None
            if self.whisper:  # a captura começa o DetectorFala "falando" (a frase segue depois da palavra)
                self._alguem_falando, self._fala_desde = True, agora
            self.estado = CONECTANDO
            # 28/10: a palavra que abriu vai ao bot (Jarvis Logbook); sem palavra e sem sussurro = None, como antes
            extras = {k: v for k, v in (("whisper", self.whisper), ("wake_word", wake_word)) if v}
            acoes.append(("conectar", extras or None))
        else:
            self.estado = OUVINDO
            acoes.append(("microfone", True))
        return acoes

    def _fechar(self, agora, motivo, som="fim"):
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
        if como == "timer" and self._alarme:
            if self._alarme["atendido"]:
                return self._encerrar_alarme(agora, "atendido")
            if agora >= self._alarme["inicio"] + self.a.alarme_max_s:
                return self._encerrar_alarme(agora, "nao_atendido")
            self.estado, self._prazo = ALARME, None  # ninguém respondeu: o alarme volta até ser parado
            return [("alarme", "tocar"), ("luz", "alarme")]
        return []

    @property
    def musical_alarm(self) -> bool:
        """True enquanto o alarme em curso espera/pediu a música (R11)."""
        return bool(self._alarme and self._alarme.get("music"))

    def _iniciar_alarme(self, agora, timer):
        self._alarme = {"timer": timer, "inicio": agora, "atendido": False}
        self.estado = ALARME
        acoes = [("volume_alerta", True), ("alarme", "tocar"), ("luz", "alarme")]
        if timer.get("music"):  # R11: campainha + pedido de música; sem música em music_wait_s, segue como sempre
            self._alarme["music"] = True
            self._prazo = ("music_wait", agora + self.a.music_wait_s)
            acoes.append(("request_music", {"timer_id": timer.get("id"), "query": timer["music"]}))
        else:
            self._prazo = ("alarme_fala", agora + self.a.volta_alarme_s)
        return acoes

    def _encerrar_alarme(self, agora, como):
        t, inicio = self._alarme["timer"], self._alarme["inicio"]
        self._alarme, self.estado, self._prazo = None, PARADA, None
        r = {"id": t.get("id"), "timer": t.get("nome"), "como_parou": como, "tocou_s": round(agora - inicio, 1)}
        acoes = [("alarme", "parar"), ("volume_alerta", False), ("luz", self._repouso()), ("registrar_timer", r)]
        if como == "nao_atendido":
            acoes.append(("aviso_nao_atendido", t))
        return acoes

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

    def _abrir_timer(self, agora):
        return [("alarme", "pausar")] + self._abrir_propria(agora, "timer", {"timer": self._alarme["timer"]})

    def _abrir_entrega(self, agora, entrega_id):
        return [("volume_alerta", True), ("som", "lembrete")] + self._abrir_propria(
            agora, "entrega", {"entrega": entrega_id})

    def _proxima_da_fila(self, agora):
        if self._fila_alarmes:
            return self._iniciar_alarme(agora, self._fila_alarmes.pop(0))
        while self._fila_entregas:
            eid, desde = self._fila_entregas.pop(0)
            if agora - desde <= self.a.fila_entrega_max_s and not self.nao_perturbe and self.mudo is not None:
                return self._abrir_entrega(agora, eid)
        return []

    def _cortar(self, agora, motivo):
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
        if self.rede_esgotada:  # a luz de erro de rede vence o não perturbe
            return "rede_esgotada"
        return "nao_perturbe" if self.nao_perturbe else "parada"

    def _luz_do_estado(self):
        return {CONECTANDO: "ouvindo", OUVINDO: "ouvindo", AGUARDANDO: "cerebro", ALARME: "alarme",
                CADASTRO: "ouvindo", TESTE_PALAVRA: "ouvindo"}.get(
            self.estado, self._repouso())

    @staticmethod
    def _sussurro(valor):
        return bool(isinstance(valor, dict) and valor.get("whisper"))

    @staticmethod
    def _palavra(valor):
        return valor.get("wake_word") if isinstance(valor, dict) else None

    def _aviso(self, e, tipo):
        """D7: em conversa sussurrada aberta, o início/fim da fala vai ao bot (o Live roda sem VAD do servidor)."""
        # revisão final (20/10): só com a conexão aberta; o que aconteceu antes vai no `conectou`
        return [("avisar_bot", tipo)] if self.whisper and e == OUVINDO else []

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
                if e in (ALARME, CADASTRO, TESTE_PALAVRA):  # não toca por cima do alarme nem na gravação: fica pendente
                    self._som_np, som = som, None
                elif e != PARADA:  # conversa aberta: toca na hora e este som é o encerramento dela
                    self._sem_fim = True
            luz = [("luz", self._luz_do_estado())]
            return ([("som", som)] if som else []) + luz
        if nome == "rede_esgotada":  # spec 2026-11-08: vem do arquivo de estado do netwatch (programa principal)
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
            self._prazo = ("alarme_fala", max(agora, self._alarme["inicio"] + self.a.volta_alarme_s))
            return []
        if nome == "timer_venceu":
            if self._alarme is not None:
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
            return self._abrir_propria(agora, "apresentacao", {"presentation": valor})
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
        if e == ALARME:
            if nome == "botao":
                return self._encerrar_alarme(agora, "botao")
            if nome == "palavra" and self.mudo is False:
                return self._encerrar_alarme(agora, "palavra") + self._abrir(agora, "palavra", self._sussurro(valor),
                                                                           self._palavra(valor))
            return []
        if self._alarme and self._da_conversa("timer") and nome in ("fala_comecou", "botao"):
            # alguém respondeu ao timer: o alarme não volta. O `encerrar` NÃO entra aqui: sozinho ele é o modelo
            # decidindo (14/10: encerrou em 1,2 s sem falar); quem manda parar já falou antes (fala_comecou).
            self._alarme["atendido"] = True
        if nome == "botao" and valor == "vermelho":
            # Spec 2026-11-04: o vermelho corta tudo e fecha, em qualquer estado de conversa aberta. Parado, só o
            # verde abre. (Alarme e cadastro já foram tratados acima: qualquer cor os encerra como antes.)
            return self._cancelar(agora) if e in (CONECTANDO, OUVINDO, CORTADA, AGUARDANDO) else []
        if nome == "botao" and e in ABERTO and self._da_conversa("entrega"):
            return [("avisar_bot", "atendida")] + self._cortar(agora, "botao")
        if nome in ("cerebro_pendente", "ela_comecou", "ela_parou", "ferramenta", "musica") and e == PARADA:
            return []  # resto de uma conexão já descartada (revisão final I1)
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
            return []
        if nome in ("falha_conexao", "caiu"):
            if e == PARADA:
                return []
            # conversa do timer que falhou: o alarme volta sozinho, sem o som de erro por cima
            return self._fechar(agora, "falha", som=None if self._da_conversa("timer") else "erro")
        if nome == "fala_comecou":
            self._alguem_falando = True
            self._fala_desde = agora
            if self._prazo and self._prazo[0] in ("espera_inicial", "continuacao"):
                self._prazo = None
            return self._aviso(e, "speech_started")
        if nome == "fala_parou":
            self._alguem_falando = False
            self._fala_desde = None
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
            if self._mic_fechado:  # 04/11: ela vai responder: o microfone volta (interrupção e continuação)
                self._mic_fechado = False
                reabrir = [("microfone", True), ("luz", "ouvindo")]
            self._resposta_ate = None
            medida = []
            if self._t_fala_parou is not None and self._abertura is not None:  # D4
                medida = [("registrar_espera", {"espera_resposta": round(agora - self._t_fala_parou, 1),
                                                "como_abriu": self._abertura["como_abriu"]})]
                self._t_fala_parou = None
            if e == AGUARDANDO:  # resposta do cérebro chegando sem "cerebro_entregando" (dentro da janela)
                acoes = self._abrir(agora, self._abertura["como_abriu"])
                self._prazo = None
                return medida + acoes
            if self._prazo:
                self._prazo = None
            return medida + reabrir
        if nome == "musica":  # 04/11: resultado da consulta ao Sendspin, feita quando ela começou a responder
            self._musica_tocando = bool(valor)
            return []
        if nome == "ela_parou":
            self._ela_falando = False
            if self._cadastro is not None and e == OUVINDO:  # D15: a confirmação dela terminou: a Pi assume
                return self._fechar(agora, "aprendizagem", som=None)
            if e == OUVINDO and self.a.no_follow_up_while_music and self._musica_tocando:
                return self._fechar(agora, "musica", som="fim")
            if e == OUVINDO:
                self._prazo = ("continuacao", agora + self.a.continuacao_s)
                return []
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
            if valor == "agradecimento" and self.pendentes > 0:  # revisão final I1: a resposta do cérebro ainda vem
                return [] if e == AGUARDANDO else self._aguardar_cerebro()
            return self._fechar(agora, valor or "encerrar", som=None if e == CORTADA else "fim")
        if nome == "cerebro_entregando":
            if e != AGUARDANDO:
                return []
            if self.nao_perturbe:  # resposta atrasada = fala espontânea: calada (o bot registra no NDJSON)
                return self._fechar(agora, "nao_perturbe", som=None)
            acoes = self._abrir(agora, self._abertura["como_abriu"])
            self._prazo = ("resposta", agora + self.a.resposta_max_s)
            return acoes
        return []

    def _falando_segura(self, motivo):
        """Com o cérebro pendente (ou a resposta ainda não falada), quem está falando segura o microfone aberto; o
        próximo `fala_parou` rearma o prazo. Os prazos de resposta só seguravam com keep_listening_while_speaking."""
        return self._alguem_falando and (motivo not in ("resposta", "resposta_curta")
                                         or self.a.keep_listening_while_speaking)

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
            motivo = "alarme_fala"
        if motivo == "alarme_fala":
            if self.mudo is not False:  # com mudo ninguém responderia: o alarme segue
                return []
            return self._abrir_timer(agora)
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
