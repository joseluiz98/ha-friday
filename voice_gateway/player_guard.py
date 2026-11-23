"""Corrects a made-up music player id before a music script reaches Home Assistant.

Gemini sometimes derives a player id from a friendly name (``media_player.f_r_i_d_a_y_office``) instead of using
the one in the prompt. For a satellite session this module replaces any player that does not exist in HA with the
satellite's own player. Players that exist (even another room's) are never touched. Pure logic: no pipecat import,
so it runs in CI, which has no pipecat.
"""
import asyncio

import aiohttp
from loguru import logger

MUSIC_PREFIX = "script__music_"
# Scripts whose required player argument is a list called `players` / a single `player`.
PLAYERS_SCRIPTS = frozenset({"script__music_play", "script__music_stop_after"})
PLAYER_SCRIPTS = frozenset({"script__music_shuffle", "script__music_repeat", "script__music_now_playing",
                            "script__music_favorite", "script__music_seek"})
READ_TIMEOUT_S = 1.0


def _fix(candidates, own, exists):
    """Replace players that do not exist by `own`, dropping duplicates. Returns (new list, [(bad, own)])."""
    out, fixed = [], []
    for p in candidates:
        if not isinstance(p, str):
            out.append(p)
            continue
        if p != own and not exists.get(p):
            fixed.append((p, own))
            p = own
        if p not in out:
            out.append(p)
    return out, fixed


async def guard(tool, args, own, read_state):
    """Returns (new_args, early_result). Raises whatever `read_state` raises (use `safe_guard` in the bot)."""
    args = dict(args or {})
    if not tool.startswith(MUSIC_PREFIX) or not own:
        return args, None

    def as_list(v):
        return [v] if isinstance(v, str) else list(v) if isinstance(v, (list, tuple)) else []

    if tool in PLAYERS_SCRIPTS and not args.get("players"):
        args["players"] = [own]
    if tool in PLAYER_SCRIPTS and not args.get("player"):
        args["player"] = own

    keys = [k for k in ("players", "player", "to_players") if args.get(k)]
    named = {p for k in keys for p in as_list(args[k]) if isinstance(p, str)}
    named.add(own)
    ids = sorted(named)
    states = dict(zip(ids, await asyncio.gather(*(read_state(i) for i in ids))))

    for k in keys:
        value = args[k]
        fixed_list, fixed = _fix(as_list(value), own, states)
        for bad, good in fixed:
            logger.info(f"player corrigido: {bad} -> {good}")
        args[k] = fixed_list if isinstance(value, (list, tuple)) else (fixed_list[0] if fixed_list else value)

    resulting = []
    for k in keys:
        for p in as_list(args[k]):
            if p not in resulting:
                resulting.append(p)
    if resulting and all(states.get(p) == "unavailable" for p in resulting):
        return args, {"result": {"ok": False, "reason": "player_unavailable", "players": resulting}}
    return args, None


async def safe_guard(tool, args, own, read_state, timeout_s=READ_TIMEOUT_S):
    """`guard` that never blocks or breaks the call: any failure or a read past `timeout_s` = (args, None)."""
    try:
        return await asyncio.wait_for(guard(tool, args, own, read_state), timeout_s)
    except Exception as e:  # includes TimeoutError
        logger.warning(f"player guard: state read failed ({type(e).__name__}); call goes through unchanged")
        return args, None


async def read_state_http(entity_id, ha_url, token, timeout_s=READ_TIMEOUT_S):
    """State of an HA entity: the state string, None when it does not exist (404). Raises on any other failure."""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout_s)) as session:
        async with session.get(f"{ha_url.rstrip('/')}/api/states/{entity_id}",
                               headers={"Authorization": f"Bearer {token}"}) as resp:
            if resp.status == 404:
                return None
            if resp.status != 200:
                raise RuntimeError(f"HA responded {resp.status} for {entity_id}")
            return (await resp.json()).get("state")
