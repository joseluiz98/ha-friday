"""USB watchdog for the XVF3800 (spec 2026-11-17-xvf3800-support): capture slow or stopped, or a new kernel `error -71`
-> reboot the Pi, at most 3 per hour; after the boot one event says what happened and whether capture came back."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "satellite"))
from friday_satellite_agent import usb_watch as uw  # noqa: E402


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def ok(clock, rate=1.0):
    return {"captura_taxa": rate, "captura_ts": clock.t - 2}


def watch(tmp_path, clock):
    return uw.UsbWatch(str(tmp_path / "usb.json"), now=clock)


def test_three_slow_windows_reboot(tmp_path):
    c = Clock(); w = watch(tmp_path, c)
    assert w.observe("xvf3800", ok(c, 0.5), 0) is None
    assert w.observe("xvf3800", ok(c, 0.4), 0) is None
    assert w.observe("xvf3800", ok(c, 0.2), 0) == "reboot"
    assert w.reason == "capture_slow"


def test_two_slow_then_ok_no_reboot(tmp_path):
    c = Clock(); w = watch(tmp_path, c)
    w.observe("xvf3800", ok(c, 0.5), 0); w.observe("xvf3800", ok(c, 0.5), 0)
    assert w.observe("xvf3800", ok(c, 1.0), 0) is None
    assert w.observe("xvf3800", ok(c, 0.5), 0) is None


def test_stale_capture_reboots(tmp_path):
    c = Clock(); w = watch(tmp_path, c)
    assert w.observe("xvf3800", {"captura_taxa": 1.0, "captura_ts": c.t - 31}, 0) == "reboot"
    assert w.reason == "capture_stopped"


def test_kernel_71_reboots(tmp_path):
    c = Clock(); w = watch(tmp_path, c)
    assert w.observe("xvf3800", ok(c), 1) == "reboot"
    assert w.reason == "usb_error_71"


def test_jabra_never_reboots(tmp_path):
    c = Clock(); w = watch(tmp_path, c)
    for _ in range(5):
        assert w.observe("jabra", ok(c, 0.1), 1) is None


def test_program_stopped_on_purpose_never_reboots(tmp_path):
    c = Clock(); w = watch(tmp_path, c)
    for _ in range(5):
        assert w.observe("xvf3800", None, 0) is None  # program not answering (switch off, OTA, restart)


def test_rate_unknown_yet_is_not_a_stall(tmp_path):
    c = Clock(); w = watch(tmp_path, c)
    for _ in range(5):
        assert w.observe("xvf3800", {"captura_taxa": None, "captura_ts": None}, 0) is None


def test_max_three_per_hour_then_exhausted(tmp_path):
    c = Clock(); w = watch(tmp_path, c)
    for i in range(3):
        assert w.observe("xvf3800", ok(c), 1) == "reboot"
        w.record_reboot()
        c.t += 600
        w = watch(tmp_path, c)  # every reboot is a new agent
    assert w.observe("xvf3800", ok(c), 1) == "exhausted"
    assert w.observe("xvf3800", ok(c), 1) is None  # said once
    c.t += 3600
    assert w.observe("xvf3800", ok(c), 1) == "reboot"


def test_after_boot_event_and_count_persist(tmp_path):
    c = Clock(); w = watch(tmp_path, c)
    w.observe("xvf3800", ok(c), 1); w.record_reboot()
    assert w.count == 1
    c.t += 120
    w2 = watch(tmp_path, c)
    assert w2.count == 1
    ev = w2.after_boot(ok(c))
    assert ev == {"reason": "usb_error_71", "at": 1000.0, "recovered": True}
    assert w2.after_boot(ok(c)) is None  # only once


def test_after_boot_not_recovered_after_five_minutes(tmp_path):
    c = Clock(); w = watch(tmp_path, c)
    w.observe("xvf3800", ok(c), 1); w.record_reboot()
    c.t += 60
    w2 = watch(tmp_path, c)
    assert w2.after_boot(None) is None  # still waiting for the program
    c.t += 300
    assert w2.after_boot(None)["recovered"] is False


def test_kernel_71_counter_matches_only_error_71(tmp_path):
    from friday_satellite_agent import hardware as hw
    out = "usb 1-1.4: can't set config #1, error -71\nother\nusb 1-1.4: uac_clock_source_is_valid error -71\n-- cursor: s=1\n"
    c = hw.UndervoltageCounter(run=lambda argv: out, read=lambda p: "boot1\n", path=str(tmp_path / "k71.json"),
                               match="error -71")
    assert c.sample() == 2


def test_descriptors_only_on_the_xvf():
    from friday_satellite_agent import capabilities as cap
    keys = lambda b: {d["key"]: d for d in cap.controls_from_config({}, [], board=b)}
    x = keys("xvf3800")
    assert x["usb_recoveries"]["state_class"] == "total_increasing" and x["usb_recoveries"]["category"] == "diagnostic"
    assert x["usb_recovery"]["platform"] == "event" and set(x["usb_recovery"]["event_types"]) == {"rebooted", "exhausted"}
    assert "usb_recoveries" not in keys("jabra") and "usb_recovery" not in keys("jabra")


class Core:
    def __init__(self):
        self.states, self.events, self.logs = [], [], []

    async def push_state(self, changes, origin):
        self.states.append(changes)

    async def push_event(self, key, etype, data):
        self.events.append((key, etype, data))

    async def push_log(self, message, data):
        self.logs.append(message)


class Program:
    def __init__(self, statuses):
        self.statuses = list(statuses)

    async def status(self):
        return self.statuses.pop(0) if self.statuses else {"captura_taxa": 1.0, "captura_ts": None}


def test_loop_reboots_on_new_kernel_71(tmp_path):
    import asyncio
    c = Clock()
    counts = iter([5, 5, 6])  # baseline 5 (old lines of this boot), then one new -71
    reboots = []

    async def reboot():
        reboots.append(1)
        raise asyncio.CancelledError  # the Pi goes down here

    async def nosleep(_s):
        return None

    core = Core()
    w = uw.UsbWatch(str(tmp_path / "usb.json"), now=c)
    try:
        asyncio.run(uw.run("xvf3800", Program([ok(c), ok(c)]), core, lambda: next(counts), w, reboot, sleep=nosleep))
    except asyncio.CancelledError:
        pass
    assert reboots == [1] and w.count == 1
    assert core.states[0] == {"usb_recoveries": 0} and core.states[-1] == {"usb_recoveries": 1}
    assert any("usb_error_71" in m for m in core.logs)


def test_loop_does_nothing_on_the_jabra(tmp_path):
    import asyncio
    core = Core()
    asyncio.run(uw.run("jabra", Program([]), core, lambda: 0, uw.UsbWatch(str(tmp_path / "u.json")), None))
    assert core.states == [] and core.events == []
