"""Log/data scrubbing for diagnostics (vendored from agents/common/friday_agent_core/diagnostics.py)."""

from __future__ import annotations

import re
from typing import Any

REDACTED = "**REDACTED**"
MAX_BYTES = 200_000
SENSITIVE_PARTS = ("secret", "token", "password", "psk", "senha", "authorization", "api_key", "apikey", "api-key")

_KW = r"(?:psk|password|passwd|token|secret|senha|api[_-]?key|apikey)"
_VAL = r"""("[^"\n]*"|'[^'\n]*'|[^\n,;&}\]"']+)"""
# Keep in sync with custom_components/friday/redact.py (a test compares them).
_PATTERNS = [
    (re.compile(r"""(?i)(authorization["']?\s*[:=]\s*["']?)(?:(?:Bearer|Basic|PVEAPIToken=?)\s+)?[^\s"',}]+"""),
     r"\1" + REDACTED),
    (re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=\-]{8,}"), r"\1 " + REDACTED),
    (re.compile(r"PVEAPIToken=\S+"), "PVEAPIToken=" + REDACTED),
    (re.compile(r"bot\d{6,}:[A-Za-z0-9_\-]{20,}"), "bot" + REDACTED),
    (re.compile(r"eyJ[\w\-]+\.eyJ[\w\-]+\.[\w\-]*"), REDACTED),
    (re.compile(r"(?i)([?&]key=)[^\s&\"']+"), r"\1" + REDACTED),
    (re.compile(r"""(?i)(["']?[\w.\-]*""" + _KW + r"""[\w.\-]*["']?\s*[=:]\s*)""" + _VAL), r"\1" + REDACTED),
    (re.compile(r"""(?i)(["']?\bkey["']?\s*[=:]\s*)""" + _VAL), r"\1" + REDACTED),
    (re.compile(r"""([=:]\s*["']?)[A-Za-z0-9+/_\-]{32,}={0,2}"""), r"\1" + REDACTED),
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
