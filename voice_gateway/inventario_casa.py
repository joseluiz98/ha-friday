"""Inventário da casa (spec 2026-10-11-friday-latencia-voz §4.2, D5).

É o texto do prompt "Assist" que o MCP do HA gera a partir das entidades expostas ao Assist (nome, apelidos,
domínio, área). Vai para a instrução da voz no início de cada conversa: o que está exposto ao Assist é tudo o
que a Friday conhece da casa, como na Alexa. Nada de nomes escritos à mão aqui: o HA é a fonte.
Falha ou demora = "" (a conversa segue só com as ferramentas, como antes). O texto é tratado como opaco.
Teste: cd /srv/friday/app && python3 -m unittest -q test_inventario_casa
"""
import asyncio
import re

PEDIDO = {"jsonrpc": "2.0", "id": 1, "method": "prompts/get", "params": {"name": "Assist"}}


def extrair_texto(resposta) -> str:
    if not isinstance(resposta, dict) or not isinstance(resposta.get("result"), dict):
        return ""
    mensagens = resposta["result"].get("messages")
    if not isinstance(mensagens, list):
        return ""
    partes = []
    for m in mensagens:
        c = m.get("content") if isinstance(m, dict) else None
        if isinstance(c, dict) and c.get("type") == "text" and isinstance(c.get("text"), str) and c["text"].strip():
            partes.append(c["text"].strip())
    return "\n".join(partes)


_FRASE_DE_TIMERS = re.compile(r"^[ \t]*This device is not able to start timers\.?[ \t]*$\n?", re.M)


def sem_frase_de_timers(texto: str) -> str:
    """O prompt do Assist diz que o "dispositivo" não inicia timers (vale para o Assist do HA). No satélite a
    ferramenta timer existe, e a frase fazia o Gemini negar o pedido (14/10). Só essa linha sai; o resto segue opaco."""
    return _FRASE_DE_TIMERS.sub("", texto).rstrip("\n") if texto else texto


_FRASE_DE_GRUPO = re.compile(
    r"^.*When a user asks to turn on all devices of a specific type, ask the user to specify an area.*$\n?", re.M)


def sem_frase_de_grupo(texto: str) -> str:
    """O prompt do Assist manda pedir a área quando o pedido é "todos os dispositivos de um tipo" (vale para o
    Assist do HA). Na voz isso fez a Friday recusar (3x, 23/10) e competiria com qualquer diretriz ensinada.
    Só essa linha sai; o resto segue opaco."""
    return _FRASE_DE_GRUPO.sub("", texto).rstrip("\n") if texto else texto


async def _post_aiohttp(url: str, token: str, corpo: dict):
    import aiohttp  # só no add-on; os testes injetam o post
    cabecalhos = {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                  "Accept": "application/json, text/event-stream"}
    async with aiohttp.ClientSession() as sessao:
        async with sessao.post(url, json=corpo, headers=cabecalhos) as resp:
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status}")
            return await resp.json(content_type=None)


async def buscar(url: str, token: str, timeout: float = 2.0, post=_post_aiohttp):
    """(texto, None) ou ("", nome do erro). Nunca levanta exceção nem passa do prazo."""
    try:
        resposta = await asyncio.wait_for(post(url, token, PEDIDO), timeout)
    except Exception as e:  # inclui o TimeoutError do prazo
        return "", type(e).__name__
    return extrair_texto(resposta), None
