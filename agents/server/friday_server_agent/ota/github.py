"""GitHub release + CI status watcher (public repo, no token: 60 req/h).

One call lists the releases; only a release whose CI is not yet final (or that is new) costs more calls
(commit sha + check-runs). Trade-off: a release with a final CI (green/red) is NOT re-queried in normal polls,
because 31 calls per cycle would blow the 60 req/h unauthenticated quota. A red CI is rechecked for
`RED_RECHECK_S` after the release (a rerun turns it green); a moved tag or an older red goes unnoticed until
`poll(known, full=True)`, which re-checks every release (used by the "check now" button).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable

from friday_agent_core.ota.channels import Release

DEFAULT_REPO = "joseluiz98/ha-friday"
API = "https://api.github.com"
TIMEOUT_S = 15
_OK_CONCLUSIONS = ("success", "neutral", "skipped")
# Transient outcomes (a cancelled run is usually re-run): pending, never a permanent red.
_PENDING_CONCLUSIONS = (None, "cancelled", "timed_out", "stale", "action_required")
_FINAL = ("green", "red")
# FINDING 8 (16/11): a red CI of a recent release is rechecked on every poll, since a rerun of a flaky job turns it
# green; past that window red is final again, so old red releases never eat the 60 req/h quota
RED_RECHECK_S = 2 * 86400


class GitHubError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReleaseInfo:
    release: Release
    notes: str
    url: str


def repo_from_env() -> str:
    return os.environ.get("FRIDAY_OTA_REPO", DEFAULT_REPO)


def _fetch_json(url: str) -> dict | list:
    req = urllib.request.Request(url, headers={
        "User-Agent": "friday-server-agent",
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return json.load(resp)


def _epoch(iso: str) -> float:
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _ci(check_runs: list[dict]) -> str:
    if not check_runs:
        return "pending"
    done = [r for r in check_runs if r.get("status") == "completed"]
    concl = [r.get("conclusion") for r in done]
    if any(c not in _OK_CONCLUSIONS and c not in _PENDING_CONCLUSIONS for c in concl):
        return "red"
    if len(done) == len(check_runs) and all(c in _OK_CONCLUSIONS for c in concl):
        return "green"
    return "pending"


class GitHubWatcher:
    def __init__(self, repo: str, fetch: Callable[[str], dict | list] = _fetch_json, clock: Callable[[], float] = time.time):
        self.repo = repo
        self._fetch = fetch
        self._clock = clock

    def _get(self, path: str, label: str):
        try:
            return self._fetch(f"{API}/repos/{self.repo}/{path}")
        except Exception as e:  # network, HTTP, JSON: the caller keeps its previous `known`
            raise GitHubError(f"github {label}: {type(e).__name__}") from None

    def poll(self, known: dict[str, ReleaseInfo], full: bool = False) -> dict[str, ReleaseInfo]:
        listing = self._get("releases?per_page=30", "releases")
        out: dict[str, ReleaseInfo] = {}
        try:
            for r in listing:
                if r.get("draft"):
                    continue
                tag = r["tag_name"]
                prev = known.get(tag)
                recheck = prev is not None and prev.release.ci == "red" and \
                    self._clock() - prev.release.published_at < RED_RECHECK_S
                if not full and prev is not None and prev.release.ci in _FINAL and not recheck:
                    out[tag] = prev
                    continue
                q = urllib.parse.quote(tag, safe="")
                sha = self._get(f"commits/{q}", "commit")["sha"]
                runs = self._get(f"commits/{q}/check-runs?per_page=100", "check-runs")["check_runs"]
                out[tag] = ReleaseInfo(
                    release=Release(tag, sha, _epoch(r["published_at"]), _ci(runs),
                                    prerelease=bool(r.get("prerelease"))),
                    notes=r.get("body") or "",
                    url=r.get("html_url") or "",
                )
        except GitHubError:
            raise
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            raise GitHubError(f"github: unexpected payload ({type(e).__name__})") from None
        return out
