#!/usr/bin/env python3
"""Entry scan: list every piece of house data in code that is about to enter the public repo.

Usage: python3 scripts/inventory_scan.py --known FILE PATH... > report.md

FILE is the private list of sensitive values (one per line, optional "category|value", "#" comments).
The report masks every value (first 2 and last 2 characters), so it is safe to read on screen.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from typing import NamedTuple

CATEGORIES = ("secret", "person_name", "ip_mac", "device_id", "email_phone", "personal_path",
              "house_fact_text", "url_webhook")

SECRET = [re.compile(p) for p in (
    r"Bearer [A-Za-z0-9._-]{20,}", r"amzn1\.ask\.account\.[A-Z0-9]{40,}", r"eyJ[A-Za-z0-9._-]{20,}",
    r"\"token\"\s*:\s*\"[^\"]{16,}\"", r"PVEAPIToken=\S+", r"\bbot\d{6,}:[A-Za-z0-9_-]{20,}",
    r"\bAIza[0-9A-Za-z_-]{35}\b", r"-----BEGIN [A-Z ]*PRIVATE KEY-----", r"\bsk-[A-Za-z0-9_-]{32,}",
    r"\bghp_[A-Za-z0-9]{36}\b", r"\bgithub_pat_[A-Za-z0-9_]{40,}", r"\bsk_[0-9a-f]{48}\b")]
IP_MAC = [re.compile(p) for p in (
    r"\b(?:10|127)\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", r"\b192\.168\.\d{1,3}\.\d{1,3}\b",
    r"\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b", r"\b(?:[0-9A-Fa-f]{2}\\*:){5}[0-9A-Fa-f]{2}\b")]
DEVICE = [re.compile(r"\bsat-[0-9a-f]{6}\b")]
EMAIL_PHONE = [re.compile(p) for p in (r"\b[\w.+-]+@[\w-]+\.[\w.]+\b", r"\+?55\s?\(?\d{2}\)?\s?9?\d{4}-?\d{4}\b")]
PERSONAL_PATH = [re.compile(r"/home/[a-z][a-z0-9_-]*")]
URL_WEBHOOK = [re.compile(r"/api/webhook/[A-Za-z0-9_-]{8,}"), re.compile(r"https?://[\w.-]*silvasoftware[\w./-]*")]
STRING = re.compile(r"\"([^\"\\\n]|\\.){41,}\"|'([^'\\\n]|\\.){41,}'")

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "venv", ".venv", "node_modules", ".superpowers"}


class Finding(NamedTuple):
    category: str
    path: str
    line: int | None
    excerpt: str


def mask(value: str) -> str:
    return value[:2] + "…" + value[-2:] if len(value) > 4 else "…"


def load_known(path: str) -> list[tuple[str, str]]:
    out = []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            cat, _, val = s.partition("|") if "|" in s else ("person_name", "", s)
            out.append((cat if cat in CATEGORIES else "person_name", val.strip()))
    return out


def _known_pairs(known) -> list[tuple[str, str]]:
    return [k if isinstance(k, tuple) else ("person_name", k) for k in known]


def _skip_file(name: str) -> bool:
    return ".bak" in name or name.endswith((".pyc", ".pyo"))


def _is_text(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return b"\x00" not in f.read(8192)
    except OSError:
        return False


def _files(paths):
    for p in paths:
        if os.path.isfile(p):
            yield p
            continue
        for dp, dn, fn in os.walk(p):
            dn[:] = sorted(d for d in dn if d not in SKIP_DIRS)
            for name in sorted(fn):
                if not _skip_file(name):
                    yield os.path.join(dp, name)


def _scan_line(text: str, known) -> list[tuple[str, str]]:
    hits = []
    low = text.lower()
    for cat, val in known:
        if val and val.lower() in low:
            in_long = any(val.lower() in m.group(0).lower() for m in STRING.finditer(text))
            hits.append(("house_fact_text" if in_long else cat, val))
    for cat, pats in (("secret", SECRET), ("ip_mac", IP_MAC), ("device_id", DEVICE), ("email_phone", EMAIL_PHONE),
                      ("personal_path", PERSONAL_PATH), ("url_webhook", URL_WEBHOOK)):
        for pat in pats:
            hits.extend((cat, m.group(0)) for m in pat.finditer(text))
    return hits


def scan(paths: list[str], known: list) -> list[Finding]:
    pairs = _known_pairs(known)
    findings: list[Finding] = []
    for root in paths:
        for path in _files([root]):
            findings += _scan_file(path, os.path.relpath(path, root) if os.path.isdir(root) else os.path.basename(path), pairs)
    return findings


def _scan_file(path: str, rel: str, pairs) -> list:
    findings = [Finding(cat, path, None, mask(val)) for cat, val in pairs
                if val and any(val.lower() in part.lower() for part in rel.split(os.sep))]
    if not _is_text(path):
        return findings
    with open(path, encoding="utf-8", errors="replace") as f:
        for n, text in enumerate(f, 1):
            for cat, val in _scan_line(text, pairs):
                findings.append(Finding(cat, path, n, mask(val)))
    return findings


def report(findings: list[Finding]) -> str:
    lines = ["# Entry scan", "", f"{len(findings)} finding(s).", ""]
    for cat in CATEGORIES:
        sel = [f for f in findings if f.category == cat]
        if not sel:
            continue
        lines += [f"## {cat} ({len(sel)})", "", "| file | line | value |", "|---|---|---|"]
        lines += [f"| {f.path} | {f.line if f.line is not None else 'file name'} | {f.excerpt} |" for f in sel]
        lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--known")
    ap.add_argument("paths", nargs="+")
    a = ap.parse_args(argv)
    known = load_known(a.known) if a.known else []
    sys.stdout.write(report(scan(a.paths, known)) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
