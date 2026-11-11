"""Release manifest (ota/release.json): components, protocols and which paths belong to which component."""
import json
from dataclasses import dataclass

# Fixed install order; server-agent (the orchestrator) is last among the server-side components.
COMPONENT_ORDER = ("brain-skills", "server", "wwt", "voice-gateway", "container", "integration", "server-agent",
                   "satellite")


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class Manifest:
    schema: int
    protocol: dict[str, int]
    min_protocol: dict[str, int]
    requires: dict[str, dict[str, int]]
    heavy: frozenset[str]
    components: dict[str, tuple[str, ...]]


def _int_map(data: dict, key: str) -> dict[str, int]:
    raw = data.get(key, {})
    if not isinstance(raw, dict):
        raise ManifestError(f"{key}: must be an object")
    for name, value in raw.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ManifestError(f"{key}.{name}: must be an integer >= 1")
    return dict(raw)


def _components(data: dict) -> dict[str, tuple[str, ...]]:
    raw = data.get("components")
    if not isinstance(raw, dict) or not raw:
        raise ManifestError("components: must be a non-empty object")
    out = {}
    for name, paths in raw.items():
        if not isinstance(paths, list) or not paths:
            raise ManifestError(f"{name}: must be a non-empty list of paths")
        for p in paths:
            if not isinstance(p, str) or not p or p.startswith("/") or ".." in p.split("/"):
                raise ManifestError(f"{name}: bad path {p!r} (relative, no '..')")
        out[name] = tuple(paths)
    return out


def parse(data: dict) -> Manifest:
    if data.get("schema") != 1:
        raise ManifestError("schema: only version 1 is supported")
    protocol = _int_map(data, "protocol")
    min_protocol = _int_map(data, "min_protocol")
    components = _components(data)
    known = set(components) | set(protocol)
    heavy = data.get("heavy", [])
    if not isinstance(heavy, list):
        raise ManifestError("heavy: must be a list")
    for name in heavy:
        if name not in known:
            raise ManifestError(f"heavy: unknown name {name!r}")
    requires = data.get("requires", {})
    if not isinstance(requires, dict):
        raise ManifestError("requires: must be an object")
    for name, reqs in requires.items():
        if name not in known:
            raise ManifestError(f"requires: unknown name {name!r}")
        if not isinstance(reqs, dict):
            raise ManifestError(f"requires.{name}: must be an object")
        for dep, ver in reqs.items():
            if dep not in known:
                raise ManifestError(f"requires.{name}: unknown name {dep!r}")
            if isinstance(ver, bool) or not isinstance(ver, int) or ver < 1:
                raise ManifestError(f"requires.{name}.{dep}: must be an integer >= 1")
    return Manifest(
        schema=1,
        protocol=protocol,
        min_protocol=min_protocol,
        requires={n: dict(r) for n, r in requires.items()},
        heavy=frozenset(heavy),
        components=components,
    )


def load(path: str) -> Manifest:
    with open(path, encoding="utf-8") as f:
        return parse(json.load(f))


def changed_components(m: Manifest, changed_paths: list[str]) -> list[str]:
    def hit(prefix: str) -> bool:
        return any(p == prefix or p.startswith(prefix + "/") for p in changed_paths)
    return [name for name, prefixes in m.components.items() if any(hit(pre) for pre in prefixes)]
