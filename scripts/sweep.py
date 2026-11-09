#!/usr/bin/env python3
"""Sweep: fail (exit 1) when the tree, or any commit of the history, carries a secret or house data.

  python3 scripts/sweep.py [--history] [PATH...]      (default PATH: the repo root)

Private list: FRIDAY_SENSITIVE_LIST (default /config/.friday/sensitive.txt), used when present; without it (CI) only
the generic patterns run. Exceptions: `.sweep-allow` at the repo root, one `<path-glob>:<regex>` per line.
Output names file, line and category, with the value masked.
"""
from __future__ import annotations

import fnmatch
import importlib.util
import os
import re
import subprocess
import sys
from typing import NamedTuple

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
_spec = importlib.util.spec_from_file_location("inventory_scan", os.path.join(HERE, "inventory_scan.py"))
scan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scan)

DEFAULT_LIST = "/config/.friday/sensitive.txt"
GENERIC = {
    "secret": scan.SECRET,
    "ip_mac": [re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b"), re.compile(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b")],
    "email_phone": [re.compile(r"\b[\w.+-]+@[A-Za-z][\w-]*(?:\.[A-Za-z][\w-]*)*\.[A-Za-z]{2,}\b"),
                    re.compile(r"\+55\s?\(?\d{2}\)?\s?9?\d{4}-?\d{4}\b")],
    "personal_path": [re.compile(r"/home/(?!jose\b)[a-z][a-z0-9_-]*")],
    "url_webhook": scan.URL_WEBHOOK,
}


class AllowRule(NamedTuple):
    glob: str
    regex: re.Pattern


def parse_allow(lines) -> list[AllowRule]:
    rules = []
    for raw in lines:
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        glob, _, rx = s.partition(":")
        rules.append(AllowRule(glob, re.compile(rx)))
    return rules


def _allowed(path: str, text: str, allow) -> bool:
    return any(fnmatch.fnmatch(path, r.glob) and r.regex.search(text) for r in allow)


def _line_hits(text: str, private) -> list[tuple[str, str]]:
    hits = []
    low = text.lower()
    for cat, val in private:
        if val and val.lower() in low:
            hits.append((cat, val))
    for cat, pats in GENERIC.items():
        for pat in pats:
            hits.extend((cat, m.group(0)) for m in pat.finditer(text))
    return hits


def _scan_text(path: str, content: str, private, allow) -> list:
    out = []
    name = os.path.basename(path)
    for cat, val in private:
        if val and val.lower() in name.lower() and not _allowed(path, name, allow):
            out.append(scan.Finding(cat, path, None, scan.mask(val)))
    for n, line in enumerate(content.splitlines(), 1):
        if _allowed(path, line, allow):
            continue
        out.extend(scan.Finding(cat, path, n, scan.mask(v)) for cat, v in _line_hits(line, private))
    return out


def _private(private) -> list[tuple[str, str]]:
    return scan._known_pairs(private)


def sweep(paths: list[str], private: list, allow: list[AllowRule]) -> list:
    pv = _private(private)
    found = []
    for path in scan._files(paths):
        if not scan._is_text(path):
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            found += _scan_text(path, f.read(), pv, allow)
    return found


def sweep_history(repo: str, private: list, allow: list[AllowRule]) -> list:
    pv = _private(private)
    objs = subprocess.run(["git", "-C", repo, "rev-list", "--all", "--objects"], check=True,
                          capture_output=True, text=True).stdout.splitlines()
    seen, found = set(), []
    for line in objs:
        sha, _, path = line.partition(" ")
        if not path or sha in seen:
            continue
        seen.add(sha)
        kind = subprocess.run(["git", "-C", repo, "cat-file", "-t", sha], capture_output=True, text=True).stdout.strip()
        if kind != "blob":
            continue
        data = subprocess.run(["git", "-C", repo, "cat-file", "-p", sha], capture_output=True).stdout
        if b"\x00" in data[:8192]:
            continue
        found += [f._replace(path=f"{path}@{sha[:8]}") for f in
                  _scan_text(os.path.join(repo, path), data.decode("utf-8", "replace"), pv, allow)]
    return found


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    history = "--history" in args
    args = [a for a in args if a != "--history"] or [REPO]
    lst = os.environ.get("FRIDAY_SENSITIVE_LIST", DEFAULT_LIST)
    private = scan.load_known(lst) if os.path.exists(lst) else []
    allow_file = os.path.join(REPO, ".sweep-allow")
    allow = parse_allow(open(allow_file, encoding="utf-8")) if os.path.exists(allow_file) else []
    found = sweep_history(args[0], private, allow) if history else sweep(args, private, allow)
    mode = "private list + generic" if private else "generic only"
    if found:
        print(f"❌ {len(found)} finding(s) ({mode}):")
        for f in found:
            print(f"{os.path.relpath(f.path, REPO) if not history else f.path}:{f.line or 'name'}: {f.category} {f.excerpt}")
        return 1
    print(f"✅ Sweep clean ({mode}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
