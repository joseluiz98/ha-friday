"""The version an agent was installed from (plan 2026-11-09 Task 8).

Install scripts and the satellite update bundle write `FRIDAY_VERSION` next to this package, one line:
`<tag-or-describe> <full-sha>`. Agents publish the first field as `sw_version`; "unknown" when the file is missing."""
import os

FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "FRIDAY_VERSION")


def _fields(path=None) -> list:
    try:
        with open(path or FILE, encoding="utf-8") as f:
            return f.read().split()
    except OSError:
        return []


def installed_version(path=None) -> str:
    parts = _fields(path)
    return parts[0] if parts else "unknown"


def installed_sha(path=None):
    parts = _fields(path)
    return parts[1] if len(parts) > 1 else None
