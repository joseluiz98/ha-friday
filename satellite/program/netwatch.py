"""Satellite network recovery runner (spec 2026-11-08, replaces the notice-only netwatch of 2026-11-05).

Runs every 5 min from friday-netwatch.timer. Gathers observations, asks `net_recovery.decide()` what to do, saves the
persistent state (before acting, so a reboot keeps the record) and runs the action: bounce the interface, restart
NetworkManager, or reboot. It speaks `no_wifi.wav` at most once per episode and only with do-not-disturb off. It
depends on nothing but the Pi itself (no network, HA or brain)."""
import argparse, json, os, subprocess, sys, time, urllib.request

from net_recovery import (BOUNCE, NONE, REBOOT, RESTART_NM, STATE_PATH, Observation, decide, load_state,
                          save_state)

SONS = os.environ.get("FRIDAY_SONS", "/home/jose/satelite/sons")
CFG_PATH = "/etc/friday-satelite/config.json"
WIFI_TYPE, ETH_TYPE = "802-11-wireless", "802-3-ethernet"


def estado_rede(run=subprocess.run) -> str:
    try:
        r = run(["nmcli", "-t", "-f", "STATE", "general"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return (r.stdout or "").strip().splitlines()[0] if r.returncode == 0 and (r.stdout or "").strip() else "unknown"


def gateway(run=subprocess.run):
    """IP do gateway da rota padrão, ou None (sem rota padrão também é rede fora)."""
    try:
        r = run(["ip", "-4", "route", "show", "default"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    partes = (r.stdout or "").split()
    return partes[partes.index("via") + 1] if "via" in partes[:-1] else None


def gateway_responde(gw: str, run=subprocess.run) -> bool:
    """08/11/2026: o MK1 ficou horas com IP e o NetworkManager "connected" sem passar nada; só o ping prova a rede."""
    try:
        r = run(["ping", "-c", "3", "-W", "2", "-q", gw], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def rede_ok(run=subprocess.run) -> bool:
    if estado_rede(run) != "connected":
        return False
    gw = gateway(run)
    return gw is not None and gateway_responde(gw, run)


healthy = rede_ok


def _unescape(s: str) -> str:
    """nmcli -t escapes ':' and '\\' with a backslash."""
    out, i = [], 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            i += 1
        out.append(s[i])
        i += 1
    return "".join(out)


def _profiles(run, ptype: str) -> list:
    """Names of the saved NetworkManager profiles of the given type."""
    try:
        r = run(["nmcli", "-t", "-f", "NAME,TYPE", "con", "show"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return []
    if r.returncode != 0:
        return []
    names = []
    for line in (r.stdout or "").splitlines():
        if line.endswith(":" + ptype):
            names.append(_unescape(line[: -len(ptype) - 1]))
    return names


def monitored_interface(run=subprocess.run):
    """Interface of the default route; else wlan0/eth0 when a saved profile of that type exists; else None."""
    try:
        r = run(["ip", "-4", "route", "show", "default"], capture_output=True, text=True, timeout=10)
        partes = (r.stdout or "").split()
        if "dev" in partes[:-1]:
            return partes[partes.index("dev") + 1]
    except (OSError, subprocess.SubprocessError):
        pass
    if _profiles(run, WIFI_TYPE):
        return "wlan0"
    if _profiles(run, ETH_TYPE):
        return "eth0"
    return None


def pi_at_fault(dev, run=subprocess.run, sys_root="/sys/class/net") -> bool:
    """Proof the Pi is at fault. Wi-Fi: a saved SSID is visible in a scan. Cable: eth0 carrier is 1.
    (The caller already knows the gateway does not answer.) Anything else or any error: False."""
    try:
        if dev == "wlan0":
            wanted = set()
            for name in _profiles(run, WIFI_TYPE):
                r = run(["nmcli", "-g", "802-11-wireless.ssid", "con", "show", name],
                        capture_output=True, text=True, timeout=15)
                ssid = (r.stdout or "").strip()
                if r.returncode == 0 and ssid:
                    wanted.add(ssid)
            if not wanted:
                return False
            r = run(["nmcli", "-t", "-f", "SSID", "dev", "wifi", "list", "--rescan", "yes"],
                    capture_output=True, text=True, timeout=60)
            if r.returncode != 0:
                return False
            seen = {_unescape(line) for line in (r.stdout or "").splitlines()}
            return bool(wanted & seen)
        if dev == "eth0":
            with open(os.path.join(sys_root, "eth0", "carrier")) as f:
                return f.read().strip() == "1"
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    return False


def _read_cfg(cfg_path):
    with open(cfg_path) as f:
        cfg = json.load(f)
    if not isinstance(cfg, dict):
        raise ValueError("config is not an object")
    return cfg


def _http_get(url, headers=None, timeout=3):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def conversation_open(cfg_path=CFG_PATH, http_get=_http_get) -> bool:
    """True iff the main program's /status says `estado` != "parada". Any error -> False. Never prints the password."""
    try:
        cfg = _read_cfg(cfg_path)
        url = f"http://127.0.0.1:{cfg['porta_local']}/status"
        data = http_get(url, headers={"Authorization": "Bearer " + str(cfg["senha_porta_local"])}, timeout=3)
        return data["estado"] != "parada"
    except Exception:
        return False


def dnd_on(cfg_path=CFG_PATH) -> bool:
    """Do-not-disturb from the config; unreadable config counts as ON (silent)."""
    try:
        return bool(_read_cfg(cfg_path)["nao_perturbe"])
    except Exception:
        return True


def boot_id(path="/proc/sys/kernel/random/boot_id") -> str:
    with open(path) as f:
        return f.read().strip()


def uptime_s(path="/proc/uptime") -> float:
    with open(path) as f:
        return float(f.read().split()[0])


def run_action(action, dev, run=subprocess.run, sleep=time.sleep) -> bool:
    """Run one ladder action. True when every command exits 0 (NONE does nothing)."""
    if action == NONE:
        return True
    if action == BOUNCE:
        steps = [["sudo", "ip", "link", "set", dev, "down"], None, ["sudo", "ip", "link", "set", dev, "up"]]
    elif action == RESTART_NM:
        steps = [["sudo", "systemctl", "restart", "NetworkManager"]]
    elif action == REBOOT:
        steps = [["sudo", "systemctl", "reboot"]]
    else:
        return False
    ok = True
    for argv in steps:
        if argv is None:
            sleep(3)
            continue
        try:
            r = run(argv, capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError):
            ok = False
            continue
        ok = ok and r.returncode == 0
    return ok


def _speak(run, sons) -> bool:
    arq = os.path.join(sons, "onboarding", "no_wifi.wav")
    if not os.path.isfile(arq):
        return False
    try:
        return run(["aplay", "-q", "-D", "voice", arq], timeout=60).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def passo(run=subprocess.run, now=time.time, state_path=STATE_PATH, cfg_path=CFG_PATH, sons=SONS, dry_run=False,
          sleep=time.sleep, http_get=_http_get, boot_path="/proc/sys/kernel/random/boot_id",
          uptime_path="/proc/uptime", sys_root="/sys/class/net") -> dict:
    dev = monitored_interface(run)
    ok = healthy(run) if dev else True
    fault = conv = False
    if dev and not ok:
        fault = pi_at_fault(dev, run, sys_root=sys_root)
        conv = conversation_open(cfg_path, http_get=http_get)
    try:
        bid, up = boot_id(boot_path), uptime_s(uptime_path)
    except (OSError, ValueError, IndexError):
        bid, up = "unknown", 0.0
    obs = Observation(has_profile=dev is not None, healthy=ok, pi_at_fault=fault, conversation_open=conv,
                      boot_id=bid, uptime_s=up)
    t = now()
    decision = decide(load_state(state_path), obs, t)
    action = decision.action
    if not dry_run:
        try:
            save_state(decision.state, state_path)  # before acting, so a reboot keeps the record
        except OSError as e:
            print(json.dumps({"state_save_failed": str(e)}), file=sys.stderr, flush=True)
            if action == REBOOT:
                action = NONE  # never reboot without the record: it is what caps the loop
    spoke = False
    if decision.speak and not dry_run and not dnd_on(cfg_path):
        spoke = _speak(run, sons)
    if action != NONE and not dry_run:
        run_action(action, dev, run=run, sleep=sleep)
    st = decision.state
    result = {"interface": dev, "healthy": ok, "pi_at_fault": fault, "action": action,
              "exhausted": st["exhausted"], "spoke": spoke, "failed_rounds": st["failed_rounds"],
              "chain_reboots_24h": len(st["chain_reboots"]), "dry_run": dry_run}
    print(json.dumps(result), flush=True)
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="F.R.I.D.A.Y. satellite network recovery")
    ap.add_argument("--dry-run", action="store_true", help="decide and report; run no sudo, speak nothing, keep state")
    ap.add_argument("--state", default=STATE_PATH)
    a = ap.parse_args(argv)
    passo(state_path=a.state, dry_run=a.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
