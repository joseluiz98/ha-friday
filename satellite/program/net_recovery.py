"""Pure decision logic for the satellite network recovery chain (spec 2026-11-08).

`netwatch.py` gathers observations, calls `decide()`, saves the state and runs the action. Nothing here touches the
network or runs a command; only the state file helpers touch disk. Ladder: bounce_interface -> restart_network_manager
-> reboot, with a cap of MAX_CHAIN_REBOOTS chain-issued reboots per rolling REBOOT_WINDOW_S that survives reboots."""
import copy, json, os, tempfile
from dataclasses import dataclass

STATE_PATH = "/var/lib/friday-netwatch/state.json"
FAILED_ROUNDS_BEFORE_ACTION = 2
MAX_CHAIN_REBOOTS = 3
REBOOT_WINDOW_S = 86400
BOOT_GRACE_S = 300

NONE = "none"
BOUNCE = "bounce_interface"
RESTART_NM = "restart_network_manager"
REBOOT = "reboot"


@dataclass(frozen=True)
class Observation:
    has_profile: bool
    healthy: bool
    pi_at_fault: bool
    conversation_open: bool
    boot_id: str
    uptime_s: float


@dataclass(frozen=True)
class Decision:
    action: str
    state: dict
    speak: bool


def fresh_state() -> dict:
    return {"boot_id": None, "failed_rounds": 0, "step": 0, "exhausted": False, "announced": False,
            "chain_reboots": []}


def load_state(path: str = STATE_PATH) -> dict:
    """Missing, corrupt or wrong-typed file -> fresh state (never raises)."""
    st = fresh_state()
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return st
    if not isinstance(data, dict):
        return st
    for k in st:
        if k in data:
            st[k] = data[k]
    if not isinstance(st["chain_reboots"], list):
        st["chain_reboots"] = []
    return st


def save_state(state: dict, path: str = STATE_PATH) -> None:
    """Atomic write: temp file in the same directory, then os.replace."""
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".state-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_exhausted(path: str = STATE_PATH) -> bool:
    try:
        with open(path) as f:
            data = json.load(f)
        return bool(isinstance(data, dict) and data.get("exhausted") is True)
    except (OSError, ValueError):
        return False


def _reset(s: dict) -> None:
    s["failed_rounds"] = 0
    s["step"] = 0
    s["exhausted"] = False
    s["announced"] = False


def decide(state: dict, obs: Observation, now: float) -> Decision:
    s = copy.deepcopy(state)
    # 1. a new boot restarts the ladder but keeps exhausted/announced/chain_reboots (a boot alone never counts)
    if s.get("boot_id") != obs.boot_id:
        s["boot_id"] = obs.boot_id
        s["failed_rounds"] = 0
        s["step"] = 0
    # 2. rolling window
    s["chain_reboots"] = [r for r in s.get("chain_reboots", [])
                          if isinstance(r, dict) and isinstance(r.get("ts"), (int, float))  # malformed: dropped
                          and now - r["ts"] <= REBOOT_WINDOW_S]
    # 3. nothing to manage without a saved profile
    if not obs.has_profile:
        _reset(s)
        return Decision(NONE, s, False)
    # 4. healthy: back to rest
    if obs.healthy:
        _reset(s)
        return Decision(NONE, s, False)
    # 5. never act right after boot or during a conversation; nothing counted
    if obs.uptime_s < BOOT_GRACE_S or obs.conversation_open:
        return Decision(NONE, s, False)
    # 6. need consecutive failed rounds
    s["failed_rounds"] += 1
    if s["failed_rounds"] < FAILED_ROUNDS_BEFORE_ACTION:
        return Decision(NONE, s, False)
    # 7. announce at most once per episode
    speak = not s["announced"]
    s["announced"] = True
    light = BOUNCE if s["step"] % 2 == 0 else RESTART_NM
    if not obs.pi_at_fault:
        # 8. no proof the Pi is at fault: light steps only, no reboot, no exhaustion
        s["step"] += 1
        return Decision(light, s, speak)
    if s["step"] < 2:
        # 9. the ladder proper
        action = [BOUNCE, RESTART_NM][s["step"]]
        s["step"] += 1
        return Decision(action, s, speak)
    if len(s["chain_reboots"]) < MAX_CHAIN_REBOOTS:
        # 10. reboot, recorded by the chain only
        s["chain_reboots"].append({"ts": now, "boot_id": obs.boot_id})
        return Decision(REBOOT, s, speak)
    # 11. cap reached: exhausted, keep trying the light steps
    s["exhausted"] = True
    s["step"] += 1
    return Decision(light, s, speak)
