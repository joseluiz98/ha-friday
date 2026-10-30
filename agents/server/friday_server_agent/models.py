"""Models per role, voice settings and the pending brain change of the server agent.

Voice model and voice settings live in `voice-settings.json` (the Voice Gateway reads it on every
conversation); the Lucid model in `models.json` (read on every run). The brain model is
`agents.defaults.model.primary` of openclaw.json: changing it restarts the container, so it is only
done with no conversation open (otherwise it stays `pending_brain` until `apply_pending` finds the line idle).
The Google API key is read from the secrets file and never logged."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import time
import urllib.request

VOICES = ["Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede", "Callirrhoe", "Autonoe",
          "Enceladus", "Iapetus", "Umbriel", "Algieba", "Despina", "Erinome", "Algenib", "Rasalgethi",
          "Laomedeia", "Achernar", "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird",
          "Zubenelgenubi", "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat"]
RATES = ("slow", "normal", "fast")
DEFAULT_VOICE_MODEL = "gemini-3.8-live"
DEFAULT_LUCID = "gemini-3.8-flash"
DEFAULT_EMBEDDINGS = "gemini-embedding-2"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
LIST_URL = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000"


def _read_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: str, data: dict) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    try:  # keep the original's mode and owner (openclaw.json is not ours alone)
        st = os.stat(path)
        os.chmod(tmp, st.st_mode & 0o7777)
        try:
            os.chown(tmp, st.st_uid, st.st_gid)
        except PermissionError:
            pass
    except FileNotFoundError:
        pass
    os.replace(tmp, path)


def google_fetch(key_file: str = "/srv/friday/secrets/gemini_api_key"):
    """Returns a callable listing the models (free call); the key goes in a header, never in logs."""
    def fetch() -> list:
        with open(key_file, encoding="utf-8") as f:
            key = f.read().strip()
        out, url = [], LIST_URL
        for _ in range(10):
            req = urllib.request.Request(url, headers={"x-goog-api-key": key})
            with urllib.request.urlopen(req, timeout=15) as r:
                page = json.load(r)
            out += page.get("models", [])
            token = page.get("nextPageToken")
            if not token:
                break
            url = f"{LIST_URL}&pageToken={token}"
        return out
    return fetch


class ModelStore:
    def __init__(self, state_dir, openclaw_json, fetch, log_tail, restart, env_file=None) -> None:
        self.state_dir, self.openclaw_json = state_dir, openclaw_json
        self.fetch, self.log_tail, self.restart = fetch, log_tail, restart
        self.env_file = env_file
        self.reindexer = None  # embeddings.Reindexer, set by __main__ (None = embeddings switch unavailable)
        self.voice_path = os.path.join(state_dir, "voice-settings.json")
        self.models_path = os.path.join(state_dir, "models.json")

    # --- reading
    def _env_voice(self) -> str | None:
        try:
            with open(self.env_file, encoding="utf-8") as f:
                for line in f:
                    if line.startswith("FRIDAY_VOICE="):
                        return line.split("=", 1)[1].strip().strip("\"'") or None
        except (OSError, TypeError):
            pass
        return None

    def _brain(self) -> str:
        primary = (_read_json(self.openclaw_json).get("agents", {}).get("defaults", {})
                   .get("model", {}).get("primary", ""))
        return primary.split("/", 1)[-1] if primary else ""

    def _embeddings(self, ms: dict) -> str:
        live = (_read_json(self.openclaw_json).get("memory", {}).get("search", {}).get("model"))
        return live or ms.get("embeddings") or DEFAULT_EMBEDDINGS

    def current(self) -> dict:
        vs, ms = _read_json(self.voice_path), _read_json(self.models_path)
        return {
            "voice": str(vs.get("model") or DEFAULT_VOICE_MODEL).removeprefix("models/"),
            "brain": self._brain(),
            "lucid": ms.get("lucid") or DEFAULT_LUCID,
            "embeddings": self._embeddings(ms),
            "voice_name": vs.get("voice") or self._env_voice() or "Charon",
            "speaking_rate": vs.get("speaking_rate") if vs.get("speaking_rate") in RATES else "normal",
            "brief_mode": bool(vs.get("brief_mode", False)),
            "pending_brain": ms.get("pending_brain"),
        }

    def list_models(self) -> dict:
        groups: dict[str, list] = {"voice": [], "brain": [], "embeddings": []}
        for item in self.fetch():
            name = str(item.get("name", "")).removeprefix("models/")
            methods = item.get("supportedGenerationMethods") or []
            if not NAME_RE.match(name):
                continue
            if "bidiGenerateContent" in methods:
                groups["voice"].append(name)
            if "generateContent" in methods:
                groups["brain"].append(name)
            if "embedContent" in methods:
                groups["embeddings"].append(name)
        groups = {k: sorted(set(v)) for k, v in groups.items()}
        return {**groups, "lucid": list(groups["brain"]), "voices": list(VOICES), "current": self.current()}

    # --- writing
    def set_voice_model(self, name: str) -> None:
        vs = _read_json(self.voice_path)
        vs["model"] = f"models/{name}"
        _write_json(self.voice_path, vs)

    def set_lucid(self, name: str) -> None:
        ms = _read_json(self.models_path)
        ms["lucid"] = name
        _write_json(self.models_path, ms)

    def set_voice_settings(self, voice, rate, brief) -> dict:
        vs = _read_json(self.voice_path)
        vs.update({"voice": voice, "speaking_rate": rate, "brief_mode": brief})
        _write_json(self.voice_path, vs)
        return {"voice": voice, "speaking_rate": rate, "brief_mode": brief}

    def idle(self) -> bool:
        from .collectors import conversation_open
        try:
            text = self.log_tail()
            if text is None:
                return False
            return not conversation_open(text)
        except (OSError, subprocess.SubprocessError):
            return False  # unknown = busy: fail closed

    def _apply_brain(self, name: str) -> None:
        cfg = _read_json(self.openclaw_json)
        if not cfg:
            raise OSError("openclaw.json unreadable")
        shutil.copy2(self.openclaw_json, f"{self.openclaw_json}.bak.{time.strftime('%Y%m%d_%H%M%S')}")
        cfg.setdefault("agents", {}).setdefault("defaults", {}).setdefault("model", {})["primary"] = f"google/{name}"
        _write_json(self.openclaw_json, cfg)
        ms = _read_json(self.models_path)
        ms.pop("pending_brain", None)
        _write_json(self.models_path, ms)

    def set_brain(self, name: str) -> str:
        if not self.idle():
            ms = _read_json(self.models_path)
            ms["pending_brain"] = name
            _write_json(self.models_path, ms)
            return "pending"
        self._apply_brain(name)
        self.restart()
        return "applied"

    def apply_pending(self) -> bool:
        """Called periodically: applies a deferred brain change once no conversation is open."""
        name = _read_json(self.models_path).get("pending_brain")
        if not name or not self.idle():
            return False
        self._apply_brain(name)
        self.restart()
        return True


def make_handlers(store: ModelStore):
    async def list_models(cmd: dict) -> dict:
        try:
            return {"data": await asyncio.to_thread(store.list_models)}
        except Exception:  # noqa: BLE001 - network, key file, bad JSON: never leak details
            return {"ok": False, "error": "models_unavailable"}

    async def set_models(cmd: dict) -> dict:
        args = cmd.get("args") or {}
        roles = {"voice", "brain", "lucid", "embeddings"}
        if not args or not set(args) <= roles:
            return {"ok": False, "error": "invalid_value"}
        if not all(isinstance(v, str) and NAME_RE.match(v) for v in args.values()):
            return {"ok": False, "error": "invalid_value"}
        # Write-time validation (a bad voice model would only fail when the next conversation connects):
        # the model must be in the Google listing for its role.
        try:
            listing = await asyncio.to_thread(store.list_models)
        except Exception:  # noqa: BLE001
            return {"ok": False, "error": "models_unavailable"}
        for role, group in (("voice", "voice"), ("brain", "brain"), ("lucid", "lucid"), ("embeddings", "embeddings")):
            if role in args and args[role] not in listing[group]:
                return {"ok": False, "error": "invalid_value"}
        result: dict = {}
        if "embeddings" in args:
            if store.reindexer is None:
                return {"ok": False, "error": "embeddings_unavailable"}
            if args["embeddings"] != store.current()["embeddings"] and store.reindexer.running:
                return {"ok": False, "error": "reindex_running"}  # before anything else is applied
        try:
            if "voice" in args:
                await asyncio.to_thread(store.set_voice_model, args["voice"])
                result["voice"] = "applied"
            if "lucid" in args:
                await asyncio.to_thread(store.set_lucid, args["lucid"])
                result["lucid"] = "applied"
            if "brain" in args:
                result["brain"] = await asyncio.to_thread(store.set_brain, args["brain"])
        except (OSError, subprocess.SubprocessError):
            return {"ok": False, "error": "write_failed"}
        if "embeddings" in args:  # background: the reply does not wait for the reindex
            if args["embeddings"] == store.current()["embeddings"]:
                result["embeddings"] = "unchanged"
            elif store.reindexer.start(args["embeddings"]):
                result["embeddings"] = "started"
            else:
                return {"ok": False, "error": "reindex_running", "data": result}
        return {"data": result}

    async def estimate_reindex(cmd: dict) -> dict:
        model = (cmd.get("args") or {}).get("model")
        if not isinstance(model, str) or not NAME_RE.match(model):
            return {"ok": False, "error": "invalid_value"}
        if store.reindexer is None:
            return {"ok": False, "error": "embeddings_unavailable"}
        try:
            return {"data": await asyncio.to_thread(store.reindexer.estimate, model)}
        except Exception:  # noqa: BLE001
            return {"ok": False, "error": "estimate_failed"}

    async def set_voice_settings(cmd: dict) -> dict:
        a = cmd.get("args") or {}
        known = a.get("voice") in VOICES or a.get("voice") == store.current()["voice_name"]  # env voice stays valid
        if (not known or a.get("speaking_rate") not in RATES
                or not isinstance(a.get("brief_mode"), bool)):
            return {"ok": False, "error": "invalid_value"}
        try:
            data = await asyncio.to_thread(store.set_voice_settings, a["voice"], a["speaking_rate"], a["brief_mode"])
        except OSError:
            return {"ok": False, "error": "write_failed"}
        return {"data": data}

    return {"list_models": list_models, "set_models": set_models,
            "estimate_reindex": estimate_reindex, "set_voice_settings": set_voice_settings}
