"""Canal da casa (spec 2026-10-12 §3): o cérebro entrega mensagens pelo satélite (POST /deliver) e o satélite
avisa o dono de timer não atendido (POST /satellite_alert → Telegram). Lógica pura com IO injetado: roda sem o
Pipecat e sem rede (test_canal_casa.py). Quem liga isto ao servidor do bot é o bot.py."""
import asyncio, hmac, json, os, re, secrets, time
from datetime import datetime, timedelta, timezone

import satelite_modo as sm

MAX_TEXTO = 1000
ESPERA_CONEXAO_S = 90.0  # campainha → a Pi abrir a conversa (inclui a fila da Pi)
ESPERA_FIM_S = 300.0  # conversa de entrega aberta → fechada
_SID = re.compile(r"^sat-[0-9a-f]{6}$")
_BRT = timezone(timedelta(hours=-3))  # sem horário de verão desde 2019; não depende do tzdata da imagem


class Entrega:
    def __init__(self, eid, destino, texto, tipo):
        self.id, self.destino, self.texto, self.tipo = eid, destino, texto, tipo
        self.conexao = asyncio.Event()
        self.atendida_ = False
        self.fim = asyncio.get_running_loop().create_future()


class Entregas:
    """Entregas em andamento, no processo do bot: /deliver cria; a conversa aberta pela Pi as encontra pelo id."""

    def __init__(self):
        self._d = {}

    def criar(self, destino, texto, tipo) -> Entrega:
        e = Entrega(secrets.token_hex(6), destino, texto, tipo)
        self._d[e.id] = e
        return e

    def ativas(self) -> list:
        return list(self._d)

    def conectou(self, eid):
        e = self._d.get(eid)
        if e:
            e.conexao.set()
        return e

    def atendida(self, eid):
        e = self._d.get(eid)
        if e:
            e.atendida_ = True

    def terminou(self, eid):
        e = self._d.get(eid)
        if e and not e.fim.done():
            e.fim.set_result("entregue" if e.atendida_ else "nao_entregue")

    def remover(self, eid):
        self._d.pop(eid, None)


def validar_pedido(corpo):
    if not isinstance(corpo, dict):
        raise ValueError("corpo precisa ser um objeto")
    destino, texto, tipo = corpo.get("destino"), corpo.get("texto"), corpo.get("tipo", "aviso")
    if destino != "jose" and not (isinstance(destino, str) and destino.startswith("satelite:")
                                  and _SID.match(destino.split(":", 1)[1])):
        raise ValueError("destino é 'jose' ou 'satelite:<id>'")
    texto = texto.strip() if isinstance(texto, str) else ""
    if not 1 <= len(texto) <= MAX_TEXTO:
        raise ValueError(f"texto de 1 a {MAX_TEXTO} caracteres")
    if tipo not in sm.MOTIVO_DA_ENTREGA:  # D25: cada tipo tem a sua abertura, com a fonte verdadeira
        raise ValueError("tipo é " + ", ".join(repr(t) for t in sm.MOTIVO_DA_ENTREGA))
    return destino, texto, tipo


def pedido_do_lembrete(evento):
    """Aviso de conclusão do agendador do OpenClaw (POST /reminder, `delivery.completionDestination`) → pedido de
    entrega no satélite. O resultado final do agendamento vem em `summary`; o Telegram já recebeu o mesmo texto pelo
    `announce` do OpenClaw. None = nada a tocar (run com erro, sem texto ou silenciosa)."""
    if not isinstance(evento, dict) or evento.get("action") != "finished" or evento.get("status") != "ok":
        return None
    texto = evento.get("summary")
    texto = texto.strip() if isinstance(texto, str) else ""
    if not texto or texto == "NO_REPLY":
        return None
    return {"destino": "jose", "tipo": "lembrete", "texto": texto[:MAX_TEXTO]}


def ler_satelites(caminho) -> dict:
    """/share/friday/satellites.json (600): {id: {"comodo", "senha", "ip"}} — a senha é a da porta 8700."""
    try:
        with open(caminho) as f:
            dados = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(dados, dict):
        return {}
    return {k: v for k, v in dados.items() if _SID.match(str(k)) and isinstance(v, dict)
            and isinstance(v.get("senha"), str) and len(v["senha"]) >= 32 and isinstance(v.get("ip"), str)}


def ler_satelites_com_cache(principal: str, cache: str) -> dict:
    """Cadastro no compartilhamento do HAOS, com cópia local: se o HAOS estiver fora, o aviso de timer da Pi
    continua aceito (spec 2026-10-18 §4.9). Um cadastro vazio nunca apaga o cache."""
    dados = ler_satelites(principal)
    if dados:
        try:
            tmp = f"{cache}.tmp.{os.getpid()}"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(dados, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, cache)
        except OSError:
            pass
        return dados
    return ler_satelites(cache)


async def read_satellites_without_blocking(primary: str, cache: str, timeout_s: float = 2.0,
                                           read=ler_satelites_com_cache) -> dict:
    """ler_satelites_com_cache numa thread, com prazo. Com o Samba fora, o open() do cadastro em /share ficava preso
    até o timeout do CIFS (~25 s) DENTRO do laço de eventos, congelando todas as sessões de voz a cada aviso
    (revisão final de 19/10). Estourou o prazo: vale o cache local; a thread presa termina sozinha depois."""
    try:
        return await asyncio.wait_for(asyncio.to_thread(read, primary, cache), timeout_s)
    except asyncio.TimeoutError:
        return ler_satelites(cache)


def _bearer(cabecalho):
    return cabecalho[len("Bearer "):] if isinstance(cabecalho, str) and cabecalho.startswith("Bearer ") else None


def token_confere(esperado: str, cabecalho) -> bool:
    dado = _bearer(cabecalho)
    return bool(esperado) and dado is not None and hmac.compare_digest(dado, esperado)


def autenticar_satelite(satelites: dict, cabecalho):
    dado = _bearer(cabecalho)
    if dado is None:
        return None
    for sid, s in satelites.items():
        if hmac.compare_digest(dado, s["senha"]):
            return sid
    return None


async def _tocar(deps, sid, eid):
    """Campainha no IP do cabo; se ele não responder, no IP do Wi-Fi informado no status (spec D9)."""
    sat = deps.satelites[sid]
    for ip in (sat["ip"], await deps.ip_wifi(sid)):
        if not ip:
            continue
        try:
            return await deps.campainha(ip, sat["senha"], eid)
        except Exception:
            continue
    return None


async def entregar(corpo, deps) -> dict:
    t0 = time.time()
    linha = {"pedido_em": round(t0, 1)}
    try:
        destino, texto, tipo = validar_pedido(corpo)
    except ValueError as e:
        return {"resultado": "erro", "erro": str(e)}
    linha.update(destino=destino, tipo=tipo)
    if destino == "jose":
        try:
            presenca = await deps.presenca()
        except Exception:
            presenca = None  # HA fora do ar: tenta o satélite; o Telegram cobre a falha (spec §3)
        if presenca not in (None, "home", "unknown", "unavailable"):  # desconhecida: tenta o satélite
            return _fim(deps, linha, {"resultado": "fora_de_casa"})
        sid = sorted(deps.satelites)[0] if deps.satelites else None
    else:
        sid = destino.split(":", 1)[1]
    if sid not in deps.satelites:
        return _fim(deps, linha, {"resultado": "sem_satelite"})
    e = deps.entregas.criar(destino, texto, tipo)
    linha["id"] = e.id
    try:
        resposta = await _tocar(deps, sid, e.id)
        linha["campainha_em"], linha["campainha"] = round(time.time(), 1), resposta
        if resposta is None:
            return _fim(deps, linha, {"resultado": "sem_satelite"})
        if resposta == "calado":
            return _fim(deps, linha, {"resultado": "calado", "satelite": sid})
        if resposta not in ("vou_abrir", "na_fila"):
            return _fim(deps, linha, {"resultado": "erro", "erro": f"resposta da Pi: {resposta}"})
        try:
            await asyncio.wait_for(e.conexao.wait(), deps.espera_conexao_s)
        except asyncio.TimeoutError:
            return _fim(deps, linha, {"resultado": "nao_entregue", "satelite": sid})
        linha["conectou_em"] = round(time.time(), 1)
        try:
            resultado = await asyncio.wait_for(asyncio.shield(e.fim), deps.espera_fim_s)
        except asyncio.TimeoutError:
            resultado = "entregue" if e.atendida_ else "nao_entregue"
        return _fim(deps, linha, {"resultado": resultado, "satelite": sid})
    finally:
        deps.entregas.remover(e.id)


def _fim(deps, linha, resultado):
    linha.update(resultado, fim_em=round(time.time(), 1))
    deps.registrar(linha)
    return resultado


def e_aviso_de_cadastro(corpo) -> bool:
    return isinstance(corpo, dict) and corpo.get("tipo") == "cadastro_voz"


def _texto_cadastro_voz(corpo, sat: dict) -> str:
    """Spec 2026-10-15 D18: resultado do programa de aprendizagem de voz (Telegram e entrega falada)."""
    nome = corpo.get("nome")
    if not isinstance(nome, str) or not 1 <= len(nome.strip()) <= 20:
        raise ValueError("nome inválido")
    if not isinstance(corpo.get("aprovado"), bool):
        raise ValueError("aprovado inválido")
    onde = f"no satélite do {sat.get('comodo')}" if sat.get("comodo") else "no satélite"
    n = corpo.get("numeros") if isinstance(corpo.get("numeros"), dict) else {}
    num = lambda k: n.get(k) if isinstance(n.get(k), str) and len(n.get(k)) <= 12 else None
    if corpo["aprovado"]:
        partes = [f"tentativas reconhecidas: {num('nova')}" if num("nova") else None,
                  f"disparos falsos barrados: {num('falsas_barradas')}" if num("falsas_barradas") else None]
        extra = "; ".join(p for p in partes if p)
        return f"Programa de aprendizagem de voz {onde}: a voz de {nome.strip()} foi aprendida" + \
            (f" ({extra})" if extra else "") + "."
    motivo = corpo.get("motivo") if isinstance(corpo.get("motivo"), str) else "motivo não informado"
    return (f"Programa de aprendizagem de voz {onde}: a voz de {nome.strip()} não foi aprendida ({motivo[:200]}). "
            "O satélite continua como antes.")


def texto_aviso(corpo, sat: dict) -> str:
    if e_aviso_de_cadastro(corpo):
        return _texto_cadastro_voz(corpo, sat)
    if not isinstance(corpo, dict) or corpo.get("tipo") != "timer_nao_atendido":
        raise ValueError("tipo de aviso desconhecido")
    d = corpo.get("duracao_s")
    if isinstance(d, bool) or not isinstance(d, int) or d < 1:
        raise ValueError("duracao_s inválida")
    t = sm.timer_vencido({"timer": {"nome": corpo.get("nome"), "duracao_s": d}})
    qual = f"O timer «{t['nome']}»" if t["nome"] else "Um timer sem nome"
    venceu = corpo.get("venceu_em")
    quando = ""
    if isinstance(venceu, (int, float)) and not isinstance(venceu, bool):
        quando = f" (venceu às {datetime.fromtimestamp(venceu, _BRT):%H:%M})"
    onde = f"no satélite do {sat.get('comodo')}" if sat.get("comodo") else "no satélite"
    return f"{qual} de {sm.duracao_falada(d)} {onde} tocou por 5 minutos e ninguém parou{quando}."


async def play_for_satellite(sid: str, query, deps) -> dict:
    """R11 (spec 2026-10-21): a Pi pede a música do alarme. Toca no player do cadastro do satélite (`player`), pelo
    script music_play do HA. Nunca levanta: qualquer falha = ok False (o alarme da Pi segue como um timer normal)."""
    if not isinstance(query, str) or "\n" in query or "\r" in query or not 1 <= len(query.strip()) <= 80:
        return {"ok": False, "erro": "query inválida"}
    player = ((deps.satelites or {}).get(sid) or {}).get("player")
    if not player:
        return {"ok": False, "erro": "satélite sem player no cadastro"}
    try:
        r = await deps.play_music(player, query.strip())
    except Exception as e:
        return {"ok": False, "erro": type(e).__name__}
    if not isinstance(r, dict) or not r.get("ok"):
        return {"ok": False}
    return {"ok": True, "title": r.get("title")}


async def mandar_telegram(texto, direto, via_cerebro) -> str:
    """D14: envio direto pelo OpenClaw (sem turno do cérebro); se falhar, um turno do cérebro manda."""
    try:
        await direto(texto)
        return "telegram"
    except Exception:
        pass
    try:
        await via_cerebro(texto)
        return "cerebro"
    except Exception:
        return "falhou"
