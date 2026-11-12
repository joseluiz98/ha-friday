"""POC descartável: Gemini Live na frente, OpenClaw como cérebro delegado, MCP do HA para comandos rápidos.

Rodado pelo run.sh do add-on openclaw_poc:
    python bot.py -t webrtc --host 127.0.0.1 --port 7860
"""

import asyncio
import json
import os
import random
import secrets
from datetime import datetime, timedelta, timezone

import aiohttp
from loguru import logger
from mcp.client.session_group import StreamableHttpParameters

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InputTextRawFrame,
    InterimTranscriptionFrame,
    LLMMessagesAppendFrame,
    LLMRunFrame,
    TranscriptionFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi import RTVIFunctionCallReportLevel, RTVIObserverParams
from pipecat.runner.types import RunnerArguments
from pipecat.services.google.gemini_live.llm import (
    ContextWindowCompressionParams,
    GeminiLiveLLMService,
    GeminiVADParams,
)
from pipecat.services.llm_service import FunctionCallParams
from pipecat.services.mcp_service import MCPClient
from pipecat.transcriptions.language import Language
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.workers.runner import WorkerRunner

import segundo_plano as sp
import registro_conversas as rc
import cancellation as cx
import satelite_modo as sm
import voice_settings as vs_cfg
import music_state as ms
import preferencias_voz as pv
import inventario_casa as ic
import gemini_watch as gw
import canal_casa as cc
import whisper_mode as wm
import memory_views as mv
import voice_directives as vd
import voice_skills as vs
import usage_ledger as ul
import activity as act
import local_config
import time
from retencao import RetencaoDeAudio
from pipecat.frames.frames import InputAudioRawFrame
from pipecat.frames.frames import FunctionCallInProgressFrame, FunctionCallResultFrame, OutputAudioRawFrame, TTSStoppedFrame, TTSTextFrame
from pipecat.frames.frames import FunctionCallResultProperties
import pipecat.frames.frames as _quadros

# Spec 2026-10-15 D2: quadros que encerram uma chamada de ferramenta. Pelo nome, sem quebrar se a versão do Pipecat
# não tiver algum (o log do bot diz quais existem).
_QUADROS_FIM_DE_FERRAMENTA = tuple(getattr(_quadros, n) for n in ("FunctionCallResultFrame", "FunctionCallCancelFrame")
                                   if hasattr(_quadros, n))
from google.genai.types import FunctionResponse, FunctionResponseScheduling
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.frames.frames import MetricsFrame
from pipecat.metrics.metrics import LLMUsageMetricsData
from pipecat.processors.frameworks.rtvi import RTVIServerMessageFrame

CONVERSAS_DIR = os.environ.get("FRIDAY_CONVERSATIONS_DIR", "/share/claude-voice/conversations")
RESERVA_DIR = os.environ.get("FRIDAY_CONVERSATIONS_SPOOL", "/srv/friday/conversations-spool")
# A gravação sai do laço de eventos da voz: com o Samba travado, um write no /share pode bloquear ~1 min (spec §4.9).
GRAVADOR = rc.GravadorEmFila(CONVERSAS_DIR, RESERVA_DIR, ao_gravar=lambda d: d == "reserva" and logger.warning(
    "conversa guardada na reserva local: compartilhamento do HAOS indisponível"))

GW_URL = os.environ.get("FRIDAY_GW_URL", "http://127.0.0.1:18789")
GW_TOKEN = os.environ["OPENCLAW_GATEWAY_TOKEN"]
# Com TLS no gateway, a URL é https://127.0.0.1 e o certificado é autoassinado e emitido para o IP da
# LAN: sem verificação, já que o tráfego não sai do host.
GW_SSL = not GW_URL.startswith("https://")
HA_URL = local_config.get("FRIDAY_HA_URL").rstrip("/")
HA_MCP_URL = f"{HA_URL}/api/mcp"
HA_PUSH_URL = f"{HA_URL}/api/services/{local_config.get('FRIDAY_OWNER_NOTIFY')}"
DONO = local_config.owner()  # nome do dono da casa: fica na configuração local, nunca no repo
# Modo em segundo plano (01/10, ver segundo_plano.py): o cérebro tem JANELA_SECS para responder dentro
# da fala; depois disso a voz avisa que conta depois e o pedido segue até PRAZO_CEREBRO_SECS.
JANELA_SECS = float(os.environ.get("FRIDAY_JANELA_CEREBRO_SECS", "8"))
# No satélite a conversa já fecha durante a espera: a função espera o cérebro inteiro (06/10, sem 2º aviso).
JANELA_SATELITE_SECS = float(os.environ.get("FRIDAY_JANELA_CEREBRO_SATELITE_SECS", "90"))
PRAZO_CEREBRO_SECS = float(os.environ.get("FRIDAY_PRAZO_CEREBRO_SECS", "600"))
# Prazo do Pipecat para a função em si, que devolve logo depois da janela.
FUNCAO_TIMEOUT_SECS = max(JANELA_SECS, JANELA_SATELITE_SECS) + 30
# Custo (29/09: US$ 5 em ~6 h). O Gemini Live recobra o contexto acumulado a cada fala: a compressão
# por janela deslizante segura esse contexto. Com pedido ao cérebro pendente, a inatividade não
# encerra a conversa (on_idle_timeout abaixo).
LIVE_TRIGGER_TOKENS = int(os.environ.get("FRIDAY_LIVE_TRIGGER_TOKENS", "16000"))
IDLE_TIMEOUT_SECS = float(os.environ.get("FRIDAY_IDLE_TIMEOUT_SECS", "180"))
logger.info(f"janela da voz: {LIVE_TRIGGER_TOKENS} tokens; inatividade: {IDLE_TIMEOUT_SECS:.0f} s")
# Cérebro quente durante a conversa (01/10). O gateway descarta as threads auxiliares depois de 60 s
# paradas e, neste Celeron N3350, recriá-las somava ~25 s à pergunta seguinte (29 s fria, 4 s quente).
# Uma busca na memória a cada 45 s mantém quente o motor de memória, a parte mais cara: a pergunta
# fria caiu para ~16 s. Não chama o modelo nem grava no histórico da Sexta-feira (conferido no banco).
# 0 desliga.
AQUECER_A_CADA_SECS = float(os.environ.get("FRIDAY_AQUECER_SECS", "45"))
AGENTE = os.environ.get("FRIDAY_OPENCLAW_AGENT", "friday")
# Canal da casa (spec 2026-10-12 §3): cadastro dos satélites (senha da porta 8700 de cada um, 600), senha do
# /deliver (lida pela skill "falar-em-casa" do cérebro) e o chat do dono no Telegram.
SATELITES_ARQ = os.environ.get("FRIDAY_SATELLITES", "/share/friday/satellites.json")
SATELITES_CACHE = os.environ.get("FRIDAY_SATELLITES_CACHE", "/srv/friday/state/satellites.cache.json")
TOKEN_CANAL_ARQ = os.environ.get("FRIDAY_TOKEN_CANAL", "/srv/friday/state/canal_casa.token")
# Senha só do POST /reminder: o OpenClaw a manda como `cron.webhookToken` (fica também no openclaw.json, 600).
TOKEN_REMINDER_ARQ = os.environ.get("FRIDAY_TOKEN_REMINDER", "/srv/friday/state/reminder_webhook.token")
# 04/11 (minor 13 de 19/10): conversas abertas e turnos do cérebro, para a porta de status e o agente do HA
ATIVIDADE = act.Activity(os.environ.get("FRIDAY_ACTIVITY_FILE", "/srv/friday/state/activity.json"))
VOICE_ENABLED_ARQ = os.environ.get("FRIDAY_VOICE_ENABLED_FILE", "/srv/friday/state/voice-enabled")  # global voice switch (HA integration)
TELEGRAM_JOSE = os.environ.get("FRIDAY_TELEGRAM_JOSE", "")
PRESENCA_JOSE = local_config.get("FRIDAY_OWNER_PERSON")
STATUS_SATELITE = "sensor.satelite_ultimo_contato"  # atributo ip_wifi (spec D9)
ENTREGAS = cc.Entregas()

SYSTEM_INSTRUCTION = """Você é a Sexta-feira, a assistente de voz da casa do {DONO}. Fale sempre em português
do Brasil, de forma natural e breve, como numa conversa entre duas pessoas, em ritmo um pouco mais rápido
e dinâmico. Nada de listas, emojis ou formatação: tudo o que você diz vira fala.

Pedidos sobre o seu jeito de falar (ritmo, tom, volume) você atende sozinha, na hora; não os passe
ao cérebro.

Para ligar, desligar ou ajustar aparelhos da casa, use as ferramentas do Home Assistant.

Para qualquer outra coisa que precise de conhecimento sobre o homelab, memória de conversas
anteriores, raciocínio mais longo, pesquisa ou execução de tarefas, use a ferramenta
perguntar_ao_cerebro. O cérebro só recebe o pedido pela chamada dessa ferramenta: uma fala que
anuncia a consulta sem a chamada no mesmo turno não pede nada a ele, e nenhuma resposta chega
depois. Quando a resposta vier, conte o resultado com as suas palavras, resumido. Se o {DONO} pedir
para você lembrar de algo, passe o pedido ao cérebro.

Se perguntar_ao_cerebro devolver status "em andamento", o cérebro ainda está trabalhando: diga numa
frase curta que vai avisar assim que tiver a resposta e siga a conversa normalmente. A resposta
chega depois, numa mensagem que começa com "[Resposta do cérebro"; ela não é fala do {DONO}. Quando
chegar, conte o resultado ao {DONO} com as suas palavras, resumido.

Se o usuário interromper você, pare e escute. Conversa entre outras pessoas no cômodo não é interrupção.""".replace("{DONO}", DONO)

def _brain_function(description: str) -> FunctionSchema:
    return FunctionSchema(
        name="perguntar_ao_cerebro",
        description=description,
        properties={
            "pedido": {
                "type": "string",
                "description": ("O pedido em português, com o que o " + DONO + " disse e o contexto da conversa. O cérebro tem o "
                                "relógio de Brasília; a voz não sabe a data de hoje, então datas e horas chegam ao "
                                "cérebro nas palavras do " + DONO + " (\"amanhã\", \"sexta às 9\")."),
            }
        },
        required=["pedido"],
    )


# 21/10 (spec 2026-10-21 §5.5): sem a vista das capacidades (modo off, ou arquivo ausente/velho) a descrição é a de sempre.
BRAIN_FUNCTION = _brain_function(mv.BRAIN_DESCRIPTION_DEFAULT)


async def chamar_cerebro(pedido: str, sessao: str) -> str:
    ATIVIDADE.brain_started()
    try:
        return await _chamar_cerebro(pedido, sessao)
    finally:
        ATIVIDADE.brain_finished()


async def _chamar_cerebro(pedido: str, sessao: str) -> str:
    # O `user` define a sessão do OpenClaw. Uma por conexão de voz: com um valor fixo, todo pedido do
    # dia reenviava o histórico inteiro (o contexto chegou a 171 mil tokens por chamada).
    body = {
        "model": f"openclaw/{AGENTE}",
        "messages": [{"role": "user", "content": pedido}],
        "user": sessao,
    }
    uso = None
    try:
        timeout = aiohttp.ClientTimeout(total=PRAZO_CEREBRO_SECS)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{GW_URL}/v1/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {GW_TOKEN}"},
                ssl=GW_SSL,
            ) as resp:
                data = await resp.json(content_type=None)
        uso = data.get("usage")
        resposta = data["choices"][0]["message"]["content"]
    except Exception as e:
        logger.exception("falha ao falar com o OpenClaw")
        resposta = f"Não consegui falar com o cérebro agora ({type(e).__name__})."
    logger.info(f"cérebro [{sessao}] -> {resposta[:200]!r} | uso: {uso}")
    return resposta


async def push_ao_jose(texto: str):
    """Entrega por push só ao dono (regra da casa: alertas da Sexta-feira não vão para os outros moradores)."""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.post(
                HA_PUSH_URL,
                json={"title": "Sexta-feira", "message": texto},
                headers={"Authorization": f"Bearer {os.environ['FRIDAY_HA_TOKEN']}"},
            ) as resp:
                await resp.read()
                if resp.status != 200:
                    logger.warning(f"push ao dono: HTTP {resp.status}")
    except Exception:
        logger.exception("falha ao mandar push ao dono")


# Pedidos ao cérebro e entregas podem sobreviver à conversa (a resposta vira push): referência forte
# até terminarem, senão o asyncio pode descartar a tarefa no meio.
_tarefas_de_fundo = set()


def _manter(tarefa):
    _tarefas_de_fundo.add(tarefa)
    tarefa.add_done_callback(_tarefas_de_fundo.discard)
    return tarefa


class SinaisDaConversa(FrameProcessor):
    """Registra quem está falando para a entrega da resposta esperar o silêncio.

    Fica entre o agregador do usuário e o Gemini: recebe, subindo, o início e o fim da fala da voz
    (vindos da saída de áudio) e a transcrição da fala do dono (vinda do Gemini).
    """

    def __init__(self, estado: sp.EstadoDaConversa, ao_falar=None):
        super().__init__()
        self._estado = estado
        self._ao_falar = ao_falar  # 19/10: fecha a vigia do Gemini (gemini_watch)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, BotStartedSpeakingFrame):
            self._estado.voz_comecou()
            if self._ao_falar:
                self._ao_falar()
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._estado.voz_parou()
        elif isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame, InputTextRawFrame)):
            self._estado.jose_falou()
        await self.push_frame(frame, direction)


async def manter_cerebro_quente():
    corpo = {
        "tool": "memory_search",
        "args": {"query": "aquecimento"},
        "sessionKey": f"agent:{AGENTE}:main",
    }
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
        while True:
            try:
                async with session.post(
                    f"{GW_URL}/tools/invoke",
                    json=corpo,
                    headers={"Authorization": f"Bearer {GW_TOKEN}"},
                    ssl=GW_SSL,
                ) as resp:
                    await resp.read()
                    if resp.status != 200:
                        logger.warning(f"aquecimento do cérebro: HTTP {resp.status}")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"aquecimento do cérebro falhou: {type(e).__name__}")
            await asyncio.sleep(AQUECER_A_CADA_SECS)


# 07/10: só no satélite ("parar", ou conversa que não é com ela). A página tem o próprio botão de desligar.
ENCERRAR_FUNCTION = FunctionSchema(
    name="encerrar_conversa",
    description=sm.DESCRICAO_ENCERRAR,
    properties={},
    required=[],
)


# 08/10 (Task 12, D11): só no satélite. O estado fica gravado na Pi; a Friday só o informa.
NAO_PERTURBE_FUNCTION = FunctionSchema(
    name="nao_perturbe",
    description=sm.DESCRICAO_NAO_PERTURBE,
    properties={"ligado": {"type": "boolean", "description": "true liga o não perturbe, false desliga."}},
    required=["ligado"],
)


# 12/10 (spec 2026-10-12 §1 e §2): só no satélite. A Pi aplica e responde pelo canal de comandos.
VOLUME_FUNCTION = FunctionSchema(
    name="volume_do_satelite",
    description=sm.DESCRICAO_VOLUME,
    properties={
        "numero": {"type": "integer", "description": "Escala de 1 a 10; 10 é o máximo."},
        "percentual": {"type": "integer", "description": "De 0 a 100."},
        "ajuste": {"type": "string", "enum": ["mais", "menos"], "description": "Um degrau a partir do atual."},
    },
    required=[],
)
# 14/10: uma ferramenta por ação. Com `acao` como enum, 25% das chamadas vinham inválidas ("cancel"; medido na API): o Live
# não impõe o enum. O nome da ferramenta é o que a API garante. Ver a skill designing-for-friday.
_ID_TIMER_PROP = {"type": "string", "description": "Id do timer (t + 6 caracteres), devolvido ao criar e ao consultar."}
_NOME_TIMER_PROP = {"type": "string", "description": "Nome do timer, como 'arroz'."}
CRIAR_TIMER_FUNCTION = FunctionSchema(
    name="criar_timer",
    description=sm.DESCRICAO_CRIAR_TIMER,
    properties={
        "duracao_s": {"type": "integer", "description": "Duração em segundos, até 24 horas."},
        "nome": {"type": "string", "description": "Nome opcional, como 'arroz'."},
        "music": {"type": "string", "description": "Opcional: o que tocar quando vencer (título, artista, álbum ou "
                                                    "playlist), até 80 caracteres."},
    },
    required=["duracao_s"],
)
CONSULTAR_TIMERS_FUNCTION = FunctionSchema(
    name="consultar_timers",
    description=sm.DESCRICAO_CONSULTAR_TIMERS,
    properties={"nome": _NOME_TIMER_PROP, "id": _ID_TIMER_PROP},
    required=[],
)
CANCELAR_TIMER_FUNCTION = FunctionSchema(
    name="cancelar_timer",
    description=sm.DESCRICAO_CANCELAR_TIMER,
    properties={
        "id": _ID_TIMER_PROP,
        "nome": _NOME_TIMER_PROP,
        "todos": {"type": "boolean", "description": "Cancelar todos os timers."},
    },
    required=[],
)
TIMER_FUNCTIONS = [CRIAR_TIMER_FUNCTION, CONSULTAR_TIMERS_FUNCTION, CANCELAR_TIMER_FUNCTION]

# Spec 2026-10-15 D15: programa de aprendizagem de voz (só no satélite). Duas ferramentas: a trava "roteiro antes de
# gravar" precisa de duas chamadas, e uma ferramenta com parâmetro que troca a ação seria um enum (D28).
APRENDIZAGEM_FUNCTION = FunctionSchema(
    name="aprendizagem_de_voz",
    description=sm.DESCRICAO_APRENDIZAGEM_DE_VOZ,
    properties={},
    required=[],
)
COMECAR_APRENDIZAGEM_FUNCTION = FunctionSchema(
    name="comecar_aprendizagem_de_voz",
    description=sm.DESCRICAO_COMECAR_APRENDIZAGEM,
    properties={"nome": {"type": "string", "description": "Nome da pessoa que vai gravar, como ela disse."}},
    required=["nome"],
)

# 05/11 (spec 2026-11-05 §6.4): só na conversa de apresentação de um satélite recém-instalado
DEFINIR_COMODO_FUNCTION = FunctionSchema(
    name="definir_comodo",
    description=sm.DESCRICAO_DEFINIR_COMODO,
    properties={"area_id": {"type": "string", "description": "Id de uma das áreas da lista da abertura da conversa."}},
    required=["area_id"],
)

# 23/10 (spec 2026-10-23): habilidades ensinadas. Declaradas só quando o registro tem alguma (sem habilidade, zero tokens a
# mais); search_skills só quando o catálogo do prompt foi cortado pelo teto.
_ID_SKILL_PROP = {"type": "string", "description": "Id da habilidade, como aparece na lista (k + 6 caracteres)."}
READ_SKILL_FUNCTION = FunctionSchema(name="read_skill", description=vs.DESC_READ_SKILL,
                                     properties={"id": _ID_SKILL_PROP}, required=["id"])
RUN_SKILL_FUNCTION = FunctionSchema(name="run_skill", description=vs.DESC_RUN_SKILL,
                                    properties={"id": _ID_SKILL_PROP}, required=["id"])
SEARCH_SKILLS_FUNCTION = FunctionSchema(
    name="search_skills", description=vs.DESC_SEARCH_SKILLS,
    properties={"query": {"type": "string", "description": "Assunto da habilidade, em poucas palavras."}},
    required=["query"])


async def estado_ha(entidade: str) -> dict:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
        async with session.get(f"{HA_URL}/api/states/{entidade}",
                               headers={"Authorization": f"Bearer {os.environ['FRIDAY_HA_TOKEN']}"}) as resp:
            resp.raise_for_status()
            return await resp.json()


async def deps_canal() -> "DepsCanal":
    """DepsCanal com o cadastro lido fora do laço de eventos (thread com prazo; ver read_satellites_without_blocking)."""
    return DepsCanal(await cc.read_satellites_without_blocking(SATELITES_ARQ, SATELITES_CACHE))


class DepsCanal:
    """IO real do canal da casa (canal_casa.entregar)."""

    def __init__(self, satelites: dict):
        self.entregas = ENTREGAS
        self.satelites = satelites
        self.espera_conexao_s, self.espera_fim_s = cc.ESPERA_CONEXAO_S, cc.ESPERA_FIM_S

    async def presenca(self):
        return (await estado_ha(PRESENCA_JOSE)).get("state")

    async def ip_wifi(self, sid):
        try:
            return (await estado_ha(STATUS_SATELITE)).get("attributes", {}).get("ip_wifi") or None
        except Exception:
            return None

    async def campainha(self, ip, senha, eid):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as session:
            async with session.post(f"http://{ip}:8700/doorbell", json={"mensagem": eid},
                                    headers={"Authorization": f"Bearer {senha}"}) as resp:
                resp.raise_for_status()
                return (await resp.json()).get("resposta")

    async def play_music(self, player, query):
        """Script music_play do HA no player do satélite (R11); a resposta do script é o fato do que tocou."""
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.post(f"{HA_URL}/api/services/script/music_play?return_response",
                                    json={"query": query, "players": [player]},
                                    headers={"Authorization": f"Bearer {os.environ['FRIDAY_HA_TOKEN']}"}) as resp:
                resp.raise_for_status()
                return (await resp.json()).get("service_response")

    def registrar(self, linha):
        logger.info("entrega: " + json.dumps(linha, ensure_ascii=False))


async def telegram_direto(texto: str):
    """D14: ferramenta `message` do OpenClaw pelo /tools/invoke, sem turno do cérebro."""
    if not TELEGRAM_JOSE:
        raise RuntimeError("FRIDAY_TELEGRAM_JOSE vazio")
    corpo = {"tool": "message", "sessionKey": f"agent:{AGENTE}:main",
             "args": {"action": "send", "channel": "telegram", "target": TELEGRAM_JOSE, "message": texto}}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        async with session.post(f"{GW_URL}/tools/invoke", json=corpo, ssl=GW_SSL,
                                headers={"Authorization": f"Bearer {GW_TOKEN}"}) as resp:
            dados = await resp.json(content_type=None)
            if resp.status != 200 or (isinstance(dados, dict) and dados.get("ok") is False):
                raise RuntimeError(f"message: HTTP {resp.status}")


async def telegram_via_cerebro(texto: str):
    """Plano B de D14: um turno do cérebro manda pelo Telegram."""
    await chamar_cerebro("[aviso do satélite de voz] Mande ao " + DONO + " pelo Telegram, com este sentido: " + texto,
                         "avisos-satelite")


def _ler_token_canal() -> str:
    try:
        with open(TOKEN_CANAL_ARQ) as f:
            return f.read().strip()
    except OSError:
        return ""  # sem o arquivo, o /deliver recusa tudo


def _ler_token_reminder() -> str:
    try:
        with open(TOKEN_REMINDER_ARQ) as f:
            return f.read().strip()
    except OSError:
        return ""  # sem o arquivo, o /reminder recusa tudo


_skills_lock = asyncio.Lock()  # gravações do registro de habilidades (rotas e contagem de uso)
_lembretes_em_curso = set()  # referência às tarefas de entrega disparadas pelo /reminder (o loop só guarda fraca)


def voice_enabled(path: str) -> bool:
    """Global voice switch: file "off" disables new conversations; missing/unreadable file means enabled."""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip().lower() != "off"
    except (OSError, ValueError):  # ValueError covers UnicodeDecodeError: fail open
        return True


def registrar_recusa_voz(app, path=None):
    """Refuse NEW WebRTC conversations (POST /start, POST .../api/offer: satellites and /client/) with 503
    when the voice switch is off. File read on each offer; ongoing conversations (ICE PATCH) and the house
    channel routes are never touched."""
    from fastapi.responses import JSONResponse

    @app.middleware("http")
    async def recusar_se_desligada(request, call_next):
        try:
            if request.method == "POST" and (request.url.path == "/start" or request.url.path.endswith("api/offer")):
                if not voice_enabled(path or os.environ.get("FRIDAY_VOICE_ENABLED_FILE", "/srv/friday/state/voice-enabled")):
                    return JSONResponse({"error": "voice_disabled"}, status_code=503)
        except Exception:  # never break request handling: any failure allows the offer
            pass
        return await call_next(request)


def registrar_rotas(app):
    """Rotas do canal da casa no próprio servidor do bot (gancho documentado do runner do Pipecat 1.12:
    `from pipecat.runner.run import app`). Ficam em 127.0.0.1:7860 e, pelo socat, em https://:7443."""
    from fastapi import Request
    from fastapi.responses import JSONResponse

    async def corpo_json(request):
        try:
            return await request.json()
        except Exception:
            return None

    @app.post("/deliver")
    async def rota_entregar(request: Request):
        if not cc.token_confere(_ler_token_canal(), request.headers.get("authorization")):
            return JSONResponse({"erro": "não autorizado"}, status_code=401)
        return JSONResponse(await cc.entregar(await corpo_json(request), await deps_canal()))

    async def _ferramentas_e_inventario():
        """Schemas das ferramentas da voz (satélite) e o inventário da casa, para validar receitas."""
        (tools, mcp, _), (texto, erro) = await asyncio.gather(
            build_tools(True), ic.buscar(HA_MCP_URL, os.environ["FRIDAY_HA_TOKEN"], timeout=5.0))
        if mcp is not None:
            try:
                await mcp.close()
            except Exception:
                pass
        return vs.tool_schemas(tools.standard_tools), vs.parse_inventory(texto), bool(texto)

    def _autorizado(request):
        return cc.token_confere(_ler_token_canal(), request.headers.get("authorization"))

    @app.get("/voice_skills")
    async def rota_skills_listar(request: Request):
        if not _autorizado(request):
            return JSONResponse({"erro": "não autorizado"}, status_code=401)
        return JSONResponse({"skills": vs.load()})

    @app.get("/voice_skills/tools")
    async def rota_skills_ferramentas(request: Request):
        if not _autorizado(request):
            return JSONResponse({"erro": "não autorizado"}, status_code=401)
        schemas, inv, _ = await _ferramentas_e_inventario()
        permitidas = {n: v for n, v in schemas.items() if n not in vs.FORBIDDEN_STEP_TOOLS}
        return JSONResponse({"tools": permitidas, "areas": sorted(inv.area_labels)})

    @app.post("/voice_skills")
    async def rota_skills_gravar(request: Request):
        if not _autorizado(request):
            return JSONResponse({"erro": "não autorizado"}, status_code=401)
        candidato = await corpo_json(request)
        if not isinstance(candidato, dict):
            return JSONResponse({"ok": False, "errors": ["o corpo precisa ser um objeto JSON"]}, status_code=400)
        candidato["taught_by"] = candidato.get("taught_by") or "unknown"
        async with _skills_lock:
            schemas, inv, inventario_ok = await _ferramentas_e_inventario()
            passos = candidato.get("steps") if isinstance(candidato.get("steps"), list) else []
            if not inventario_ok and any(isinstance(p, dict) and "__" in str(p.get("tool")) for p in passos):
                return JSONResponse({"ok": False, "errors": ["inventário da casa indisponível agora; tente de novo"]},
                                    status_code=503)
            erros = vs.validate(candidato, schemas, inv)
            if erros:
                logger.info(f"voice_skills: receita recusada ({len(erros)} erro(s))")
                return JSONResponse({"ok": False, "errors": erros}, status_code=400)
            agora = datetime.now(timezone(timedelta(hours=-3))).isoformat(timespec="seconds")
            novas, r = vs.upsert(vs.load(), candidato, agora)
            if not r["ok"]:
                return JSONResponse({"ok": False, "conflict": vs.recipe(r["conflict"])}, status_code=409)
            vs.save(novas)
        s = r["skill"]
        logger.info(f"voice_skills: {s['name']!r} created={r['created']} passos={len(s['steps'])}")
        if r["created"] and vs.read_config()["notify_new_skill"]:
            texto = (f"Nova habilidade da Friday: {s['name']} — {s['description']}. Ensinada por {s['taught_by']}. "
                     f"Passos: {', '.join(p['tool'] for p in s['steps'])}.")

            async def avisar():
                try:
                    await telegram_direto(texto)
                except Exception as e:
                    logger.warning(f"voice_skills: aviso de nova habilidade falhou ({type(e).__name__})")

            t = asyncio.create_task(avisar())
            _lembretes_em_curso.add(t)
            t.add_done_callback(_lembretes_em_curso.discard)
        return JSONResponse(r)

    @app.delete("/voice_skills/{key}")
    async def rota_skills_apagar(key: str, request: Request):
        if not _autorizado(request):
            return JSONResponse({"erro": "não autorizado"}, status_code=401)
        async with _skills_lock:
            novas, removida = vs.delete(vs.load(), key)
            if removida is None:
                return JSONResponse({"ok": False}, status_code=404)
            vs.save(novas)
        logger.info(f"voice_skills: {removida['name']!r} apagada")
        return JSONResponse({"ok": True, "deleted": vs.recipe(removida)})

    @app.post("/reminder")
    async def rota_reminder(request: Request):
        """Conclusão de um lembrete do agendador do OpenClaw (delivery.completionDestination webhook). O OpenClaw
        espera no máximo 10 s: responde já e toca no satélite em segundo plano. Sem Telegram aqui: o `announce` do
        próprio agendamento já mandou o mesmo texto. Não passa pelo caminho dos timers."""
        if not cc.token_confere(_ler_token_reminder(), request.headers.get("authorization")):
            return JSONResponse({"erro": "não autorizado"}, status_code=401)
        evento = await corpo_json(request)
        pedido = cc.pedido_do_lembrete(evento)
        job = evento.get("jobId") if isinstance(evento, dict) else None
        if pedido is None:
            logger.info(f"lembrete do agendador ignorado: job {job}")
            return JSONResponse({"ignorado": True})

        async def tocar():
            try:
                r = await cc.entregar(pedido, await deps_canal())
            except Exception as e:
                r = {"resultado": "erro", "erro": repr(e)}
            logger.info(f"lembrete do agendador (job {job}): " + json.dumps(r, ensure_ascii=False))

        t = asyncio.create_task(tocar())
        _lembretes_em_curso.add(t)
        t.add_done_callback(_lembretes_em_curso.discard)
        return JSONResponse({"aceito": True}, status_code=202)

    @app.post("/satellite_alert")
    async def rota_aviso(request: Request):
        sats = await cc.read_satellites_without_blocking(SATELITES_ARQ, SATELITES_CACHE)
        sid = cc.autenticar_satelite(sats, request.headers.get("authorization"))
        if not sid:
            return JSONResponse({"erro": "não autorizado"}, status_code=401)
        corpo = await corpo_json(request)
        try:
            texto = cc.texto_aviso(corpo, sats[sid])
        except ValueError as e:
            return JSONResponse({"erro": str(e)}, status_code=400)
        via = await cc.mandar_telegram(texto, telegram_direto, telegram_via_cerebro)
        logger.info(f"aviso do satélite {sid}: {via}")
        if cc.e_aviso_de_cadastro(corpo):  # D18/D25: a Friday também conta no satélite, com a fonte verdadeira

            async def contar():
                try:
                    r = await cc.entregar({"destino": f"satelite:{sid}", "texto": texto, "tipo": "aprendizagem"},
                                          await deps_canal())
                except Exception as e:
                    r = {"resultado": "erro", "erro": repr(e)}
                logger.info("resultado da aprendizagem no satélite: " + json.dumps(r, ensure_ascii=False))

            t = asyncio.create_task(contar())
            _lembretes_em_curso.add(t)
            t.add_done_callback(_lembretes_em_curso.discard)
        return JSONResponse({"enviado": via})


    @app.post("/satellite_media")
    async def rota_musica(request: Request):
        sats = await cc.read_satellites_without_blocking(SATELITES_ARQ, SATELITES_CACHE)
        sid = cc.autenticar_satelite(sats, request.headers.get("authorization"))
        if not sid:
            return JSONResponse({"erro": "não autorizado"}, status_code=401)
        corpo = await corpo_json(request)
        r = await cc.play_for_satellite(sid, corpo.get("query") if isinstance(corpo, dict) else None, DepsCanal(sats))
        logger.info(f"música do alarme no satélite {sid}: ok={r.get('ok')}")
        return JSONResponse(r)


async def build_tools(eh_satelite: bool = False, brain_desc: str | None = None, skill_tools: bool = False,
                      search_tool: bool = False, apresentacao: bool = False) -> tuple:
    """(ToolsSchema, cliente do MCP ou None, nomes das ferramentas vindas do MCP)."""
    brain = BRAIN_FUNCTION if brain_desc is None else _brain_function(brain_desc)
    standard = [brain] + (
        [ENCERRAR_FUNCTION, NAO_PERTURBE_FUNCTION, VOLUME_FUNCTION, *TIMER_FUNCTIONS, APRENDIZAGEM_FUNCTION,
         COMECAR_APRENDIZAGEM_FUNCTION] if eh_satelite else [])
    if apresentacao:
        standard += [DEFINIR_COMODO_FUNCTION]
    if skill_tools:
        standard += [READ_SKILL_FUNCTION, RUN_SKILL_FUNCTION]
    if search_tool:
        standard.append(SEARCH_SKILLS_FUNCTION)
    mcp, nomes_mcp = None, set()
    try:
        mcp = MCPClient(
            server_params=StreamableHttpParameters(
                url=HA_MCP_URL,
                headers={"Authorization": f"Bearer {os.environ['FRIDAY_HA_TOKEN']}"},
            )
        )
        ha_tools = await mcp.tools()
        standard = ha_tools.standard_tools + standard
        nomes_mcp = {t.name for t in ha_tools.standard_tools}
        logger.info(f"MCP do HA: {len(ha_tools.standard_tools)} ferramentas")
    except Exception:
        mcp = None
        logger.exception("MCP do HA indisponível; seguindo só com o cérebro")
    return ToolsSchema(standard_tools=standard), mcp, nomes_mcp


class TextoDigitadoParaGemini(FrameProcessor):
    """Entrega ao Gemini Live o texto digitado na Playground.

    A Playground manda texto como LLMMessagesAppendFrame. O agregador o anexa só ao contexto local, e o
    Gemini Live, com a sessão já aberta, não reenvia mensagens anexadas assim: o texto se perdia sem
    erro (30/09). Como InputTextRawFrame ele segue pelo canal de texto em tempo real do Gemini.
    """

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMMessagesAppendFrame) and direction == FrameDirection.DOWNSTREAM:
            textos = [
                m["content"]
                for m in frame.messages
                if isinstance(m, dict) and m.get("role") == "user" and isinstance(m.get("content"), str)
            ]
            if textos and len(textos) == len(frame.messages):
                for texto in textos:
                    logger.info(f"texto digitado -> Gemini: {texto[:200]!r}")
                    await self.push_frame(InputTextRawFrame(text=texto), direction)
                return
        await self.push_frame(frame, direction)


# 14/10: a transcrição de entrada do Gemini Live (o texto do Jarvis Logbook) é criada sem idioma pelo Pipecat 1.12
# (`AudioTranscriptionConfig()`, llm.py) e saía em italiano/espanhol ("venerdì", "viernes"). Não muda o que o modelo
# entende, só esse texto. A API aceita `language_codes` (testado em 14/10); se o módulo mudar, nada acontece.
try:
    import pipecat.services.google.gemini_live.llm as _gemini_llm
    from google.genai.types import AudioTranscriptionConfig as _AudioTranscriptionConfig

    if hasattr(_gemini_llm, "AudioTranscriptionConfig"):
        _gemini_llm.AudioTranscriptionConfig = lambda: _AudioTranscriptionConfig(language_codes=["pt-BR"])
        logger.info("transcrição de entrada e de saída do Gemini Live fixada em pt-BR")
except Exception as _erro:  # noqa: BLE001
    logger.warning(f"idioma da transcrição não fixado: {_erro}")


class SegurarFalaDepoisDoVolume(FrameProcessor):
    """Segura o começo do áudio de cada turno dela e o descarta se o texto dele for o marcador "<no speech>{pause}"
    que o Gemini 3.8 fala quando decide ficar calada; qualquer outra fala é liberada na hora (sm.PortaoDeFala).
    Nasceu para o turno depois do volume (13/10); desde 12/11 vale para todo turno (portão com sempre=True)."""

    def __init__(self, portao, sessao=""):
        super().__init__()
        self._portao, self._sessao = portao, sessao
        self._guardados = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, OutputAudioRawFrame):
            tipo, texto = "audio", ""
        elif isinstance(frame, TTSTextFrame):
            tipo, texto = "texto", frame.text or ""
        elif isinstance(frame, TTSStoppedFrame):
            tipo, texto = "fim", ""
        else:
            await self.push_frame(frame, direction)
            return
        acao = self._portao.tratar(tipo, texto)
        if acao == "segurar":
            self._guardados.append((frame, direction))
            return
        if acao == "descartar":
            if self._guardados or tipo == "texto":
                logger.info(f"[{self._sessao}] turno sem fala (<no speech>) descartado; "
                            f"segurado {self._portao.segurou_s:.2f}s")
            self._guardados.clear()
            if tipo == "fim":  # o quadro de fim de turno segue, para o resto do pipeline fechar o turno
                await self.push_frame(frame, direction)
            return
        if acao == "soltar":
            guardados, self._guardados = self._guardados, []
            logger.info(f"[{self._sessao}] portão de fala: soltou após {self._portao.segurou_s:.2f}s ({tipo})")
            for q, d in guardados:
                await self.push_frame(q, d)
        await self.push_frame(frame, direction)


class SeguraAudioAteGemini(FrameProcessor):
    """Segura o áudio de entrada até o Gemini Live ficar pronto (ver retencao.py): sem isso a pré-gravação
    do satélite, que chega no primeiro segundo, é descartada pelo Pipecat."""

    def __init__(self, llm):
        super().__init__()
        self._llm = llm
        self._ret = RetencaoDeAudio()
        self._vigia = None

    def _pronto(self):
        return bool(getattr(self._llm, "_ready_for_realtime_input", True))

    async def _vigiar(self):
        for _ in range(200):  # até 10 s
            await asyncio.sleep(0.05)
            saida = self._ret.liberar_se_pronto(self._pronto())
            for q in saida:
                await self.push_frame(q, FrameDirection.DOWNSTREAM)
            if saida or self._pronto():
                return

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame) and direction == FrameDirection.DOWNSTREAM:
            for q in self._ret.entrar(frame, self._pronto()):
                await self.push_frame(q, direction)
            if self._vigia is None and not self._pronto():
                self._vigia = _manter(asyncio.create_task(self._vigiar()))
            return
        await self.push_frame(frame, direction)


class EntregaObserver(BaseObserver):
    """Spec 2026-10-12 §3: na conversa de entrega, fala de alguém DEPOIS da fala dela = entregue."""

    def __init__(self, entrega_id: str):
        super().__init__()
        self._id, self._ela_falou = entrega_id, False

    async def on_push_frame(self, data: FramePushed):
        if not data.first_push:
            return
        if isinstance(data.frame, BotStoppedSpeakingFrame):
            self._ela_falou = True
        elif isinstance(data.frame, TranscriptionFrame) and data.frame.text and self._ela_falou:
            ENTREGAS.atendida(self._id)


class RegistroObserver(BaseObserver):
    """Grava cada turno no NDJSON do CV (spec do satélite §4). Só olha a primeira passagem de cada quadro."""

    def __init__(self, turnos: rc.Turnos):
        super().__init__()
        self._t = turnos

    async def on_push_frame(self, data: FramePushed):
        if not data.first_push:
            return
        f = data.frame
        try:
            if isinstance(f, TranscriptionFrame) and f.text:
                self._t.usuario(f.text, datetime.now().timestamp())
            elif isinstance(f, TTSTextFrame) and f.text:
                self._t.ela(f.text)
            elif isinstance(f, FunctionCallInProgressFrame):
                self._t.ferramenta(f.function_name, json.dumps(f.arguments, ensure_ascii=False), f.tool_call_id)
            elif isinstance(f, BotStoppedSpeakingFrame):
                linha = self._t.ela_parou(datetime.now().timestamp())
                if linha:
                    GRAVADOR.enviar(linha)
        except Exception:
            logger.exception("falha ao gravar o turno no NDJSON")


class ChamadasObserver(BaseObserver):
    """23/10: chamadas de ferramenta da conversa e se deram certo; é a fonte de uma habilidade ensinada por
    demonstração (o bot anexa as que deram certo ao pedido que vai ao cérebro). Só a primeira passagem de cada quadro."""

    def __init__(self, chamadas: list):
        super().__init__()
        self._chamadas, self._em_curso, self._vistos = chamadas, {}, set()

    async def on_push_frame(self, data: FramePushed):
        if not data.first_push:
            return
        f = data.frame
        try:
            if isinstance(f, FunctionCallInProgressFrame):
                self._em_curso[f.tool_call_id] = (f.function_name, f.arguments)
            elif isinstance(f, FunctionCallResultFrame):
                nome, args = self._em_curso.pop(f.tool_call_id, (f.function_name, getattr(f, "arguments", {})))
                vs.record_call(self._chamadas, self._vistos, f.tool_call_id, nome, args, f.result,
                               getattr(f, "error", None))
        except Exception:
            logger.exception("falha ao anotar a chamada de ferramenta")


class UsageObserver(BaseObserver):
    """Spec 2026-10-21 §5.6: tokens de prompt e de raciocínio por turno, por braço do teste A/B das vistas de memória.
    Só olha a primeira passagem de cada quadro (mesmo padrão do RegistroObserver)."""

    def __init__(self, ab: mv.AbLog, sessao: str, arm: str, satelite: str | None = None, modelo: str = ""):
        super().__init__()
        self._ab, self._sessao, self._arm = ab, sessao, arm
        self._satelite, self._modelo, self._contador = satelite, modelo, 0

    async def on_push_frame(self, data: FramePushed):
        if not data.first_push:
            return
        f = data.frame
        try:
            if isinstance(f, MetricsFrame):
                for d in f.data:
                    if isinstance(d, LLMUsageMetricsData):
                        self._ab.usage(self._sessao, self._arm, d.value.prompt_tokens, d.value.reasoning_tokens or 0)
                        self._contador += 1
                        v = d.value
                        ul.append_voice(
                            datetime.now(timezone.utc), self._sessao, self._contador, self._satelite,
                            getattr(d, "model", None) or self._modelo,
                            ul.voice_tokens(v.prompt_tokens, v.completion_tokens, v.cache_read_input_tokens,
                                            v.reasoning_tokens, v.input_audio_tokens, v.output_audio_tokens,
                                            v.cache_read_input_audio_tokens))
        except Exception:
            logger.exception("falha ao gravar o uso de tokens do A/B")


class SinaisDoSatelite(BaseObserver):
    """Spec 2026-10-15 §4.1 e §4.2: avisa a Pi das ferramentas da voz em andamento (a espera curta não corre
    enquanto uma ação do HA roda) e de um turno que foi só agradecimento (a Pi fecha quando ela termina de falar)."""

    def __init__(self, avisar, contador):
        super().__init__()
        self._avisar = avisar
        self._contador, self._fim = contador, sm.FimPorAgradecimento()
        self._casa = sm.TurnoDeCasa()

    async def on_push_frame(self, data: FramePushed):
        if not data.first_push:
            return
        f = data.frame
        try:
            n = None
            if isinstance(f, FunctionCallInProgressFrame):
                n = self._contador.comecou(f.function_name, f.tool_call_id)
                self._casa.ferramenta(f.function_name)
            elif _QUADROS_FIM_DE_FERRAMENTA and isinstance(f, _QUADROS_FIM_DE_FERRAMENTA):
                n = self._contador.terminou(f.tool_call_id)
            if n is not None:
                await self._avisar(sm.msg_ferramenta(n))
            if isinstance(f, TranscriptionFrame) and f.text:
                self._fim.usuario(f.text)
                # 04/11: o turno de casa NÃO zera aqui: frase sem pontuação só vira transcrição 0,5 s depois do último
                # pedaço (Pipecat, _transcription_timeout_handler), muitas vezes depois da ferramenta já chamada
            elif isinstance(f, BotStoppedSpeakingFrame):
                agradeceu, casa = self._fim.ela_parou(), self._casa.ela_parou()  # os dois sempre, para zerar
                if agradeceu:
                    logger.info("agradecimento: a Pi fecha a conversa depois da fala dela")
                    await self._avisar(sm.MSG_ENCERRAR_AGRADECIMENTO)
                elif casa:
                    logger.info("comando da casa: a Pi fecha a conversa depois da fala dela")
                    await self._avisar(sm.MSG_ENCERRAR_COMANDO_CASA)
        except Exception:
            logger.exception("falha nos sinais do satélite")


class PromessaObserver(BaseObserver):
    """27/10: turno dela que prometeu consulta sem chamar ferramenta (sm.PromessaSemChamada). Segura a Pi como uma
    ferramenta em andamento e manda ao Gemini o fato de que nada foi pedido; solta quando ela chama algo, volta a
    falar ou em SEGURA_MAX_S."""

    ESPERA_S, SEGURA_MAX_S = 1.5, 7.0
    _ID = "promessa_sem_chamada"

    def __init__(self, sessao, contador, avisar, enviar, cerebro_ocupado):
        super().__init__()
        self._sessao, self._contador, self._avisar = sessao, contador, avisar
        self._enviar, self._cerebro_ocupado = enviar, cerebro_ocupado
        self._p, self._segurando, self._falou_em = sm.PromessaSemChamada(), False, 0.0

    async def _soltar(self, depois_s=0.0):
        if depois_s:
            await asyncio.sleep(depois_s)
        if self._segurando:
            self._segurando = False
            n = self._contador.terminou(self._ID) if self._contador else None
            if n is not None:
                await self._avisar(sm.msg_ferramenta(n))

    async def _conferir(self, parou_em):
        await asyncio.sleep(self.ESPERA_S)
        if self._falou_em > parou_em or not self._p.sem_chamada_desde(parou_em) or self._cerebro_ocupado():
            return
        self._p.avisou()
        logger.warning(f"[{self._sessao}] promessa de consulta sem chamada de ferramenta: aviso ao Gemini")
        if self._contador:
            n = self._contador.comecou(self._ID, self._ID)
            if n is not None:
                self._segurando = True
                await self._avisar(sm.msg_ferramenta(n))
        await self._enviar(sm.AVISO_PROMESSA_SEM_CHAMADA)
        await self._soltar(self.SEGURA_MAX_S)

    async def on_push_frame(self, data: FramePushed):
        if not data.first_push:
            return
        f, agora = data.frame, time.monotonic()
        try:
            if isinstance(f, FunctionCallInProgressFrame):
                self._p.chamada(agora)
                _manter(asyncio.create_task(self._soltar(0.5)))  # o contador já registrou a chamada de verdade
            elif isinstance(f, BotStartedSpeakingFrame):
                self._p.ela_comecou(agora)
                self._falou_em = agora
                await self._soltar()
            elif isinstance(f, TTSTextFrame) and f.text:
                self._p.ela(f.text)
            elif isinstance(f, BotStoppedSpeakingFrame) and self._p.ela_parou(agora):
                _manter(asyncio.create_task(self._conferir(agora)))
            elif isinstance(f, TranscriptionFrame) and f.text:
                self._p.usuario()
        except Exception:
            logger.exception("falha na vigia de promessa sem chamada")


async def _vazio() -> str:
    return ""


async def bot(runner_args: RunnerArguments):
    corpo = getattr(runner_args, "body", None)
    eh_satelite, satelite_id = sm.origem(corpo)
    comodo = sm.comodo(corpo) if eh_satelite else None
    nao_perturbe = [sm.nao_perturbe(corpo) if eh_satelite else False]  # lista: o handler da ferramenta muda
    # 12/10: conversa aberta pela própria Pi — entrega de mensagem do cérebro ou timer vencido (spec 2026-10-12)
    id_entrega = sm.entrega(corpo) if eh_satelite else None
    timer = sm.timer_vencido(corpo) if eh_satelite else None
    apresentacao = sm.apresentacao(corpo) if eh_satelite else None  # 05/11: satélite recém-instalado (spec 2026-11-05)
    entrega = ENTREGAS.conectou(id_entrega) if id_entrega else None
    # 06/10: o que o cérebro aprendeu (USER.md) também vale para a voz. 23/10: vista curada (teto) + diretrizes novas do dia,
    # lidas no arquivo inteiro (spec 2026-10-23 §4); sem a vista, todas as ativas como antes.
    diretrizes = vd.load()
    preferencias = diretrizes.context
    if diretrizes.overflow:
        logger.warning(f"diretrizes fora da voz por teto do dia: {len(diretrizes.overflow)}")
    if diretrizes.missing:
        logger.warning(f"diretrizes ativas que não chegaram à voz: {len(diretrizes.missing)}")
    habilidades = vs.load()  # 23/10: habilidades ensinadas; a voz vê só o catálogo (teto), a receita vem sob demanda
    catalogo, cortou = vs.catalog(habilidades)
    logger.info(f"habilidades: {len(habilidades)} no registro; catálogo {len(catalogo)} caracteres")
    # 21/10 (spec 2026-10-21 §5.4–5.6): vistas da memória do cérebro (Voice Briefing e capacidades), atrás do modo
    # off|ab|on lido do arquivo a cada conexão. Off (o padrão), ausente ou velha = a voz segue como antes.
    arm = mv.choose_arm(mv.read_mode(mv.MODE_FILE), random.random())
    briefing = caps = ""
    if arm == "on":
        briefing = mv.briefing_context(mv.read_view(mv.VOICE_BRIEFING, time.time(), mv.BRIEFING_MAX))
        caps = mv.read_view(mv.BRAIN_CAPABILITIES, time.time(), mv.CAPABILITIES_MAX)
    ab = mv.AbLog()
    # "conexão: entrega …" e "conexão: timer …" ficam fora da medição de latência (spec 2026-10-12 §6)
    # 20/10 (spec 2026-10-20 D6): conversa aberta por um "Friday" sussurrado; só o satélite manda `whisper`
    sussurro = eh_satelite and sm.whisper(corpo)
    logger.info(f"conexão: {sm.rotulo_conexao(eh_satelite, satelite_id, comodo, id_entrega, timer, bool(apresentacao))}"
                f"{' sussurro' if sussurro else ''}; "
                f"preferências: {preferencias.count(chr(10) + '- ')} diretrizes; vistas de memória: braço={arm}")
    if id_entrega and not entrega:
        logger.warning(f"entrega {id_entrega} desconhecida ou vencida: a conversa é encerrada")
    transport = SmallWebRTCTransport(
        webrtc_connection=runner_args.webrtc_connection,
        params=TransportParams(audio_in_enabled=True, audio_out_enabled=True),
    )

    # A família 2.5 (default do Pipecat 1.12) não está mais disponível para contas novas.
    # 11/10: o inventário da casa (prompt "Assist" do MCP do HA = o que está exposto ao Assist) entra na
    # instrução, buscado em paralelo com as ferramentas. Falha ou demora > 2 s = conversa segue sem ele.
    t_inventario = time.monotonic()
    # 21/10 (spec 2026-10-21 §6.4): fato de música da abertura (player do cadastro + estado, teto de 0,3 s), lido em
    # paralelo: nunca atrasa nem impede a abertura (falha = sem o fato).
    async def _cadastro():
        return await cc.read_satellites_without_blocking(SATELITES_ARQ, SATELITES_CACHE)

    async def _estado_do_player(player):
        return await ms.player_state(player, HA_URL, os.environ["FRIDAY_HA_TOKEN"])

    (ferramentas, mcp, nomes_mcp), (inventario, erro_inventario), fato_musica = await asyncio.gather(
        build_tools(eh_satelite, mv.brain_description(caps), skill_tools=bool(habilidades), search_tool=cortou,
                    apresentacao=bool(apresentacao)),
        ic.buscar(HA_MCP_URL, os.environ["FRIDAY_HA_TOKEN"]),
        ms.music_fact_for(satelite_id, _cadastro, _estado_do_player, sm.music_fact) if eh_satelite else _vazio()
    )
    inventario = ic.sem_frase_de_grupo(inventario)  # 23/10: a frase do HA que manda pedir o cômodo competia com as diretrizes
    if eh_satelite:  # só o satélite tem a ferramenta timer; a página segue com o texto do HA como veio
        inventario = ic.sem_frase_de_timers(inventario)
    logger.info(f"inventário da casa (prompt Assist do HA): {len(inventario)} caracteres em "
                f"{time.monotonic() - t_inventario:.2f} s" + (f"; falhou: {erro_inventario}" if erro_inventario else ""))
    cfg_voz = vs_cfg.ler()  # Task 15: escolhas do Home Assistant, lidas a cada conversa (arquivo > ambiente)
    fatos_voz = vs_cfg.fatos(cfg_voz)
    settings_kwargs = {
        "model": vs_cfg.modelo(cfg_voz),
        "voice": vs_cfg.voz(cfg_voz),
        "language": Language.PT_BR,
        "system_instruction": SYSTEM_INSTRUCTION
        + ("\n\n" + sm.contexto_satelite(satelite_id, comodo, nao_perturbe[0], fato_musica) if eh_satelite else "")
        + ("\n\n" + preferencias if preferencias else "")
        + ("\n\n" + catalogo if catalogo else "")
        + ("\n\n" + briefing if briefing else "")
        + ("\n\n" + inventario if inventario else "")
        + ("\n\n" + fatos_voz if fatos_voz else ""),
        "context_window_compression": ContextWindowCompressionParams(
            enabled=True, trigger_tokens=LIVE_TRIGGER_TOKENS
        ),
    }

    fim_ms = sm.fim_de_fala_ms(corpo) if eh_satelite and not sussurro else None
    if fim_ms is not None:  # 04/11: pausa de fim de fala ajustada pela Pi (em 0,8 s nada muda no Gemini)
        settings_kwargs["vad"] = GeminiVADParams(silence_duration_ms=fim_ms)
        logger.info(f"pausa de fim de fala do satélite: {fim_ms} ms")
    llm_extra = {}
    if sussurro:
        # D7: sem a detecção de atividade do servidor; início e fim da fala vêm da Pi (PiSpeechTurns)
        settings_kwargs["vad"] = GeminiVADParams(disabled=True)
        llm_extra["user_audio_preroll_secs"] = 1.5
    llm = GeminiLiveLLMService(
        api_key=os.environ["GEMINI_API_KEY"],
        tools=ferramentas,
        settings=GeminiLiveLLMService.Settings(**settings_kwargs),
        **llm_extra,
    )
    sessao = f"voz-{datetime.now():%Y%m%d-%H%M%S}"
    logger.info(f"sessão do cérebro desta conexão: {sessao} (janela da voz: {LIVE_TRIGGER_TOKENS} tokens)")
    if eh_satelite:
        logger.info("quadros de fim de ferramenta: " + ", ".join(q.__name__ for q in _QUADROS_FIM_DE_FERRAMENTA))
    estado = sp.EstadoDaConversa()
    chamadas = []  # chamadas de ferramenta da conversa (ChamadasObserver): fonte da habilidade ensinada por demonstração

    # 19/10 (caso real 13:59): o Gemini recebeu a resposta do cérebro e não falou; não havia registro do que ele
    # mandou de volta. Depois de cada entrega, as mensagens dele vão resumidas ao log até ela começar a falar.
    vigia = gw.ResultWatch()
    _mensagem_original = llm._handle_server_message

    async def _mensagem_vigiada(message):
        if vigia.active():
            try:
                logger.info(f"gemini [{sessao}] +{vigia.elapsed():.1f}s após o cérebro: "
                            f"{gw.summarize_server_message(message)}")
            except Exception:
                logger.exception("vigia do Gemini: falha ao resumir a mensagem")
        await _mensagem_original(message)

    llm._handle_server_message = _mensagem_vigiada

    def vigia_fala():
        latencia = vigia.speech_started()
        if latencia is not None:
            logger.info(f"gemini [{sessao}] começou a falar {latencia:.1f}s após o cérebro")

    async def vigia_silencio():
        await asyncio.sleep(gw.SILENCE_WARN_S)
        if vigia.silent_too_long():
            logger.warning(f"gemini [{sessao}] calado {gw.SILENCE_WARN_S:.0f}s após a resposta do cérebro")

    def vigiar_entrega():
        vigia.open()
        _manter(asyncio.create_task(vigia_silencio()))
    turnos = rc.Turnos(f"friday-satelite-{satelite_id}" if eh_satelite else "friday-pagina", sessao,
                       satelite_id if eh_satelite else "pagina", whisper=sussurro,  # 22/10: marca no Logbook
                       wake_word=sm.wake_word(corpo))  # 28/10: palavra que abriu, só no 1º turno

    async def avisar_pi(dados: dict):
        if eh_satelite:
            await worker.queue_frames([RTVIServerMessageFrame(data=dados)])

    cancelamento = cx.SessionCancel()  # botão vermelho (spec 2026-11-04): cancela o cérebro e as entregas da sessão

    async def entregar(tarefa, pedido):
        """Espera a resposta do cérebro e a entrega no primeiro silêncio, ou por push se a conversa acabou."""
        try:
            resposta = await tarefa
            if eh_satelite and nao_perturbe[0]:  # D11: resposta atrasada é fala espontânea — só no registro
                logger.info(f"cérebro [{sessao}] resposta calada pelo não perturbe")
                GRAVADOR.enviar(rc.linha_avulsa(turnos.chave, sessao, satelite_id, pedido, resposta,
                                                sm.MOTIVO_NAO_PERTURBE, datetime.now().timestamp(),
                                                whisper=turnos.whisper))
                return
            if await sp.esperar_silencio(estado):
                logger.info(f"cérebro [{sessao}] resposta em segundo plano entregue na conversa")
                await avisar_pi(sm.MSG_ENTREGANDO)
                if eh_satelite:
                    await asyncio.sleep(0.6)  # a Pi toca o som de início e abre o microfone
                turnos.entrega_do_cerebro(pedido, datetime.now().timestamp())
                vigiar_entrega()
                await worker.queue_frames(
                    [InputTextRawFrame(text=sp.texto_para_gemini(pedido, resposta))]
                )
            elif eh_satelite:
                logger.info(f"cérebro [{sessao}] conversa do satélite encerrada: resposta só no registro")
                GRAVADOR.enviar(rc.linha_avulsa(turnos.chave, sessao, satelite_id, pedido, resposta,
                                                "conversa encerrada antes da entrega", datetime.now().timestamp(),
                                                whisper=turnos.whisper))
            else:
                logger.info(f"cérebro [{sessao}] conversa encerrada: resposta por push")
                await push_ao_jose(sp.texto_push(pedido, resposta))
        except asyncio.CancelledError:
            if not cancelamento.cancelled:
                raise
            logger.info(f"cérebro [{sessao}] cancelado pelo botão vermelho")
            GRAVADOR.enviar(rc.linha_avulsa(turnos.chave, sessao, satelite_id, pedido, "",
                                            "cancelado pelo botão vermelho", datetime.now().timestamp(),
                                            whisper=turnos.whisper))
        except Exception:
            logger.exception(f"cérebro [{sessao}] falha na entrega em segundo plano")
            if not eh_satelite:
                await push_ao_jose(
                    sp.texto_push(pedido, "não consegui entregar a resposta; veja a conversa no app.")
                )
        finally:
            estado.pendentes -= 1
            if estado.aberta and not cancelamento.cancelled:
                await avisar_pendentes()

    # Pedidos ao cérebro ainda dentro da janela (06/10): a Pi precisa saber desde o INÍCIO, senão a
    # continuação de 5 s fecha a conversa enquanto ela espera a resposta (caso real: "Deixa eu ver" e fim).
    na_janela = [0]

    async def avisar_pendentes():
        await avisar_pi(sm.msg_pendentes(na_janela[0] + estado.pendentes))

    def seguir_em_segundo_plano(tarefa, pedido):
        if cancelamento.cancelled:  # cancelado na janela: nada de "segue em segundo plano" nem push depois
            return
        estado.pendentes += 1
        _manter(cancelamento.track(asyncio.create_task(entregar(tarefa, pedido))))

    async def cerebro_desta_sessao(params: FunctionCallParams):
        pedido = params.arguments.get("pedido", "")
        logger.info(f"cérebro [{sessao}] <- {pedido!r}")
        enviado = pedido + vs.demonstration_block(chamadas)
        tarefa = cancelamento.track(_manter(asyncio.create_task(chamar_cerebro(sm.pedido_com_origem(enviado, satelite_id) if eh_satelite else enviado, sessao))))
        na_janela[0] += 1
        await avisar_pendentes()
        try:
            pronta, resposta = await sp.esperar_com_janela(
                tarefa, sm.janela_do_cerebro(eh_satelite, JANELA_SECS, JANELA_SATELITE_SECS))
        except asyncio.CancelledError:
            # A função foi cancelada no meio da janela; o pedido continua e a resposta não se perde.
            na_janela[0] -= 1
            seguir_em_segundo_plano(tarefa, pedido)
            if not cancelamento.cancelled:
                _manter(asyncio.create_task(avisar_pendentes()))
            raise
        na_janela[0] -= 1
        if pronta:
            vigiar_entrega()
            await params.result_callback({"resposta": resposta})
            await avisar_pendentes()  # pode ir a 0: a Pi espera a fala dela antes de fechar
            return
        logger.info(f"cérebro [{sessao}] em segundo plano após {JANELA_SECS:.0f} s")
        seguir_em_segundo_plano(tarefa, pedido)
        await avisar_pendentes()
        await params.result_callback(
            {
                "status": "em andamento",
                "orientacao": sm.orientacao_em_andamento(eh_satelite),
            }
        )

    llm.register_function(
        "perguntar_ao_cerebro",
        cerebro_desta_sessao,
        timeout_secs=FUNCAO_TIMEOUT_SECS,
        cancel_on_interruption=False,
    )

    def _agora_brt():
        return datetime.now(timezone(timedelta(hours=-3))).isoformat(timespec="seconds")

    async def _registrar_uso(skill_id):
        try:
            async with _skills_lock:
                lista = vs.load()
                if any(s.get("id") == skill_id for s in lista):  # registro ilegível (lista vazia) nunca é regravado
                    vs.save(vs.mark_used(lista, skill_id, _agora_brt()))
        except Exception:
            logger.exception(f"[{sessao}] habilidade: falha ao registrar o uso")

    def _achar_habilidade(id_pedido):
        lista = vs.load()
        chave = str(id_pedido or "").strip().casefold()
        for s in lista:
            if str(s.get("id", "")).casefold() == chave:
                return s, lista
        return None, lista

    async def read_skill(params: FunctionCallParams):
        skill, lista = _achar_habilidade(params.arguments.get("id"))
        if skill is None:
            await params.result_callback({"erro": "não existe habilidade com esse id", "ids": [s.get("id") for s in lista]})
            return
        logger.info(f"[{sessao}] read_skill {skill['name']!r}")
        await _registrar_uso(skill["id"])
        await params.result_callback(vs.recipe(skill))

    async def run_skill(params: FunctionCallParams):
        skill, lista = _achar_habilidade(params.arguments.get("id"))
        if skill is None:
            await params.result_callback({"erro": "não existe habilidade com esse id", "ids": [s.get("id") for s in lista]})
            return
        plano = vs.run_plan(skill, nomes_mcp)

        async def um(i, passo):
            if mcp is None:
                return i, (False, "Home Assistant indisponível")
            try:
                # _call_tool_text: método do Pipecat 1.12 (fixado na imagem); devolve o texto da resposta do MCP
                texto = await mcp._call_tool_text(passo["tool"], passo["args"])
                return i, (vs.call_succeeded(texto), texto)
            except Exception as e:
                return i, (False, f"{type(e).__name__}: {e}")

        executar = [(i, p) for i, p in enumerate(plano) if p["run"]]
        if skill.get("ordered"):
            saidas = [await um(i, p) for i, p in executar]
        else:
            saidas = await asyncio.gather(*[um(i, p) for i, p in executar])
        r = vs.run_results(plano, dict(saidas), skill["name"])
        logger.info(f"[{sessao}] run_skill {skill['name']}: " + json.dumps([x["status"] for x in r["steps"]]))
        await _registrar_uso(skill["id"])
        await params.result_callback(r)

    async def search_skills(params: FunctionCallParams):
        await params.result_callback({"habilidades": vs.search(vs.load(), params.arguments.get("query", ""))})

    if habilidades:
        llm.register_function("read_skill", read_skill)
        llm.register_function("run_skill", run_skill, cancel_on_interruption=False, timeout_secs=60)
        if cortou:
            llm.register_function("search_skills", search_skills)

    async def encerrar_conversa(params: FunctionCallParams):
        logger.info(f"[{sessao}] encerrar_conversa: a Pi fecha na hora")
        await avisar_pi(sm.MSG_ENCERRAR)
        await params.result_callback({"ok": True}, properties=FunctionCallResultProperties(run_llm=False))

    async def alternar_nao_perturbe(params: FunctionCallParams):
        ligado = bool(params.arguments.get("ligado"))
        antes = nao_perturbe[0]
        nao_perturbe[0] = ligado
        logger.info(f"[{sessao}] não perturbe {'ligado' if ligado else 'desligado'}")
        await avisar_pi(sm.msg_nao_perturbe(ligado))
        await params.result_callback(sm.resultado_nao_perturbe(ligado, antes))

    # Canal de comandos com a Pi (spec 2026-10-12 §4): a Pi responde pelo RTVI (client-message "resposta").
    respostas_pi = {}

    async def comandar_pi(comando: dict):
        cid = secrets.token_hex(4)
        f = asyncio.get_running_loop().create_future()
        respostas_pi[cid] = f
        await avisar_pi(sm.msg_comando(cid, comando))
        try:
            return await asyncio.wait_for(f, 2.0)
        except asyncio.TimeoutError:
            return None
        finally:
            respostas_pi.pop(cid, None)

    portao_fala = sm.PortaoDeFala(sempre=True)  # 12/11: o marcador também sai em conversa comum

    async def volume_do_satelite(params: FunctionCallParams):
        try:
            comando = dict(sm.args_volume(params.arguments), acao="volume")
        except ValueError as e:
            await params.result_callback({"aplicado": False, "erro": str(e)})
            return
        r = sm.resultado_volume(await comandar_pi(comando))
        logger.info(f"[{sessao}] volume {comando} -> {r}")
        # D1: confirmação é o bip e o anel; com sucesso nada é falado.
        # 13/10: com run_llm=False o Pipecat não manda o resultado ao Gemini (só no próximo contexto) e o 3.8 fica com a
        # chamada sem resposta: repetia o volume a cada fala, até no "parar". A ferramenta é NON_BLOCKING (registro
        # abaixo) e o resultado vai direto, com scheduling SILENT (campo de topo do FunctionResponse; dentro do dicionário
        # o modelo o tratava como texto). Mesmo assim ele gera um turno e fala "<no speech>{pause}": o portão o descarta.
        if r["aplicado"]:
            portao_fala.armar()
            try:
                await llm._session.send_tool_response(function_responses=FunctionResponse(
                    name=params.function_name, id=params.tool_call_id, response=r,
                    scheduling=FunctionResponseScheduling.SILENT))
                llm._completed_tool_calls.add(params.tool_call_id)  # o Pipecat não reenvia no próximo contexto
            except Exception as e:
                logger.warning(f"[{sessao}] volume: resposta direta ao Gemini falhou ({e}); segue pelo contexto")
        await params.result_callback(r, properties=FunctionCallResultProperties(run_llm=not r["aplicado"]))

    async def timer_do_satelite(params: FunctionCallParams):
        try:
            comando = sm.args_do_timer(params.function_name, params.arguments)  # a ação é o nome da ferramenta
        except ValueError as e:
            await params.result_callback({"ok": False, "erro": str(e)})
            return
        r = sm.resultado_timer(await comandar_pi(comando))
        logger.info(f"[{sessao}] {params.function_name} {comando} -> {r}")
        await params.result_callback(r)

    trava_aprendizagem = sm.TravaAprendizagem()

    async def aprendizagem_de_voz(params: FunctionCallParams):
        r = sm.roteiro_aprendizagem(await comandar_pi({"acao": "vozes_consultar"}))
        trava_aprendizagem.passou()
        logger.info(f"[{sessao}] aprendizagem_de_voz -> {len(r['cadastrados']) if isinstance(r['cadastrados'], list) else r['cadastrados']} cadastrado(s)")
        await params.result_callback(r)

    async def comecar_aprendizagem_de_voz(params: FunctionCallParams):
        if not trava_aprendizagem.permite:
            logger.info(f"[{sessao}] comecar_aprendizagem_de_voz sem roteiro: recusado")
            await params.result_callback(sm.ERRO_SEM_ROTEIRO)
            return
        r = sm.resultado_comando(await comandar_pi({"acao": "cadastro_voz", "nome": params.arguments.get("nome")}))
        logger.info(f"[{sessao}] comecar_aprendizagem_de_voz -> {r}")
        await params.result_callback(r)

    async def definir_comodo(params: FunctionCallParams):
        area_id = params.arguments.get("area_id")
        _resultado, comando = sm.definir_comodo(apresentacao, area_id)
        resposta = await comandar_pi(comando) if comando else None
        r = sm.resultado_definir_comodo(apresentacao, area_id, resposta)
        logger.info(f"[{sessao}] definir_comodo {area_id!r} -> {r.get('ok')}")
        await params.result_callback(r)

    if apresentacao:  # só a conversa de apresentação tem a ferramenta
        llm.register_function("definir_comodo", definir_comodo)

    if eh_satelite:
        llm.register_function("encerrar_conversa", encerrar_conversa)
        llm.register_function("nao_perturbe", alternar_nao_perturbe)
        llm.register_function("volume_do_satelite", volume_do_satelite, cancel_on_interruption=False)
        for nome_da_ferramenta in sm.ACAO_DA_FERRAMENTA:
            llm.register_function(nome_da_ferramenta, timer_do_satelite)
        llm.register_function("aprendizagem_de_voz", aprendizagem_de_voz)
        # um "ok" dito por cima não pode cancelar o comando depois de a Pi já ter agendado (D28)
        llm.register_function("comecar_aprendizagem_de_voz", comecar_aprendizagem_de_voz, cancel_on_interruption=False)

    context = LLMContext()
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(context)

    # D7/D8: conversa sussurrada = turnos dirigidos pela Pi e voz do TTS sussurrado (whisper_mode.py)
    turnos_pi = wm.PiSpeechTurns(ready=lambda: bool(getattr(llm, "_ready_for_realtime_input", True))) if sussurro else None
    voz_sussurrada = None
    if sussurro:
        from google import genai as _genai
        _cliente_tts = _genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        _modelo_tts = os.environ.get("FRIDAY_WHISPER_TTS_MODEL", "models/gemini-3.8-flash-tts")
        _voz_tts = vs_cfg.voz(cfg_voz)  # a mesma voz da sessão Live
        voz_sussurrada = wm.WhisperVoice(lambda frase: wm.stream_whisper_tts(_cliente_tts, _modelo_tts, _voz_tts, frase))

    contador_pi = sm.ContadorFerramentas()  # dividido entre os sinais do satélite e a vigia de promessa

    async def avisar_gemini(texto: str):
        await worker.queue_frames([InputTextRawFrame(text=texto)])

    pipeline = Pipeline(
        [
            transport.input(),
            SeguraAudioAteGemini(llm),
            TextoDigitadoParaGemini(),
            user_aggregator,
            SinaisDaConversa(estado, ao_falar=vigia_fala),
            *([turnos_pi] if turnos_pi else []),
            llm,
            SegurarFalaDepoisDoVolume(portao_fala, sessao),
            *([voz_sussurrada] if voz_sussurrada else []),
            transport.output(),
            assistant_aggregator,
        ]
    )
    # A Playground mostra nome, argumentos e resultado de cada chamada de ferramenta. O padrão do
    # Pipecat (NONE) só manda o id da chamada; FULL expõe tudo ao navegador, aceitável na LAN da POC.
    worker = PipelineWorker(
        pipeline,
        # enable_usage_metrics: o log passa a mostrar os tokens de cada fala, com o áudio separado.
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        idle_timeout_secs=IDLE_TIMEOUT_SECS,
        observers=[RegistroObserver(turnos), UsageObserver(ab, sessao, arm, satelite_id if eh_satelite else None, settings_kwargs["model"]), ChamadasObserver(chamadas)]
                  + ([EntregaObserver(entrega.id)] if entrega else [])
                  + ([SinaisDoSatelite(avisar_pi, contador_pi)] if eh_satelite else [])
                  + [PromessaObserver(sessao, contador_pi if eh_satelite else None, avisar_pi, avisar_gemini,
                                      lambda: na_janela[0] + estado.pendentes > 0)],
        # A inatividade é decidida em on_idle_timeout: com resposta do cérebro a caminho, a conversa fica.
        cancel_on_idle_timeout=False,
        rtvi_observer_params=RTVIObserverParams(
            function_call_report_level={"*": RTVIFunctionCallReportLevel.FULL}
        ),
    )
    if worker.rtvi:
        @worker.rtvi.event_handler("on_client_message")
        async def ao_msg_da_pi(rtvi, msg):
            if msg.type == "resposta" and isinstance(msg.data, dict):
                f = respostas_pi.pop(msg.data.get("id"), None)
                if f and not f.done():
                    f.set_result(msg.data)
            elif msg.type == "atendida" and entrega:  # botão na conversa de entrega
                ENTREGAS.atendida(entrega.id)
            elif msg.type == "cancelar":  # botão vermelho (spec 2026-11-04): corta cérebro e entregas, fecha a sessão
                n = cancelamento.cancel()
                logger.info(f"botão vermelho [{sessao}]: {n} tarefa(s) cancelada(s)")
                await runner.cancel()
            elif msg.type == "speech_started" and turnos_pi:  # D7: a Pi percebeu a fala (inclusive sussurrada)
                await turnos_pi.started()
            elif msg.type == "speech_stopped" and turnos_pi:
                await turnos_pi.stopped()

    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)
    aquecimento = []

    @worker.event_handler("on_idle_timeout")
    async def on_idle_timeout(worker):
        if estado.manter_aberta():
            logger.info(f"inatividade com {estado.pendentes} pedido(s) ao cérebro a caminho: conversa mantida")
            return
        logger.info("inatividade: encerrando a conversa")
        estado.aberta = False
        await runner.cancel()

    ab_fim = []  # lista: o evento `end` do A/B sai uma vez só

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("cliente conectado")
        ATIVIDADE.opened(sessao)
        ab.start(sessao, arm, satelite_id if eh_satelite else None)
        if AQUECER_A_CADA_SECS > 0:
            aquecimento.append(asyncio.create_task(manter_cerebro_quente()))
        # satélite: nada de cumprimento; conversa aberta pela Pi: o que aconteceu (entrega ou timer), e ela fala
        inicial = ((sm.mensagem_de_abertura_apresentacao(apresentacao) if apresentacao else None)
                   or sm.mensagem_de_abertura(entrega.texto if entrega else None, timer, entrega.tipo if entrega else None)
                   or sm.mensagem_inicial(eh_satelite))
        if inicial:
            context.add_message(inicial)
        if entrega or timer or apresentacao:  # 14/10: o 1º turno dessas conversas também pode vir como "<no speech>{pause}" (visto na API)
            portao_fala.armar()
        await worker.queue_frames([LLMRunFrame()])  # sempre: sem ele o Gemini descarta o áudio
        if id_entrega and not entrega:
            await avisar_pi(sm.MSG_ENCERRAR)

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("cliente desconectado")
        ATIVIDADE.closed(sessao)
        if not ab_fim:
            ab_fim.append(True)
            ab.end(sessao, arm)
        estado.aberta = False
        await runner.cancel()

    try:
        await runner.run()
    finally:
        if not ab_fim:  # conversa encerrada sem o evento de desconexão (inatividade): fecha a janela do A/B
            ab_fim.append(True)
            ab.end(sessao, arm)
        # Pedidos ao cérebro em andamento NÃO são cancelados: com a conversa fechada, viram push.
        estado.aberta = False
        ATIVIDADE.closed(sessao)
        if entrega:
            ENTREGAS.terminou(entrega.id)
        for tarefa in aquecimento:
            tarefa.cancel()


def _registrar_ram():
    """08/10: RAM por processo do add-on no log, a cada FRIDAY_RAM_LOG_SECS (0 desliga)."""
    import threading, time
    import medir_ram

    intervalo = int(os.environ.get("FRIDAY_RAM_LOG_SECS", "600"))

    def laco():
        while True:
            try:
                procs = medir_ram.por_processo()
                logger.info("ram por processo: " + json.dumps(procs[:8], ensure_ascii=False))
            except Exception:
                logger.exception("ram por processo: falha na leitura")
            time.sleep(intervalo)

    if intervalo > 0:
        threading.Thread(target=laco, daemon=True, name="ram").start()


if __name__ == "__main__":
    from pipecat.runner.run import app as app_do_runner, main

    registrar_rotas(app_do_runner)
    registrar_recusa_voz(app_do_runner, VOICE_ENABLED_ARQ)
    _registrar_ram()
    ATIVIDADE.start_heartbeat(20)
    main()
