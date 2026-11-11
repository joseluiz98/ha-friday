"""Retry schedule per (device, version) and the Telegram push window."""
import datetime as dt
from dataclasses import asdict, dataclass

from .constants import PUSH_WINDOW, RETRY_DELAYS_S

_MAX_TEXT = 200


@dataclass
class Attempts:
    version: str
    failures: int = 0
    next_at: float = 0.0
    exhausted: bool = False
    last_stage: str = ""
    last_error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Attempts":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


def record_failure(a: Attempts, stage: str, error: str, now: float) -> Attempts:
    a.failures += 1
    if a.failures <= len(RETRY_DELAYS_S):
        a.next_at = now + RETRY_DELAYS_S[a.failures - 1]
    else:
        a.exhausted = True
        a.next_at = 0
    a.last_stage = stage[:_MAX_TEXT]
    a.last_error = error[:_MAX_TEXT]
    return a


def may_try(a: Attempts | None, version: str, now: float) -> bool:
    if a is None or a.version != version:
        return True
    if a.exhausted:
        return False
    return now >= a.next_at


def reset(version: str) -> Attempts:
    return Attempts(version=version)


def _hm(s: str) -> dt.time:
    h, m = s.split(":")
    return dt.time(int(h), int(m))


def push_at(now: float, tz=None) -> float:
    start, end = _hm(PUSH_WINDOW[0]), _hm(PUSH_WINDOW[1])
    local = dt.datetime.fromtimestamp(now, tz)
    if start <= local.time() < end:
        return now
    day = local.date()
    if local.time() >= end:
        day += dt.timedelta(days=1)
    return dt.datetime.combine(day, start, tzinfo=local.tzinfo).timestamp()
