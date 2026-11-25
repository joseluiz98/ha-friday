"""Parar os timers que tocam, em todos os satélites (spec 2026-11-24, objetivo 6): a ferramenta `parar_timers` manda
`timers_parar` a cada satélite cadastrado (o mesmo caminho dos anúncios: cabo e, se não responde, Wi-Fi). Sem nome nem
cômodo para todos os que tocam, como o "stop" da Echo. Um satélite que não responde não impede os outros: ele aparece
em `sem_resposta`. `alarmes_nas_casas` lê o que toca nos outros satélites (para a conversa saber quais timers tocam)."""
import asyncio
import unicodedata

MAX_TEXTO = 40


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t.strip().casefold())
    return "".join(c for c in t if not unicodedata.combining(c))


def args_parar_timers(a) -> dict:
    if not isinstance(a, dict):
        raise ValueError("argumentos inválidos")
    r = {}
    for k in ("nome", "comodo"):
        v = a.get(k)
        if v is None:
            r[k] = None
            continue
        if not isinstance(v, str):
            raise ValueError(f"{k} é texto")
        v = v.strip()
        if len(v) > MAX_TEXTO:
            raise ValueError(f"{k} longo demais")
        r[k] = v or None
    return r


async def _ips(deps, sid: str) -> list:
    sat, ips = deps.satelites[sid], []
    for origem in (lambda: sat.get("ip"), lambda: deps.ip_wifi(sid)):
        try:
            ip = origem()
            ip = await ip if asyncio.iscoroutine(ip) else ip
        except Exception:
            continue
        if ip and ip not in ips:
            ips.append(ip)
    return ips


async def _falar_com(deps, sid: str, pedido):
    """`pedido(ip, senha)` no cabo e, se falhar, no Wi-Fi; None se nenhum respondeu."""
    for ip in await _ips(deps, sid):
        try:
            return await pedido(ip, deps.satelites[sid]["senha"])
        except Exception:
            continue
    return None


async def parar_em_todos(deps, nome=None, comodo=None) -> dict:
    alvos = dict(deps.satelites)
    if comodo:
        alvos = {sid: s for sid, s in alvos.items() if _norm(s.get("comodo") or "") == _norm(comodo)}
        if not alvos:
            return {"ok": False, "erro": "nenhum satélite nesse cômodo",
                    "comodos": sorted(s.get("comodo") or sid for sid, s in deps.satelites.items())}

    async def um(sid):
        async def pedido(ip, senha):
            return await deps.comandar(ip, senha, {"acao": "timers_parar", "nome": nome})
        try:
            return await asyncio.wait_for(_falar_com(deps, sid, pedido), getattr(deps, "comando_timeout_s", 3.0))
        except Exception:
            return None

    respostas = await asyncio.gather(*[um(sid) for sid in alvos])
    parados, sem_resposta = [], []
    for (sid, sat), r in zip(alvos.items(), respostas):
        onde = sat.get("comodo") or sid
        if not isinstance(r, dict) or not r.get("ok"):  # sem resposta, ou programa antigo sem o comando
            sem_resposta.append(onde)
            continue
        for t in r.get("parados") or []:
            parados.append({"timer": t.get("nome") or "sem nome", "comodo": onde})
    return {"ok": True, "parados": parados, "sem_resposta": sem_resposta}


async def alarmes_nas_casas(deps, excluir=None) -> list:
    """O que toca agora nos OUTROS satélites: [{"nome", "comodo"}]. Quem não responde em 1 s (ou é de programa antigo,
    sem `alarmes` no status) fica de fora: nunca atrasa nem impede a conversa."""
    async def um(sid):
        async def pedido(ip, senha):
            return await deps.status(ip, senha)
        try:
            return await asyncio.wait_for(_falar_com(deps, sid, pedido), getattr(deps, "status_timeout_s", 1.0))
        except Exception:
            return None

    ids = [sid for sid in deps.satelites if sid != excluir]
    respostas = await asyncio.gather(*[um(sid) for sid in ids])
    r = []
    for sid, st in zip(ids, respostas):
        for t in (st.get("alarmes") if isinstance(st, dict) else None) or []:
            if isinstance(t, dict):
                r.append({"nome": t.get("nome"), "comodo": deps.satelites[sid].get("comodo") or sid})
    return r
