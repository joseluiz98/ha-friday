"""House values that never enter the repo: owner name, Home Assistant URL, notify service, person entity, VM address.

They live in a KEY=VALUE file on each host (default /srv/friday/state/local.env, mode 600; override with
FRIDAY_LOCAL_CONFIG). Environment variables of the same name win over the file. A missing key raises
MissingLocalConfig naming it: no built-in house value to fall back to. Template: local.example.env."""
import os

DEFAULT_PATH = "/srv/friday/state/local.env"


class MissingLocalConfig(KeyError):
    pass


def path() -> str:
    return os.environ.get("FRIDAY_LOCAL_CONFIG", DEFAULT_PATH)


def load(file: str | None = None) -> dict:
    out = {}
    try:
        with open(file or path(), encoding="utf-8") as f:
            for raw in f:
                s = raw.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, _, v = s.partition("=")
                out[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return out


def get(key: str, file: str | None = None) -> str:
    val = os.environ.get(key) or load(file).get(key)
    if not val:
        raise MissingLocalConfig(f"{key} missing from {file or path()} (see local.example.env)")
    return val


def owner() -> str:
    return get("FRIDAY_OWNER_NAME")
