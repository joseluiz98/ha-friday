"""Embeddings reindex cost estimate (read-only).

Switching the embeddings model is NOT supported (`reindex_unsupported`): in OpenClaw 2026.9.9 the memory index
lives inside the agent's own sqlite (agents/friday/agent/openclaw-agent.sqlite, together with sessions, WAL and
a generation lock) and `openclaw memory index --force` rebuilds it in place with the model of the live config.
Building into an isolated store and swapping it atomically is not possible without losing writes made by the
running gateway, and the index model cannot be verified before the swap. Only the estimate is offered."""

from __future__ import annotations

import glob
import json
import logging
import os
import sys
from datetime import datetime

from . import models as modelsmod

LOG = logging.getLogger(__name__)

BRL_PER_USD = 7.4
# prices.json (USD per 1M input tokens) has no embeddings entry yet; Gemini embedding list price.
DEFAULT_USD_PER_M = 0.15
WORKSPACE = "/srv/friday/home/.openclaw/workspace/friday"
# openclaw.json lists paths as the container sees them; on the VM they sit under the Samba mounts.
PATH_MAP = [("/share/", "/mnt/haos/share/"), ("/homeassistant/", "/mnt/haos/config/"),
            ("/mnt/haos/", "/mnt/haos/")]
COST_DIR = os.environ.get("FRIDAY_COST_DIR", "/opt/friday/cost")
STATE_COST = os.environ.get("FRIDAY_COST_STATE", "/srv/friday/state/cost")


def price_usd_per_m(model: str) -> float | None:
    """Input-token price from the cost instrumentation's prices.json, None if the model has none."""
    try:
        if COST_DIR not in sys.path:
            sys.path.insert(0, COST_DIR)
        import pricing  # noqa: PLC0415 - /opt/friday/cost
        p = pricing.price_for(pricing.load_prices(), model, datetime.now().date())
        return float(p["input_text"]) if p and p.get("input_text") is not None else None
    except Exception:  # noqa: BLE001
        return None


def calibrated_factor() -> float | None:
    """The R$/US$ factor of the cost sensors (cost_report.Ctx.factor), None when unavailable."""
    try:
        if COST_DIR not in sys.path:
            sys.path.insert(0, COST_DIR)
        import cost_report  # noqa: PLC0415
        import ledger  # noqa: PLC0415
        today = datetime.now(ledger.BRT).date()
        return float(cost_report.Ctx(STATE_COST, today, None).factor(today)[0])
    except Exception:  # noqa: BLE001
        return None


class Reindexer:
    def __init__(self, store, workspace=WORKSPACE, path_map=None,
                 price_usd_per_m=price_usd_per_m, factor=calibrated_factor) -> None:
        self.store = store
        self.workspace = workspace
        self.path_map = PATH_MAP if path_map is None else path_map
        self._price, self._factor = price_usd_per_m, factor

    # --- estimate
    def _host(self, path: str) -> str:
        for src, dst in self.path_map:
            if path == src.rstrip("/") or path.startswith(src if src.endswith("/") else src + "/"):
                return dst.rstrip("/") + path[len(src.rstrip("/")):]
        return path

    def _files(self) -> list:
        files: list = []
        ws = self.workspace
        files += [p for p in glob.glob(os.path.join(ws, "*.md"))]
        files += glob.glob(os.path.join(ws, "memory", "**", "*.md"), recursive=True)
        cfg = modelsmod._read_json(self.store.openclaw_json)
        for item in (cfg.get("memory", {}).get("search", {}).get("extraPaths") or []):
            path, pattern = (item.get("path"), item.get("pattern") or "*.md") if isinstance(item, dict) \
                else (item, "*.md")
            if not isinstance(path, str):
                continue
            path = self._host(path)
            if os.path.isdir(path):
                files += glob.glob(os.path.join(path, "**", pattern), recursive=True)
            elif os.path.isfile(path):
                files.append(path)
        return sorted({p for p in files if os.path.isfile(p) and ".bak" not in os.path.basename(p)})

    def estimate(self, model: str) -> dict:
        chars = 0
        for p in self._files():
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    chars += len(f.read())
            except OSError:
                continue
        tokens = chars // 4
        price = self._price(model)
        factor = self._factor()
        usd = tokens * (DEFAULT_USD_PER_M if price is None else price) / 1e6
        return {"chars": chars, "tokens": tokens, "usd": round(usd, 6),
                "brl": round(usd * (factor or BRL_PER_USD), 4),
                "price_known": price is not None, "factor_calibrated": bool(factor)}
