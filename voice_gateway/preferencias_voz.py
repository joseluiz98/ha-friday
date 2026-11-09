"""Preferências que o cérebro aprendeu (USER.md da friday), levadas à voz no início de cada conversa (06/10).
A voz (Gemini Live) começa cada sessão só com as instruções fixas e resolve comandos de casa sozinha; sem isto,
o que o dono ensina por voz fica no cérebro e a voz não usa. Lógica pura (teste: test_preferencias_voz.py)."""
import re

import local_config

CAMINHO = "/srv/friday/home/.openclaw/workspace/friday/USER.md"
_META = re.compile(r"<!--.*status:\s*(\w+).*-->")
_OBSERVED = re.compile(r"observed:\s*([\w-]+)")
_LUCID_TAG = re.compile(r"\s*<!-- lucid:.*?-->\s*$")  # 23/10: a vista do Lucid compara o texto sem a tag


def diretrizes_ativas(texto: str) -> list:
    """Diretrizes `status: active` da seção `## Directives` (o exemplo do cabeçalho fica fora)."""
    partes = texto.split("\n## Directives", 1)
    if len(partes) < 2:
        return []
    secao = partes[1].split("\n## ", 1)[0]
    ativas, status = [], None
    for linha in secao.splitlines():
        m = _META.search(linha)
        if m:
            status = m.group(1)
        elif linha.startswith("- ") and status is not None:
            if status == "active":
                ativas.append(linha[2:].strip())
            status = None
    return ativas


def all_active(texto: str) -> list:
    """(observed, texto) de todo bloco `status: active` do arquivo inteiro, fora de blocos de código cercados
    por ``` (o exemplo do cabeçalho). A posição no arquivo não importa (o rodapé `## Related` já engoliu uma)."""
    ativas, status, observed, cerca = [], None, "", False
    for linha in texto.splitlines():
        if linha.lstrip().startswith("```"):
            cerca = not cerca
            continue
        if cerca:
            continue
        m = _META.search(linha)
        if m:
            status = m.group(1)
            o = _OBSERVED.search(linha)
            observed = o.group(1) if o else ""
        elif linha.startswith("- ") and status is not None:
            if status == "active":
                ativas.append((observed, _LUCID_TAG.sub("", linha[2:]).strip()))
            status = None
    return ativas


def contexto(diretrizes: list) -> str:
    # Informação, não ordem (regra de autonomia da Friday).
    if not diretrizes:
        return ""
    return ("Preferências que o cérebro aprendeu com o " + local_config.owner() + " em conversas anteriores (arquivo USER.md, "
            "podem não se aplicar a esta conversa):\n" + "\n".join("- " + d for d in diretrizes))


def ler(caminho: str = CAMINHO) -> str:
    try:
        with open(caminho, encoding="utf-8") as f:
            return contexto([t for _, t in all_active(f.read())])
    except OSError:
        return ""
