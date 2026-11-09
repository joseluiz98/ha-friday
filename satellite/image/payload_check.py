#!/usr/bin/env python3
"""Nothing from the house goes into the image. Scans a payload directory and reports WHAT is wrong, never the value.

  payload_check.py PAYLOAD_DIR [--secrets-json /share/friday/satellites.json]

Fails (exit 1) on: a file/dir that is house data (a `vozes`/`gravacoes` folder, a *.pkl verifier, an upload key,
the registry); any `senha`/`token` value of the
given registry found inside any file; the id of the production satellite in a text file."""
import argparse
import json
import os
import sys

FORBIDDEN_EXACT = ("vozes", "gravacoes", "satellites.json", "rotulos.json", "webhook_id")  # data of the house
FORBIDDEN_START = ("id_satsync",)
SENSITIVE_LIST = os.environ.get("FRIDAY_SENSITIVE_LIST", "/config/.friday/sensitive.txt")


def production_ids(path: str = SENSITIVE_LIST) -> tuple:
    """Satellite ids of the house, from the private sensitive list (never written in the repo)."""
    try:
        with open(path, encoding="utf-8") as f:
            return tuple(line.split("|", 1)[1].strip() for line in f
                         if line.startswith("device_id|sat-"))
    except OSError:
        return ()


PRODUCTION_IDS = production_ids()


def secret_values(registry_path: str) -> list[str]:
    try:
        with open(registry_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    found = []
    for entry in data.values() if isinstance(data, dict) else []:
        if isinstance(entry, dict):
            found += [v for k, v in entry.items() if k in ("senha", "token", "password") and isinstance(v, str) and len(v) >= 8]
    return found


def scan(root: str, secrets: list[str]) -> list[str]:
    problems = []
    needles = [s.encode() for s in secrets] + [p.encode() for p in PRODUCTION_IDS]
    for folder, dirs, files in os.walk(root):
        for name in dirs + files:
            if name in FORBIDDEN_EXACT or name.endswith(".pkl") or name.startswith(FORBIDDEN_START):
                problems.append(f"forbidden name: {os.path.relpath(os.path.join(folder, name), root)}")
        for name in files:
            path = os.path.join(folder, name)
            try:
                with open(path, "rb") as f:
                    blob = f.read()
            except OSError:
                continue
            for n in needles:
                if n in blob:
                    kind = "production satellite id" if n.decode() in PRODUCTION_IDS else "a secret from the registry"
                    problems.append(f"{kind} inside {os.path.relpath(path, root)}")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("payload")
    ap.add_argument("--secrets-json", default="")
    a = ap.parse_args(argv)
    problems = scan(a.payload, secret_values(a.secrets_json) if a.secrets_json else [])
    for p in problems:
        print("PAYLOAD PROBLEM:", p, file=sys.stderr)
    print(f"payload check: {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
