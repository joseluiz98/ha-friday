"""Voices of the household, kept on the VM (spec 2026-11-05 §6.5): the satellites push their `~/vozes/` every night
(rrsync, one folder per satellite under incoming/); this gathers them by person and microphone so a new satellite
can receive them on adoption. Nothing is ever deleted here."""

from __future__ import annotations

import os
import re
import shutil

DEFAULT_MIC = "jabra-speak-410"  # satellites that predate the `mic` file all had the Jabra
VERIFIER = "friday_verificador.pkl"
_MIC = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_PERSON = re.compile(r"^[a-z][a-z_]{0,39}$")  # ids made by cadastro.nome_valido


def _mic_of(sat_dir: str) -> str:
    try:
        with open(os.path.join(sat_dir, "mic"), encoding="utf-8") as f:
            mic = f.read().strip()
    except OSError:
        return DEFAULT_MIC
    return mic if _MIC.match(mic) else DEFAULT_MIC


def _copy_new(src: str, dst: str) -> bool:
    """Copy unless an identical file is already there (same size and not older). Returns True if copied."""
    try:
        s = os.stat(src)
        d = os.stat(dst)
        if d.st_size == s.st_size and d.st_mtime >= s.st_mtime:
            return False
    except FileNotFoundError:
        pass
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".tmp"
    shutil.copyfile(src, tmp)
    shutil.copystat(src, tmp)
    os.replace(tmp, dst)
    return True


def consolidate(data_dir: str) -> dict:
    """incoming/<sat>/vozes/<person>/**/*.wav -> voices/<person>/<mic>/**/*.wav (names kept, no duplicates);
    the newest incoming/<sat>/friday_verificador.pkl -> voices/friday_verificador.pkl. Returns counts."""
    incoming, out = os.path.join(data_dir, "incoming"), os.path.join(data_dir, "voices")
    copied = 0
    newest, newest_t = None, -1.0
    if os.path.isdir(incoming):
        for sat in sorted(os.listdir(incoming)):
            sat_dir = os.path.join(incoming, sat)
            if not os.path.isdir(sat_dir):
                continue
            mic = _mic_of(sat_dir)
            root = os.path.join(sat_dir, "vozes")
            for person in sorted(os.listdir(root)) if os.path.isdir(root) else []:
                if not _PERSON.match(person) or not os.path.isdir(os.path.join(root, person)):
                    continue
                base = os.path.join(root, person)
                for folder, _dirs, files in os.walk(base):
                    for name in sorted(files):
                        if not name.endswith(".wav"):
                            continue
                        rel = os.path.relpath(os.path.join(folder, name), base)
                        copied += _copy_new(os.path.join(folder, name), os.path.join(out, person, mic, rel))
            ver = os.path.join(sat_dir, VERIFIER)
            if os.path.isfile(ver) and os.stat(ver).st_mtime > newest_t:
                newest, newest_t = ver, os.stat(ver).st_mtime
    if newest:
        os.makedirs(out, exist_ok=True)
        _copy_new(newest, os.path.join(out, VERIFIER))
    return {"copied": copied, "people": people(data_dir)}


def people(data_dir: str) -> list[str]:
    """Names of the people with a voice on the VM (the folder names of voices/)."""
    out = os.path.join(data_dir, "voices")
    try:
        return sorted(n for n in os.listdir(out) if os.path.isdir(os.path.join(out, n)))
    except OSError:
        return []
