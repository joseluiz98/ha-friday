"""GitHub release + CI status watcher (public repo, no token: 60 req/h).

One call lists the releases; only a release whose CI is not yet final (or that is new) costs more calls
(commit sha + check-runs). A release with a final CI (green/red) is kept as-is, never asked again.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Callable

from friday_agent_core.ota.channels import Release

DEFAULT_REPO = "joseluiz98/ha-friday"
API = "https://api.github.com"
TIMEOUT_S = 15
_OK_CONCLUSIONS = ("success", "neutral", "skipped")
_FINAL = ("green", "red")


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
    if any(r.get("conclusion") not in _OK_CONCLUSIONS for r in done):
        return "red"
    return "green" if len(done) == len(check_runs) else "pending"


class GitHubWatcher:
    def __init__(self, repo: str, fetch: Callable[[str], dict | list] = _fetch_json):
        self.repo = repo
        self._fetch = fetch

    def _get(self, path: str, label: str):
        try:
            return self._fetch(f"{API}/repos/{self.repo}/{path}")
        except Exception as e:  # network, HTTP, JSON: the caller keeps its previous `known`
            raise GitHubError(f"github {label}: {type(e).__name__}") from None

    def poll(self, known: dict[str, ReleaseInfo]) -> dict[str, ReleaseInfo]:
        listing = self._get("releases?per_page=30", "releases")
        out: dict[str, ReleaseInfo] = {}
        try:
            for r in listing:
                if r.get("draft"):
                    continue
                tag = r["tag_name"]
                prev = known.get(tag)
                if prev is not None and prev.release.ci in _FINAL:
                    out[tag] = prev
                    continue
                sha = self._get(f"commits/{tag}", "commit")["sha"]
                runs = self._get(f"commits/{tag}/check-runs?per_page=100", "check-runs")["check_runs"]
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
