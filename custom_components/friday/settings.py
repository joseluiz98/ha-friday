"""Settings that live in forms and in `friday.set_setting`, not on the device page.

Pure Python: the specification of the general settings (one value for every satellite), the advanced
settings (per satellite, ranges read from the hidden descriptors the agent announces) and their validation.
"""

from __future__ import annotations

from typing import Any

from .protocol import Descriptor

GENERAL_KEYS: tuple[str, ...] = (
    "end_of_speech_pause",
    "reply_wait",
    "follow_up_window",
    "no_follow_up_while_music",
    "no_follow_up_after_home_commands",
)

GENERAL_SPECS: dict[str, dict[str, Any]] = {
    "end_of_speech_pause": {"type": "number", "min": 0.5, "max": 2.0, "step": 0.1},
    "reply_wait": {"type": "number", "min": 2, "max": 30, "step": 1},
    "follow_up_window": {"type": "number", "min": 1, "max": 30, "step": 1},
    "no_follow_up_while_music": {"type": "bool"},
    "no_follow_up_after_home_commands": {"type": "bool"},
}

ADVANCED_FIXED: tuple[str, ...] = ("ducking", "ducking_level", "ducking_restore", "continuous_speech_limit")
_WAKEWORD_KEYS = ("_mode", "_sensitivity")
_EPS = 1e-6


class SettingError(ValueError):
    """A setting value or key was refused; `code` is one of the error codes of the forms."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


def _is_wakeword_key(key: str) -> bool:
    return key.startswith("wakeword_") and key.endswith(_WAKEWORD_KEYS)


def advanced_keys(capabilities: list[Descriptor]) -> list[str]:
    """Advanced settings this satellite announces, in descriptor order."""
    return [d.key for d in capabilities if d.key in ADVANCED_FIXED or _is_wakeword_key(d.key)]


def _number(value: Any, lo: float, hi: float, step: float | None) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SettingError("bad_type")
    if value < lo - _EPS or value > hi + _EPS:
        raise SettingError("out_of_range")
    if step:
        steps = (value - lo) / step
        if abs(steps - round(steps)) > _EPS * 10:
            raise SettingError("out_of_range")
        value = lo + round(steps) * step
    if float(value).is_integer() and float(step or 0).is_integer() and float(lo).is_integer():
        return int(value)
    return round(float(value), 6)


def validate_general(key: str, value: Any) -> bool | float | int:
    spec = GENERAL_SPECS.get(key)
    if spec is None:
        raise SettingError("unknown_key")
    if spec["type"] == "bool":
        if not isinstance(value, bool):
            raise SettingError("bad_type")
        return value
    return _number(value, spec["min"], spec["max"], spec["step"])


def validate_advanced(capabilities: list[Descriptor], key: str, value: Any) -> bool | float | int | str:
    by_key = {d.key: d for d in capabilities}
    desc = by_key.get(key)
    if key not in advanced_keys(capabilities) or desc is None:
        raise SettingError("unknown_key")
    if desc.platform == "switch":
        if not isinstance(value, bool):
            raise SettingError("bad_type")
        return value
    if desc.platform == "select":
        if not isinstance(value, str):
            raise SettingError("bad_type")
        if value not in (desc.options or []):
            raise SettingError("out_of_range")
        return value
    lo = desc.min if desc.min is not None else float("-inf")
    hi = desc.max if desc.max is not None else float("inf")
    if desc.step is None or lo == float("-inf"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingError("bad_type")
        if value < lo or value > hi:
            raise SettingError("out_of_range")
        return value
    return _number(value, lo, hi, desc.step)


def check_wakewords(modes: dict[str, str]) -> None:
    """`modes` = {word: mode} as it will end up; at least one must be active."""
    if not any(mode == "active" for mode in modes.values()):
        raise SettingError("one_active_word")


def general_plan(general: dict[str, Any], sat_state: dict[str, Any], sat_keys: set[str]) -> dict[str, Any]:
    """What to send to one satellite so it matches the general settings."""
    plan: dict[str, Any] = {}
    for key in GENERAL_KEYS:
        if key not in general or key not in sat_keys:
            continue
        if key == "follow_up_window" and sat_state.get("follow_up_window") == 0:
            continue  # Follow-up mode is off on this satellite: keep it off
        if sat_state.get(key) != general[key]:
            plan[key] = general[key]
    return plan
