"""Embeddings model switch: cost estimate and background forced reindex.

OpenClaw 2026.9.9 reindexes with `openclaw memory index --agent friday --force` (the 04:15
friday-memory-index.service runs the same without --force). The command reads the model from the
config, so the reindex runs against a private temporary copy of openclaw.json (OPENCLAW_CONFIG_PATH)
that already has the new model; the live `memory.search.model` is written only after it succeeds.
Failure keeps the old model and pushes a log with an error code (never the command output)."""

from __future__ import annotations

import asyncio
import glob
import json
import logging
import os
import subprocess
import sys
from datetime import datetime

from . import models as modelsmod

LOG = logging.getLogger(__name__)

BRL_PER_USD = 7.4
# prices.json (USD per 1M input tokens) has no embeddings entry yet; Gemini embedding list price.
DEFAULT_USD_PER_M = 0.15
REINDEX_TIMEOUT = 1800
AGENT_ID = "friday"
CONTAINER = "friday"
WORKSPACE = "/srv/friday/home/.openclaw/workspace/friday"
# openclaw.json lists paths as the container sees them; on the VM they sit under the Samba mounts.
PATH_MAP = [("/share/", "/mnt/haos/share/"), ("/homeassistant/", "/mnt/haos/config/"),
            ("/mnt/haos/", "/mnt/haos/")]
COST_DIR = os.environ.get("FRIDAY_COST_DIR", "/opt/friday/cost")
STATE_COST = os.environ.get("FRIDAY_COST_STATE", "/srv/friday/state/cost")


def run_command(cmd: list, timeout: int) -> tuple:
    r = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       timeout=timeout, text=True, errors="replace", check=False)
    return r.returncode, r.stdout[-500:]


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
    def __init__(self, store, run=run_command, workspace=WORKSPACE, path_map=None,
                 price_usd_per_m=price_usd_per_m, factor=calibrated_factor,
                 push_state=None, push_log=None) -> None:
        self.store, self.run = store, run
        self.workspace = workspace
        self.path_map = PATH_MAP if path_map is None else path_map
        self._price, self._factor = price_usd_per_m, factor
        self.push_state, self.push_log = push_state, push_log
        self.state = "idle"
        self._task: asyncio.Task | None = None

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
        usd = tokens * (DEFAULT_USD_PER_M if price is None else price) / 1e6
        factor = self._factor() or BRL_PER_USD
        return {"chars": chars, "tokens": tokens, "usd": round(usd, 6), "brl": round(usd * factor, 4)}

    # --- reindex
    @property
    def running(self) -> bool:
        return self.state == "running"

    def start(self, model: str) -> bool:
        """Schedules the background reindex; False if one is already running."""
        if self.running:
            return False
        self.state = "running"
        self._task = asyncio.get_running_loop().create_task(self._run(model))
        return True

    async def wait(self) -> None:
        if self._task:
            await self._task

    def _reindex(self, model: str) -> str | None:
        """Blocking. Returns None on success or an error code."""
        live = self.store.openclaw_json
        cfg = modelsmod._read_json(live)
        if not cfg:
            return "config_unreadable"
        cfg.setdefault("memory", {}).setdefault("search", {})["model"] = model
        tmp = f"{live}.reindex.tmp"
        try:
            try:
                open(tmp, "w").close()
                os.chmod(tmp, 0o600)
                modelsmod._write_json(tmp, cfg)
                cmd = ["docker", "exec", "-e", f"OPENCLAW_CONFIG_PATH={tmp}", CONTAINER, "sh", "-c",
                       'HOME="${FRIDAY_OC_HOME:-$HOME}" exec openclaw "$@"', "oc",
                       "memory", "index", "--agent", AGENT_ID, "--force"]
                rc, _tail = self.run(cmd, REINDEX_TIMEOUT)
            except subprocess.TimeoutExpired:
                return "reindex_timeout"
            except OSError:
                return "reindex_error"
            if rc != 0:
                return f"reindex_failed_rc{rc}"
            try:  # success: only now the live config and the stored choice change
                live_cfg = modelsmod._read_json(live)
                live_cfg.setdefault("memory", {}).setdefault("search", {})["model"] = model
                modelsmod._write_json(live, live_cfg)
                ms = modelsmod._read_json(self.store.models_path)
                ms["embeddings"] = model
                modelsmod._write_json(self.store.models_path, ms)
            except OSError:
                return "write_failed"
            return None
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

    async def _set(self, state: str) -> None:
        self.state = state
        if self.push_state:
            await self.push_state({"embeddings_reindex": state})

    async def _run(self, model: str) -> None:
        await self._set("running")
        try:
            code = await asyncio.to_thread(self._reindex, model)
        except Exception as err:  # noqa: BLE001 - never let the task die silently
            LOG.warning("reindex crashed: %s", type(err).__name__)
            code = "reindex_error"
        if code is None:
            await self._set("idle")
            if self.push_log:
                await self.push_log(f"Embeddings model changed to {model}; memory reindexed", {"model": model})
        else:
            await self._set("failed")
            if self.push_log:
                await self.push_log(f"Embeddings reindex to {model} failed; kept the previous model",
                                    {"model": model, "code": code})
