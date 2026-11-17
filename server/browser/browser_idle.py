#!/usr/bin/env python3
"""Closes the brain's headless Chromium when it has been idle (Task 11 of the satellite-arbitration plan).

OpenClaw 2026.11.3 has no setting for this: `browser.tabCleanup` only accepts `enabled` (the 120 min idle limit is an
internal default that closes tabs, not the browser) and the Chromium process stays resident (~1 GB, ~12 processes)
after the first use. OpenClaw starts it again on demand, so closing it only costs a cold start.

Runs on the VM host every few minutes (friday-browser-idle.timer) as root. Idle means all of:
  * no voice brain turn running (`brain_turns == 0` in /srv/friday/state/activity.json; a file not renewed for 90 s means
    the bot is down, so no voice turn can be running and the field is ignored);
  * the Chromium processes used no meaningful CPU for IDLE_SECONDS (a page being driven by the brain, including a
    Telegram turn that the activity file does not count, burns CPU; an idle tab ticks ~1 CPU tick per 20 s);
  * the browser was not (re)started in that window.
Closing goes through `oc browser stop` (OpenClaw's own, clean). Only if processes survive it, those exact processes are
killed: selected by their executable path under /usr/lib/chromium/ inside the container, never by name pattern.
"""
import json, os, subprocess, sys, time

STATE = os.environ.get("FRIDAY_BROWSER_IDLE_STATE", "/srv/friday/state/browser-idle.json")
ACTIVITY = os.environ.get("FRIDAY_ACTIVITY", "/srv/friday/state/activity.json")
CONTAINER = os.environ.get("FRIDAY_CONTAINER", "friday")
OC = os.environ.get("FRIDAY_OC", "/usr/local/bin/oc")
IDLE_SECONDS = 600
ACTIVITY_STALE_SECONDS = 90
ACTIVE_TICKS = 100      # CPU ticks (1/100 s) of Chromium per check that count as use; idle measured: ~10 per 2 min
CHROMIUM_DIR = "/usr/lib/chromium/"

# Runs inside the container: one line per process whose executable lives in the Chromium directory.
SCAN = ('for p in /proc/[0-9]*; do e=$(readlink "$p/exe" 2>/dev/null); '
        'case "$e" in ' + CHROMIUM_DIR + '*) echo "${p#/proc/} $(cat "$p/stat")";; esac; done')


def parse_scan(text: str) -> dict:
    """{pid: (starttime, cpu_ticks)} from the SCAN output (`<pid> <contents of /proc/pid/stat>`)."""
    procs = {}
    for line in text.splitlines():
        try:
            pid, stat = line.split(" ", 1)
            rest = stat.rsplit(")", 1)[1].split()    # comm may contain spaces/parentheses; fields after it are fixed
            procs[int(pid)] = (int(rest[19]), int(rest[11]) + int(rest[12]))
        except (ValueError, IndexError):
            continue
    return procs


def cpu_delta(prev: dict, cur: dict) -> int:
    """CPU ticks used since the previous check. A pid is the same process only if its start time matches; a new
    process counts all its ticks so far (it was just launched); a vanished process counts nothing."""
    total = 0
    for pid, (start, ticks) in cur.items():
        old = prev.get(str(pid)) or prev.get(pid)
        total += max(0, ticks - old[1]) if old and old[0] == start else ticks
    return total


def brain_busy(activity: dict | None, now: float) -> bool:
    if not activity or now - float(activity.get("updated_at", 0)) > ACTIVITY_STALE_SECONDS:
        return False
    return int(activity.get("brain_turns", 0)) > 0


def decide(state: dict, procs: dict, activity: dict | None, now: float):
    """-> (new_state, close: bool). `state` is what the previous run returned ({} when there was none)."""
    if not procs:
        return {}, False
    snap = {str(p): list(v) for p, v in procs.items()}
    prev = state.get("procs") or {}
    last_active = state.get("last_active", now)
    if not prev:
        last_active = now                                    # first sight: count idleness from here
    elif cpu_delta(prev, procs) > ACTIVE_TICKS:
        last_active = now
    if brain_busy(activity, now):
        last_active = now
    close = now - last_active >= IDLE_SECONDS
    return {"procs": snap, "last_active": last_active, "at": now}, close


def sh(*cmd, timeout=60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def scan() -> dict:
    r = sh("docker", "exec", CONTAINER, "sh", "-c", SCAN)
    if r.returncode != 0:
        raise RuntimeError("scan failed: " + r.stderr.strip()[:200])
    return parse_scan(r.stdout)


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def close_browser() -> str:
    sh(OC, "browser", "stop", timeout=90)
    time.sleep(3)
    left = scan()
    if not left:
        return "stopped"
    for sig in ("TERM", "KILL"):
        for pid in sorted(left):
            sh("docker", "exec", CONTAINER, "kill", "-" + sig, str(pid))
        time.sleep(3)
        left = scan()
        if not left:
            return "killed-" + sig.lower()
    return "failed"


def main() -> int:
    now = time.time()
    procs = scan()
    state, close = decide(load(STATE) or {}, procs, load(ACTIVITY), now)
    if close and brain_busy(load(ACTIVITY), time.time()):   # a turn started while we were scanning: re-check right before acting
        close = False
        state["last_active"] = time.time()
    if close:
        result = close_browser()
        print(f"browser-idle: idle >= {IDLE_SECONDS}s with {len(procs)} Chromium processes -> {result}", flush=True)
        state = {}
        if result == "failed":
            return 1
    elif procs:
        print(f"browser-idle: {len(procs)} processes, idle {int(now - state['last_active'])}s of {IDLE_SECONDS}s", flush=True)
    save(STATE, state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
