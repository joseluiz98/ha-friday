"""Estado de um player do HA para o fato de música da abertura (spec 2026-10-21 §6.4). Teto curto: a leitura nunca
atrasa nem impede a abertura da conversa; qualquer erro ou prazo = None (a abertura sai sem o estado)."""
import asyncio

import aiohttp


async def player_state(entity_id: str, ha_url: str, token: str, timeout_s: float = 0.3):
    if not entity_id:
        return None
    try:
        async def ler():
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout_s)) as session:
                async with session.get(f"{ha_url.rstrip('/')}/api/states/{entity_id}",
                                       headers={"Authorization": f"Bearer {token}"}) as resp:
                    resp.raise_for_status()
                    return await resp.json()
        return await asyncio.wait_for(ler(), timeout_s)
    except Exception:
        return None


async def music_fact_for(sid: str, read_catalog, read_state, make_fact) -> str:
    """Fato de música da abertura: o player do satélite vem do cadastro (`player`); sem ele, nada. `read_catalog()`
    e `read_state(entity_id)` são corrotinas injetadas; qualquer falha = sem fato (nunca impede a abertura)."""
    try:
        player = ((await read_catalog()).get(sid) or {}).get("player")
        if not player:
            return ""
        return make_fact(player, await read_state(player))
    except Exception:
        return ""
