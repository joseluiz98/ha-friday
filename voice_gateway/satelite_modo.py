"""Modo satélite da voz (spec 2026-10-05 §2.2). Lógica pura, roda sem o Pipecat."""
import re
import time
import unicodedata

import local_config

DONO = local_config.owner()  # nome do dono da casa: fica na configuração local, nunca no repo

_ID = re.compile(r"^sat-[0-9a-f]{6}$")
MSG_ENTREGANDO = {"evento": "cerebro_entregando"}
MSG_ENCERRAR = {"evento": "encerrar"}
# 07/10: informação, não ordem nem frase pronta (regra de autonomia da Friday).
DESCRICAO_ENCERRAR = (
    "Encerra a conversa no satélite na hora: a sua fala é cortada, o microfone fecha e a conexão termina, "
    "sem esperar a continuação. Uma resposta do cérebro que ainda viria é descartada. Serve para quando "
    "quem fala pede para parar ou encerrar, e para quando você percebe que o que o microfone capta não é "
    "com você, por exemplo uma conversa entre pessoas na sala.")


def origem(body):
    """(True, id) se a conexão veio do satélite (request_data da oferta WebRTC), senão (False, None)."""
    if not isinstance(body, dict) or body.get("origem") != "satelite":
        return False, None
    sid = str(body.get("satelite_id") or "")
    return True, (sid if _ID.match(sid) else "sat-desconhecido")


_COMODO = re.compile(r"^[\w À-ÿ',-]{1,40}$")


def comodo(body):
    """Cômodo do satélite (config `comodo` da Pi, 06/10). Vai no prompt: só nome curto, sem pontuação de frase."""
    c = str((body or {}).get("comodo") or "").strip() if isinstance(body, dict) else ""
    return c if _COMODO.match(c) else None


def nao_perturbe(body) -> bool:
    """Estado do não perturbe que a Pi manda na oferta (D11)."""
    return isinstance(body, dict) and body.get("nao_perturbe") is True


def msg_nao_perturbe(ligado: bool) -> dict:
    return {"evento": "nao_perturbe", "ligado": bool(ligado)}


def resultado_nao_perturbe(ligado: bool, antes: bool) -> dict:
    """Spec 2026-10-20 (A): o resultado da ferramenta diz o fato, mudou ou não. A Pi é a dona do estado."""
    ligado, antes = bool(ligado), bool(antes)
    estado = "ligado" if ligado else "desligado"
    if ligado != antes:
        situacao = f"O não perturbe deste satélite foi {estado}."
    else:
        situacao = f"O não perturbe deste satélite já estava {estado}; nada mudou."
    return {"ok": True, "nao_perturbe": ligado, "mudou": ligado != antes, "situacao": situacao}


# 08/10 (Task 12): informação, não ordem nem frase pronta.
DESCRICAO_NAO_PERTURBE = (
    "Liga ou desliga o não perturbe deste satélite. Com ele ligado, você não fala por conta própria: "
    "respostas do cérebro que chegariam depois da conversa e, no futuro, avisos ficam registrados em vez "
    "de falados. A palavra de ativação e os pedidos diretos seguem normais. A escolha fica gravada no "
    "satélite até alguém mudar.")
MOTIVO_NAO_PERTURBE = "calada pelo não perturbe"


# D27: "aprende a minha voz" compete com "lembrar de algo → cérebro" na instrução de sistema; o fato diz onde mora.
FATO_APRENDIZAGEM = ("Este satélite tem um programa de aprendizagem de voz, local, que ensina o satélite a reconhecer o "
                     "\"Friday\" de cada pessoa da casa (ferramentas aprendizagem_de_voz e comecar_aprendizagem_de_voz).")


def music_fact(player, estado) -> str:
    """Spec 2026-10-21 §6.4: fato de música da abertura. Sem `player` no cadastro (2º satélite, cadastro antigo)
    não há frase nenhuma. `estado` é o JSON do HA (/api/states) ou None (HA lento/fora: o fato sai sem o estado)."""
    if not player or not isinstance(player, str):
        return ""
    t = (f"Este satélite toca música do Music Assistant pelo player {player}. O volume do satélite vale para a música "
         "e para a sua voz (ferramenta volume_do_satelite). A música abaixa sozinha enquanto se conversa, e a letra "
         "de uma música tocando pode chegar ao seu microfone sem ser um pedido de quem está na sala.")
    if isinstance(estado, dict):
        if estado.get("state") == "playing":
            a = estado.get("attributes") or {}
            quem = " — ".join(x for x in (a.get("media_title"), a.get("media_artist")) if x)
            t += f" O que está tocando agora: {quem}." if quem else " Tem música tocando agora."
        elif estado.get("state") in ("paused", "idle", "off", "standby"):
            t += " No momento nenhuma música está tocando."
    return t


def contexto_satelite(sid: str, comodo_: str = None, nao_perturbe_: bool = False, music_fact: str = "") -> str:
    # Informação, não ordem (regra de autonomia da Friday): ela decide como lidar.
    onde = f"Este satélite fica no cômodo {comodo_} da casa. " if comodo_ else ""
    return (f"Esta conversa vem do satélite de voz {sid}, um alto-falante com microfone num cômodo da casa. "
            + onde + "Quem fala pode ser qualquer pessoa da casa, não necessariamente o " + DONO + ". "
            "A palavra de ativação às vezes dispara sozinha, então o microfone pode captar conversas que não "
            "são com você. "
            "Durante a conversa, as pessoas no cômodo também falam umas com as outras, e essas falas chegam a "
            "você do mesmo jeito que os pedidos. Uma fala dirigida a outra pessoa não é um pedido a você. Quando "
            "você não responde, nada acontece: o satélite espera alguns segundos e fecha a conversa sozinho. "
            "Uma pergunta curta resolve uma dúvida real sobre se a fala foi com você; sem dúvida, ela é "
            "desnecessária. "
            + f"O não perturbe deste satélite está {'ligado' if nao_perturbe_ else 'desligado'}. "
            + FATO_APRENDIZAGEM
            + (" " + music_fact if music_fact else ""))


def pedido_com_origem(pedido: str, sid: str) -> str:
    return f"[pedido vindo do satélite de voz {sid}] {pedido}"


def msg_pendentes(n: int) -> dict:
    return {"evento": "cerebro_pendente", "pendentes": int(n)}


# ---- spec 2026-10-15: escuta depois da resposta (itens 1 e 2)
MSG_ENCERRAR_AGRADECIMENTO = {"evento": "encerrar", "motivo": "agradecimento"}  # D6
FORA_DO_CONTADOR = {"perguntar_ao_cerebro"}  # já tem o cerebro_pendente


def msg_ferramenta(n: int) -> dict:
    return {"evento": "ferramenta", "em_andamento": int(n)}


class ContadorFerramentas:
    """D2: chamadas de ferramenta em andamento, por tool_call_id. Devolve o total novo quando muda, senão None."""

    def __init__(self):
        self._ids = set()

    def comecou(self, nome, cid):
        if nome in FORA_DO_CONTADOR or cid in self._ids:
            return None
        self._ids.add(cid)
        return len(self._ids)

    def terminou(self, cid):
        if cid not in self._ids:
            return None
        self._ids.discard(cid)
        return len(self._ids)


# D5 (lista aprovada pelo dono em 15/10). Fora de propósito: "tá bom", "beleza", "entendi", "ok"… podem ser "sim".
AGRADECIMENTOS = ("muito obrigado", "muito obrigada", "por enquanto e so", "era so isso", "e so isso", "so isso",
                  "obrigado", "obrigada", "brigado", "brigada", "valeu", "falou", "tchau", "ate mais")
_CHAMADAS = ("sexta feira", "friday", "freddy", "freddie")


def _normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto.lower())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return " ".join("".join(ch if "a" <= ch <= "z" else " " for ch in t).split())


def so_agradecimento(texto) -> bool:
    """A frase inteira é agradecimento ou despedida (depois de tirar a chamada do começo)."""
    if not isinstance(texto, str):
        return False
    t = _normalizar(texto)
    for c in _CHAMADAS:
        if t == c or t.startswith(c + " "):
            t = t[len(c):].strip()
            break
    if not t:
        return False
    while t:
        for frase in AGRADECIMENTOS:  # as mais longas primeiro
            if t == frase or t.startswith(frase + " "):
                t = t[len(frase):].strip()
                break
        else:
            return False
    return True


class FimPorAgradecimento:
    """D5: junta a transcrição do usuário de cada turno; quando ela para de falar, diz se o turno foi só um
    agradecimento. A transcrição chega em pedaços ("Obri" + "gado."), então junta sem espaço, como o registro."""

    def __init__(self):
        self._partes = []

    def usuario(self, texto):
        self._partes.append(texto)

    def ela_parou(self) -> bool:
        texto, self._partes = "".join(self._partes), []
        return so_agradecimento(texto)


# ---- 04/11: turno só de comando da casa fecha a conversa (continuação no estilo Alexa)
MSG_ENCERRAR_COMANDO_CASA = {"evento": "encerrar", "motivo": "comando_casa"}
HOME_COMMAND_PREFIXES = ("intent__HassTurnOn", "intent__HassTurnOff", "light__HassLightSet",
                         "climate__HassClimateSetTemperature", "media_player__HassMedia",
                         "media_player__HassSetVolume", "media_player__play_media", "script__tv_", "script__music_")
HOME_COMMAND_NAMES = ("volume_do_satelite", "nao_perturbe")
HOME_COMMAND_EXCLUDED = ("script__music_now_playing",)
HOME_NEUTRAL = ("homeassistant__GetLiveContext",)  # só lê o estado da casa: não conta nem contra


def e_comando_da_casa(nome: str) -> bool:
    if nome in HOME_COMMAND_EXCLUDED:
        return False
    return nome in HOME_COMMAND_NAMES or nome.startswith(HOME_COMMAND_PREFIXES)


class TurnoDeCasa:
    """As ferramentas que ela chamou desde a última fala do usuário. Quando ela para de falar, diz se o turno foi só
    de comandos da casa (há pelo menos um e todos são comandos, ignorando as leituras de estado)."""

    def __init__(self):
        self._nomes = []

    def usuario(self):
        self._nomes = []

    def ferramenta(self, nome):
        self._nomes.append(str(nome))

    def ela_parou(self) -> bool:
        nomes = [n for n in self._nomes if n not in HOME_NEUTRAL]
        self._nomes = []
        return bool(nomes) and all(e_comando_da_casa(n) for n in nomes)


def fim_de_fala_ms(body):
    """Pausa de fim de fala pedida pela Pi (0,5–2,0 s) em ms para a VAD do Gemini; só do satélite, senão None."""
    if not (isinstance(body, dict) and body.get("origem") == "satelite"):
        return None
    v = body.get("end_of_speech_pause_s")
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0.5 <= v <= 2.0:
        return None
    return round(v * 1000)


def mensagem_inicial(eh_satelite: bool):
    """Mensagem do pontapé da conversa. O LLMRunFrame vai SEMPRE: o Gemini Live do Pipecat só aceita áudio
    depois do contexto inicial (medido em 06/10: sem ele o áudio do satélite era descartado em silêncio).
    No satélite, contexto vazio = pronto para ouvir, sem fala; o pedido gravado chega e ela responde."""
    if eh_satelite:
        return None
    return {"role": "developer", "content": "Cumprimente o " + DONO + " em uma frase curta."}


def orientacao_em_andamento(eh_satelite: bool) -> str:
    """Texto devolvido à voz quando o cérebro passa para o segundo plano (06/10: no satélite ela avisava
    duas vezes; lá a espera já é sinalizada por luz e som). Informação, não frase pronta."""
    if eh_satelite:
        return ("O cérebro ainda está trabalhando nisso e a resposta chega depois. No satélite, a espera já é "
                "sinalizada por luz e som; não é preciso avisar.")
    return ("O cérebro ainda está trabalhando nisso e a resposta chega depois. Diga ao " + DONO + ", "
            "numa frase curta, que vai avisar assim que tiver, e siga a conversa.")


def janela_do_cerebro(eh_satelite: bool, padrao: float, satelite: float) -> float:
    """Quanto a função espera o cérebro antes de mandá-lo ao segundo plano. No satélite a conversa já está
    fechada durante a espera (luz e som sinalizam): esperar a resposta inteira evita o 2º aviso (06/10)."""
    return satelite if eh_satelite else padrao


# ---- spec 2026-10-12: volume, timers e conversas abertas pela Pi (entrega de mensagem, timer vencido)
# Informação, não ordem nem frase pronta (regra de autonomia da Friday).
DESCRICAO_VOLUME = (
    "Muda o volume do alto-falante deste satélite, o mesmo por onde você fala. Use só um destes: numero, na escala "
    "de 1 a 10 das Echos (10 é o máximo); percentual, de 0 a 100; ou ajuste, 'mais' ou 'menos', que sobe ou desce um "
    "degrau a partir do volume atual. O volume vale para a música e para a voz deste satélite. A Echo do cômodo e a TV "
    "são outros aparelhos, com volume próprio pelas ferramentas de media_player. Quando o pedido de volume não cita aparelho, quem pede está falando do volume que "
    "ouve neste satélite. O satélite confirma sozinho, com um bip no volume novo e o anel de LEDs; uma confirmação "
    "falada não acrescenta nada para quem pediu.")
_TIMERS_BASE = ("Timers deste satélite. A contagem é feita no próprio satélite, que toca um alarme na hora certa mesmo sem "
                "internet. Cada timer tem um id, devolvido ao criar e ao consultar, mesmo quando não tem nome. ")
DESCRICAO_CRIAR_TIMER = (
    _TIMERS_BASE + "Cria um timer de duracao_s segundos, até 24 horas; nome é opcional, como 'arroz'. Hora marcada "
    "('às 15h', 'amanhã') ou mais de 24 horas é lembrete, e lembrete é com o cérebro. music é opcional, até 80 "
    "caracteres: o que tocar quando o timer vencer (título, artista, álbum ou playlist), para acordar com música. "
    "O satélite toca a campainha, pede a música e, se ela não começar em cerca de 10 segundos, o alarme segue como o "
    "de qualquer timer.")
DESCRICAO_CONSULTAR_TIMERS = (
    _TIMERS_BASE + "Lista os timers ativos, cada um com id, nome, duração e quanto falta. Sem filtro devolve todos; "
    "nome ou id limitam a lista.")
DESCRICAO_CANCELAR_TIMER = (
    _TIMERS_BASE + "Cancela timers: pelo id (um timer só), pelo nome, ou todos=true (todos os timers); sem nenhum dos "
    "três, cancela o único ativo. Se houver mais de um timer ativo (ou mais de um com o mesmo nome) e o pedido não disser "
    "qual, nada é cancelado e a resposta traz a lista dos candidatos, cada um com id, nome, duração e quanto falta.")
ACAO_DA_FERRAMENTA = {"criar_timer": "criar", "consultar_timers": "consultar", "cancelar_timer": "cancelar"}
_ID_ENTREGA = re.compile(r"^[a-z0-9]{8,32}$")
_ID_TIMER = re.compile(r"^t[0-9a-f]{6}$")
_NOME_TIMER = re.compile(r"^[\w À-ÿ'-]{1,40}$")


def _inteiro(x):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or int(x) != x:
        raise ValueError("número inteiro esperado")
    return int(x)


def args_volume(a: dict) -> dict:
    dados = {k: a[k] for k in ("numero", "percentual", "ajuste") if isinstance(a, dict) and a.get(k) is not None}
    if len(dados) != 1:
        raise ValueError("informe só um: numero, percentual ou ajuste")
    if "numero" in dados:
        n = _inteiro(dados["numero"])
        if not 1 <= n <= 10:
            raise ValueError("volume de 1 a 10")
        return {"numero": n}
    if "percentual" in dados:
        p = _inteiro(dados["percentual"])
        if not 0 <= p <= 100:
            raise ValueError("percentual de 0 a 100")
        return {"percentual": p}
    if dados["ajuste"] not in ("mais", "menos"):
        raise ValueError("ajuste é 'mais' ou 'menos'")
    return {"ajuste": dados["ajuste"]}


def args_timer(a: dict) -> dict:
    a = a if isinstance(a, dict) else {}
    acao, nome = a.get("acao"), a.get("nome")
    nome = nome.strip() if isinstance(nome, str) else ""
    if acao == "criar":
        if a.get("duracao_s") is None:
            raise ValueError("criar precisa de duracao_s")
        r = {"acao": "timer_criar", "duracao_s": _inteiro(a["duracao_s"])}
    elif acao == "consultar":
        r = {"acao": "timer_consultar"}
    elif acao == "cancelar":
        r = {"acao": "timer_cancelar"}
        if a.get("todos") is True:
            r["todos"] = True
    else:
        raise ValueError("acao é 'criar', 'consultar' ou 'cancelar'")
    if nome:
        r["nome"] = nome
    music = a.get("music")
    if music is not None:
        if acao != "criar":
            raise ValueError("music só vale para criar")
        if not isinstance(music, str) or "\n" in music or "\r" in music or not 1 <= len(music.strip()) <= 80:
            raise ValueError("music: texto de 1 a 80 caracteres, numa linha")
        r["music"] = music.strip()
    tid = a.get("id")
    if tid is not None:
        if acao not in ("consultar", "cancelar"):
            raise ValueError("id só vale para consultar e cancelar")
        if not isinstance(tid, str) or not _ID_TIMER.match(tid):
            raise ValueError("id de timer inválido; use o que o timer devolveu")
        r["timer_id"] = tid  # o `id` do comando é o do envelope (msg_comando)
    return r


def args_do_timer(ferramenta: str, a: dict) -> dict:
    """A ação é o nome da ferramenta (criar_timer, consultar_timers, cancelar_timer): nenhum parâmetro a repete. Um `acao`
    escrito pelo modelo é ignorado. Medido em 14/10: com `acao` como enum, 25% das chamadas vinham inválidas."""
    if ferramenta not in ACAO_DA_FERRAMENTA:
        raise ValueError(f"ferramenta de timer desconhecida: {ferramenta}")
    return args_timer(dict(a if isinstance(a, dict) else {}, acao=ACAO_DA_FERRAMENTA[ferramenta]))


def msg_comando(cid: str, comando: dict) -> dict:
    return dict({"evento": "comando", "id": cid}, **comando)


def resultado_volume(resposta) -> dict:
    if not isinstance(resposta, dict):
        return {"aplicado": False, "erro": "o satélite não confirmou"}
    if not resposta.get("ok"):
        return {"aplicado": False, "erro": resposta.get("erro", "falhou")}
    return {"aplicado": True, "volume": resposta.get("volume"), "posicao": resposta.get("posicao"),
            "percentual": resposta.get("percentual"), "confirmacao": "o satélite confirmou com som e luz"}


class PortaoDeFala:
    """Depois de um volume aplicado, o satélite já confirmou com bip e anel (D1). O Gemini 3.8 ainda gera um turno e fala
    o marcador "<no speech>{pause}" (visto em 13/10 no registro de conversas), que não pode ir ao alto-falante. O mesmo
    marcador aparece às vezes no 1º turno de uma conversa aberta pela Pi (timer ou entrega, visto na API em 14/10).
    O portão segura o áudio do primeiro turno depois do volume e decide pelo texto que o Gemini manda junto:
    marcador = descarta o turno; qualquer outra coisa = solta tudo. Na dúvida (fim do turno ou espera demais) a fala vale.
    `tratar(tipo, texto)` recebe "audio", "texto" ou "fim" e responde "passar", "segurar", "soltar" (libera o que foi
    segurado e este quadro) ou "descartar"; quem guarda os quadros é o processador do Pipecat."""

    JANELA_S = 5.0       # quanto tempo o portão espera o turno começar
    ESPERA_MAX_S = 2.0   # quanto tempo segura áudio sem texto que decida
    MARCADOR = "<no speech>"

    def __init__(self, agora=time.monotonic):
        self._agora = agora
        self._estado, self._ate, self._desde, self._texto = "ocioso", 0.0, 0.0, ""

    def armar(self):
        self._estado, self._ate, self._texto = "esperando", self._agora() + self.JANELA_S, ""

    def _soltar(self):
        self._estado = "ocioso"
        return "soltar"

    def tratar(self, tipo: str, texto: str = "") -> str:
        if self._estado == "esperando" and self._agora() >= self._ate:
            self._estado = "ocioso"
        if self._estado == "ocioso":
            return "passar"
        if self._estado == "descartando":
            if tipo == "fim":
                self._estado = "ocioso"
            return "descartar"
        if tipo == "fim":
            if self._estado == "esperando":
                return "passar"                      # fim de um turno anterior ao volume
            return self._soltar()
        if self._estado == "decidindo" and self._agora() - self._desde > self.ESPERA_MAX_S:
            return self._soltar()
        if tipo == "audio":
            if self._estado == "esperando":
                self._estado, self._desde = "decidindo", self._agora()
            return "segurar"
        self._texto += texto                          # tipo == "texto"
        if self._estado == "esperando":
            self._estado, self._desde = "decidindo", self._agora()
        visto = self._texto.lstrip().lower()
        if visto.startswith(self.MARCADOR):
            self._estado = "descartando"
            return "descartar"
        if self.MARCADOR.startswith(visto):
            return "segurar"                          # ainda pode ser o marcador
        return self._soltar()


def resultado_timer(resposta) -> dict:
    if not isinstance(resposta, dict):
        return {"ok": False, "erro": "o satélite não confirmou"}
    return {k: v for k, v in resposta.items() if k != "id"}


def entrega(body):
    eid = body.get("entrega") if isinstance(body, dict) else None
    return eid if isinstance(eid, str) and _ID_ENTREGA.match(eid) else None


def timer_vencido(body):
    t = body.get("timer") if isinstance(body, dict) else None
    if not isinstance(t, dict):
        return None
    d = t.get("duracao_s")
    if isinstance(d, bool) or not isinstance(d, int):
        return None
    nome = t.get("nome")
    nome = nome.strip() if isinstance(nome, str) and _NOME_TIMER.match(nome.strip()) else None
    return {"nome": nome, "duracao_s": d}


def rotulo_conexao(eh_satelite, sid, comodo_, entrega_id, timer, apresentacao=False) -> str:
    if not eh_satelite:
        return "página"
    base = f"satélite {sid} ({comodo_})"
    if apresentacao:
        return f"apresentação {base}"
    if entrega_id:
        return f"entrega {entrega_id} {base}"
    if timer:
        return f"timer {base}"
    return base


def duracao_falada(s: int) -> str:
    def um(n, sing, plur):
        return f"{n} {sing if n == 1 else plur}"
    h, m, seg = s // 3600, (s % 3600) // 60, s % 60
    partes = [um(h, "hora", "horas")] if h else []
    if m:
        partes.append(um(m, "minuto", "minutos"))
    if seg and not h:
        partes.append(um(seg, "segundo", "segundos"))
    return " e ".join(partes) or "0 segundos"


# Spec 2026-10-15 D25: o motivo de cada tipo de entrega diz a fonte verdadeira. canal_casa aceita estes tipos.
MOTIVO_DA_ENTREGA = {
    "lembrete": "chegou a hora de um lembrete do cérebro",
    "aviso": "chegou um aviso do cérebro",
    "aprendizagem": "chegou o resultado do programa de aprendizagem de voz deste satélite",
}


# D26 (15/10): medido com 12 tentativas no Gemini Live. Sem este fato a abertura do resultado ficou calada em 2 de 3;
# com ele, 12/12 falaram. Lembrete e aviso ficam como estavam (20/20).
FATO_DA_ENTREGA = {"aprendizagem": " O resultado ainda não foi contado a ninguém neste satélite."}


def mensagem_de_abertura(texto_entrega, timer, tipo_entrega="lembrete"):
    """Conversa aberta pela Pi: em vez do contexto vazio, a Friday recebe o que aconteceu e fala primeiro."""
    if texto_entrega:
        # 15/10: dizer que o som "já tocou" sem dizer que a mensagem não foi falada deixava o 1º turno em
        # "<no speech>{pause}" em até 37% (API); com o fato abaixo, 20/20 falaram. Cada tipo nomeia a sua fonte (D25).
        motivo = MOTIVO_DA_ENTREGA.get(tipo_entrega, MOTIVO_DA_ENTREGA["aviso"])
        return {"role": "developer", "content": (
            f"Esta conversa foi aberta pelo satélite porque {motivo}: «{texto_entrega}». O satélite tocou o som de "
            "aviso; a mensagem ainda não foi falada. Ninguém falou nada ainda." + FATO_DA_ENTREGA.get(tipo_entrega, ""))}
    if timer:
        qual = f"o timer «{timer['nome']}»" if timer.get("nome") else "um timer (criado sem nome)"
        return {"role": "developer", "content": (
            f"Esta conversa foi aberta pelo satélite porque terminou {qual} deste satélite, de "
            f"{duracao_falada(timer['duracao_s'])}. O alarme está em pausa enquanto a conversa dura e volta a tocar "
            "se ninguém responder. Ninguém falou nada ainda.")}
    return None


# ---- spec 2026-10-15: programa de aprendizagem de voz (item 4). Textos: fatos, sem ordens (skill designing-for-friday).
DESCRICAO_APRENDIZAGEM_DE_VOZ = (
    "Programa de aprendizagem de voz deste satélite: ensina o satélite a reconhecer o \"Friday\" dito por uma pessoa da "
    "casa, para a palavra acordar com quem mora aqui e não com outras vozes. Vale para qualquer jeito de pedir isso. "
    "Devolve o roteiro da gravação e as pessoas já cadastradas. Esta chamada não grava nada.")
DESCRICAO_COMECAR_APRENDIZAGEM = (
    "Começa a gravação do programa de aprendizagem de voz para a pessoa com este nome. A conversa fecha quando a sua "
    "fala termina, e o satélite conduz a gravação sozinho, com falas gravadas e bipes; a pessoa precisa estar perto "
    "do satélite. Só funciona depois de aprendizagem_de_voz nesta conversa.")
ROTEIRO_APRENDIZAGEM = [
    "o satélite toca uma fala gravada explicando o que fazer",
    "dez bipes, um a cada 6 segundos; a cada bipe a pessoa diz Friday uma vez",
    "na metade, uma fala gravada pede para mudar de lugar, uns 3 metros",
    "depois, 30 segundos de fala livre, sem dizer a palavra",
    "no fim, uma fala gravada avisa que o treino começou; o resultado vem falado neste satélite e por Telegram ao " + DONO,
]
ERRO_SEM_ROTEIRO = {"ok": False, "erro": "o roteiro ainda não foi passado nesta conversa"}


def resultado_comando(resposta) -> dict:
    """Resposta da Pi sem o id do comando (o envelope é do canal, não da voz)."""
    if not isinstance(resposta, dict):
        return {"ok": False, "erro": "o satélite não confirmou"}
    return {k: v for k, v in resposta.items() if k != "id"}


def roteiro_aprendizagem(resposta) -> dict:
    vozes = resposta.get("vozes") if isinstance(resposta, dict) and resposta.get("ok") else None
    return {
        "roteiro": ROTEIRO_APRENDIZAGEM,
        "gravacao_minutos": 2,
        "treino_minutos": "de 5 a 20",
        "antes_de_comecar": "falta o nome de quem vai gravar e a concordância dessa pessoa",
        "gravar_de_novo_o_mesmo_nome": "substitui o cadastro anterior dessa pessoa, se o novo for aprovado",
        "botao_do_satelite": "cancela a gravação",
        "cadastrados": vozes if vozes is not None else "o satélite não respondeu",
    }


class TravaAprendizagem:
    """D15: comecar_aprendizagem_de_voz só vale depois de aprendizagem_de_voz na mesma conversa."""

    def __init__(self):
        self.permite = False

    def passou(self):
        self.permite = True


def whisper(body) -> bool:
    """Conversa sussurrada (spec 2026-10-20 D6): a Pi manda `whisper: true` na oferta, só para o satélite."""
    return isinstance(body, dict) and body.get("origem") == "satelite" and body.get("whisper") is True


def wake_word(body) -> str | None:
    """28/10: nome da palavra que abriu a conversa (`friday`, `sexta_feira`), mandado pela Pi na oferta. Vai para o
    1º turno do registro (Jarvis Logbook). Só do satélite e só um nome curto em minúsculas; o resto vira None."""
    if not (isinstance(body, dict) and body.get("origem") == "satelite"):
        return None
    w = body.get("wake_word")
    return w if isinstance(w, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,31}", w) else None


# ---- 27/10: promessa de consulta sem chamada. Ela disse "Espera aí que eu já vejo quem vai dirigir…" e o turno
# acabou sem ferramenta nenhuma; nada em andamento, a Pi fechou por silêncio 5 s depois e a resposta nunca existiu.
PROMESSAS = ("deixa eu ver", "deixe me ver", "ja vejo", "vou ver", "vou verificar", "vou pesquisar", "vou procurar",
             "vou checar", "vou consultar", "vou perguntar", "ja te digo", "ja te falo", "ja te conto", "ja verifico",
             "um momento", "um instante", "so um minuto", "espera ai", "espere ai", "espera um pouco")
AVISO_PROMESSA_SEM_CHAMADA = (
    "[Aviso do bot da voz; não é fala do " + DONO + "] A sua última fala anunciou uma consulta, mas nenhuma ferramenta "
    "foi chamada nesse turno: nada foi pedido ao cérebro e nenhuma resposta está a caminho.")


def promete_consulta(texto) -> bool:
    if not isinstance(texto, str):
        return False
    t = f" {_normalizar(texto)} "
    return any(f" {p} " in t for p in PROMESSAS)


class PromessaSemChamada:
    """Um turno dela que prometeu consulta e não teve chamada de ferramenta. A chamada pode sair um pouco antes do
    áudio (MARGEM_S) ou logo depois da fala (o bot confere de novo com sem_chamada_desde). Um aviso por fala do dono."""

    MARGEM_S = 2.0

    def __init__(self):
        self._inicio, self._falas, self._chamadas, self._avisou = None, [], [], False

    def usuario(self):
        self._avisou = False

    def chamada(self, t):
        self._chamadas.append(t)

    def ela_comecou(self, t):
        if self._inicio is None:
            self._inicio = t

    def ela(self, texto):
        self._falas.append(texto)

    def ela_parou(self, t) -> bool:
        inicio = self._inicio if self._inicio is not None else t
        texto, self._falas, self._inicio = "".join(self._falas), [], None
        houve_chamada = any(c >= inicio - self.MARGEM_S for c in self._chamadas)
        self._chamadas = [c for c in self._chamadas if c >= t]  # sem_chamada_desde só olha daqui em diante
        return not (self._avisou or houve_chamada) and promete_consulta(texto)

    def sem_chamada_desde(self, t) -> bool:
        return not any(c >= t for c in self._chamadas)

    def avisou(self):
        self._avisou = True


# ---- spec 2026-11-05 §6.4: apresentação de um satélite recém-instalado. Textos: fatos, sem ordens, sem frase pronta
# (skill designing-for-friday). A conversa é aberta pela Pi (como as entregas) e a Friday fala primeiro.
_ID_AREA = re.compile(r"^[a-z0-9_]{1,64}$")
_ETAPA = re.compile(r"^[a-z_]{1,30}$")
_NOME_CURTO = re.compile(r"^[\w À-ÿ',-]{1,40}$")  # nomes de área e de pessoa vão ao prompt: nome curto, sem pontuação de frase


def apresentacao(body):
    """`presentation` da oferta da Pi: {areas:[{id,name}], known_voices:[nomes], failed_steps:[etapas], wake_word_ok}.
    Só do satélite; qualquer campo fora do formato derruba o conjunto (None = conversa comum)."""
    if not isinstance(body, dict) or body.get("origem") != "satelite":
        return None
    p = body.get("presentation")
    if not isinstance(p, dict):
        return None
    areas, vozes, etapas, ok = p.get("areas"), p.get("known_voices"), p.get("failed_steps"), p.get("wake_word_ok")
    if not (isinstance(areas, list) and isinstance(vozes, list) and isinstance(etapas, list) and isinstance(ok, bool)):
        return None
    if len(areas) > 60 or len(vozes) > 20 or len(etapas) > 10:
        return None
    limpas = []
    for a in areas:
        if not (isinstance(a, dict) and isinstance(a.get("id"), str) and _ID_AREA.match(a["id"])
                and isinstance(a.get("name"), str) and _NOME_CURTO.match(a["name"])):
            return None
        limpas.append({"id": a["id"], "name": a["name"]})
    if not all(isinstance(v, str) and _NOME_CURTO.match(v) for v in vozes):
        return None
    if not all(isinstance(e, str) and _ETAPA.match(e) for e in etapas):
        return None
    return {"areas": limpas, "known_voices": list(vozes), "failed_steps": list(etapas), "wake_word_ok": ok}


def mensagem_de_abertura_apresentacao(fatos: dict) -> dict:
    """Abertura da conversa de apresentação: o que é verdade agora. O modelo decide o que dizer."""
    areas = ", ".join(f"{a['name']} (id {a['id']})" for a in fatos["areas"]) or "nenhuma área cadastrada no Home Assistant"
    vozes = ", ".join(fatos["known_voices"]) or "Nenhuma voz cadastrada ainda"
    falhas = ", ".join(fatos["failed_steps"]) or "Nenhuma etapa da instalação falhou"
    palavra = "deu certo" if fatos["wake_word_ok"] else "não deu certo"
    return {"role": "developer", "content": (
        "Esta conversa foi aberta pelo satélite porque ele acabou de ser instalado (recém-instalado) e ainda não sabe "
        f"em que cômodo está. As áreas da casa são: {areas}. "
        f"Vozes que a F.R.I.D.A.Y. já conhece: {vozes}. "
        f"Etapas da instalação que falharam: {falhas}. "
        f"O teste da palavra de ativação {palavra}. "
        "O satélite tem a ferramenta definir_comodo, que grava a área escolhida. Nada foi falado ainda neste satélite. "
        "Ninguém falou nada ainda.")}


DESCRICAO_DEFINIR_COMODO = (
    "Grava a área do Home Assistant em que este satélite está; o nome do satélite passa a ser o da área. Existe só "
    "nesta conversa de instalação. Recebe o id de uma das áreas da lista da abertura; um id fora da lista não muda "
    "nada e devolve as áreas válidas. Devolve o nome da área gravada.")


def definir_comodo(fatos: dict, area_id):
    """(resultado, comando para a Pi ou None). Confere o id contra a lista recebida; nada muda se for desconhecido."""
    for a in fatos["areas"]:
        if isinstance(area_id, str) and a["id"] == area_id:
            return {"ok": True, "area": a["name"]}, {"acao": "set_area", "area_id": a["id"]}
    return {"ok": False, "erro": "área desconhecida", "areas": fatos["areas"]}, None


def resultado_definir_comodo(fatos: dict, area_id, resposta) -> dict:
    """Junta o que a Pi respondeu ao set_area com o nome da área."""
    resultado, comando = definir_comodo(fatos, area_id)
    if comando is None:
        return resultado
    if not isinstance(resposta, dict):
        return {"ok": False, "erro": "o satélite não confirmou"}
    return resultado if resposta.get("ok") else {"ok": False, "erro": resposta.get("erro", "o satélite recusou")}
