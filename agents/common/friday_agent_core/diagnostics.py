"""Redaction and bounded command output shared by the agents' `diagnostics` command.

Never returns a secret: keys that look sensitive are masked, log lines are scrubbed by regex
and any literal secret the caller knows is replaced verbatim.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

REDACTED = "**REDACTED**"
MAX_BYTES = 200_000
SENSITIVE_PARTS = ("secret", "token", "password", "psk", "senha", "authorization")

_PATTERNS = [
    (re.compile(r"(?i)\b(Bearer)\s+[A-Za-z0-9._~+/=\-]+"), r"\1 " + REDACTED),
    (re.compile(r"PVEAPIToken=\S+"), "PVEAPIToken=" + REDACTED),
    (re.compile(r"""(?i)\b((?:psk|password|passwd|senha\w*|token|secret)\w*)(["']?\s*[=:]\s*["']?)[^\s"',;&}]+"""),
     r"\1\2" + REDACTED),
]


def is_sensitive_key(key: Any) -> bool:
    k = str(key).lower()
    return any(p in k for p in SENSITIVE_PARTS)


def redact_data(obj: Any, literals: tuple[str, ...] = ()) -> Any:
    """Recursively mask values under sensitive keys; scrub strings elsewhere."""
    if isinstance(obj, dict):
        return {k: (REDACTED if is_sensitive_key(k) else redact_data(v, literals)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_data(v, literals) for v in obj]
    if isinstance(obj, str):
        return redact_text(obj, literals)
    return obj


def redact_text(text: str, literals: tuple[str, ...] = ()) -> str:
    for lit in literals:
        if lit:
            text = text.replace(lit, REDACTED)
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


def cap(text: str, limit: int = MAX_BYTES) -> str:
    """Keep the tail (the newest log lines) when the text is too long."""
    data = text.encode("utf-8", "replace")
    if len(data) <= limit:
        return text
    return "[...truncated...]\n" + data[-limit:].decode("utf-8", "replace")


async def run_text(argv: list[str], timeout: float = 10, literals: tuple[str, ...] = (),
                   limit: int = MAX_BYTES) -> str:
    """Run a command off the event loop; stdout+stderr, redacted and capped. Never raises."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT)
    except OSError as err:
        return f"unavailable: {type(err).__name__}"
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return "unavailable: timeout"
    return cap(redact_text(out.decode("utf-8", "replace"), literals), limit)
