"""Update channels. A GitHub pre-release goes to alpha first and is promoted to stable only after
SOAK_S healthy hours there without failure; a normal release is stable as soon as CI is green."""
import re
from dataclasses import dataclass

from .constants import SOAK_S

CHANNELS = ("stable", "alpha")
_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


@dataclass(frozen=True)
class Release:
    tag: str
    sha: str
    published_at: float
    ci: str  # "green" | "red" | "pending"
    prerelease: bool = False


@dataclass
class SoakRecord:
    started_at: float | None  # when the first alpha device became healthy on this tag
    failed: bool  # any alpha device (or the server) reported a failure on this tag
    blocked: bool = False  # the owner stopped this tag's promotion by hand (never cleared by a failure, nor the reverse)


def version_key(tag: str) -> tuple[int, int, int]:
    m = _TAG.match(tag)
    if not m:
        raise ValueError(f"bad tag: {tag!r}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def eligible(releases: list[Release]) -> list[Release]:
    good = []
    for r in releases:
        if r.ci != "green":
            continue
        try:
            version_key(r.tag)
        except ValueError:
            continue
        good.append(r)
    return sorted(good, key=lambda r: version_key(r.tag))


def promoted(r: Release, soak: dict[str, SoakRecord], now: float) -> bool:
    if not r.prerelease:
        return True
    rec = soak.get(r.tag)
    return (rec is not None and rec.started_at is not None and not rec.failed
            and not rec.blocked and now - rec.started_at >= SOAK_S)


def stable_tag(releases: list[Release], soak: dict[str, SoakRecord], now: float) -> str | None:
    ok = [r for r in eligible(releases) if promoted(r, soak, now)]
    return ok[-1].tag if ok else None


def target_for(channel: str, releases: list[Release], soak: dict[str, SoakRecord], now: float) -> str | None:
    if channel == "alpha":
        ok = [r for r in eligible(releases) if not (soak.get(r.tag) and soak[r.tag].failed)]
        return ok[-1].tag if ok else None
    return stable_tag(releases, soak, now)


def server_channel(satellite_channels: list[str]) -> str:
    return "alpha" if "alpha" in satellite_channels else "stable"
