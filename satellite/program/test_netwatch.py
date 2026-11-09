import contextlib, io, json, os, subprocess, tempfile, unittest

import net_recovery as nr
import netwatch


class Fake:
    """Injectable `run`: answers ip / ping / nmcli / sudo / aplay without touching the system."""

    def __init__(self, state="connected", dev="wlan0", gw="192.0.2.1", ping_code=0, profiles=None,
                 ssids=None, visible=None, aplay_code=0, sudo_code=0, on_sudo=None):
        self.state, self.dev, self.gw, self.ping_code = state, dev, gw, ping_code
        self.profiles = [("casa", "802-11-wireless")] if profiles is None else profiles
        self.ssids = {"casa": "MinhaRede"} if ssids is None else ssids
        self.visible = ["Outra", "MinhaRede"] if visible is None else visible
        self.aplay_code, self.sudo_code, self.on_sudo = aplay_code, sudo_code, on_sudo
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        out, code = "", 0
        if argv[0] == "nmcli":
            if argv[1:] == ["-t", "-f", "STATE", "general"]:
                out, code = (f"{self.state}\n", 0) if self.state is not None else ("", 1)
            elif argv[1:] == ["-t", "-f", "NAME,TYPE", "con", "show"]:
                out = "".join(f"{n}:{t}\n" for n, t in self.profiles)
            elif argv[1] == "-g" and argv[2] == "802-11-wireless.ssid":
                out = self.ssids.get(argv[-1], "") + "\n"
            elif argv[1:4] == ["-t", "-f", "SSID"]:
                out = "".join(s + "\n" for s in self.visible)
        elif argv[0] == "ip":
            if self.gw:
                out = f"default via {self.gw} dev {self.dev} proto dhcp src 192.0.2.15 metric 600\n"
        elif argv[0] == "ping":
            code = self.ping_code
        elif argv[0] == "sudo":
            if self.on_sudo:
                self.on_sudo(argv)
            code = self.sudo_code
        elif argv[0] == "aplay":
            code = self.aplay_code
        return subprocess.CompletedProcess(argv, code, out, "")

    def sudo(self):
        return [c for c in self.calls if c[0] == "sudo"]

    def played(self):
        return [c for c in self.calls if c[0] == "aplay"]


class Base(unittest.TestCase):
    PASSWORD = "s3cr3t-pass-do-not-print"

    def setUp(self):
        self.d = tempfile.mkdtemp()
        j = lambda *p: os.path.join(self.d, *p)
        os.makedirs(j("sons", "onboarding"))
        self.wav = j("sons", "onboarding", "no_wifi.wav")
        open(self.wav, "wb").write(b"RIFF")
        self.state_path = j("state", "state.json")
        self.cfg = j("config.json")
        self.write_cfg(nao_perturbe=False)
        self.boot_path, self.up_path = j("boot_id"), j("uptime")
        open(self.boot_path, "w").write("boot-1\n")
        self.set_uptime(10_000)
        self.sys_root = j("sys")
        self.t = [1000.0]
        self.conv = {"estado": "parada"}
        self.http_calls = []

    def write_cfg(self, **kw):
        cfg = {"porta_local": 8700, "senha_porta_local": self.PASSWORD}
        cfg.update(kw)
        json.dump(cfg, open(self.cfg, "w"))

    def set_uptime(self, s):
        open(self.up_path, "w").write(f"{s} 123.4\n")

    def http_get(self, url, headers=None, timeout=3):
        self.http_calls.append((url, dict(headers or {}), timeout))
        return self.conv

    def step(self, fake, **kw):
        args = dict(run=fake, now=lambda: self.t[0], state_path=self.state_path, cfg_path=self.cfg,
                    sons=os.path.join(self.d, "sons"), sleep=lambda s: None, http_get=self.http_get,
                    boot_path=self.boot_path, uptime_path=self.up_path, sys_root=self.sys_root)
        args.update(kw)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            res = netwatch.passo(**args)
        self.out = buf.getvalue()
        return res

    def state(self):
        return json.load(open(self.state_path))


class PassoTest(Base):
    def test_healthy_round_runs_nothing_and_is_silent(self):
        f = Fake()
        res = self.step(f)
        self.assertEqual(res["action"], "none")
        self.assertIs(res["healthy"], True)
        self.assertEqual(f.sudo(), [])
        self.assertEqual(f.played(), [])
        self.assertEqual(json.loads(self.out.strip().splitlines()[-1]), res)
        for k in ("interface", "healthy", "pi_at_fault", "action", "exhausted", "spoke", "failed_rounds",
                  "chain_reboots_24h", "dry_run"):
            self.assertIn(k, res)

    def test_second_failed_round_bounces_and_speaks_with_dnd_off(self):
        f = Fake(ping_code=1)
        r1 = self.step(f)
        self.assertEqual(r1["action"], "none")
        self.assertEqual(f.sudo(), [])
        r2 = self.step(f)
        self.assertEqual(r2["action"], nr.BOUNCE)
        self.assertEqual(f.sudo(), [["sudo", "ip", "link", "set", "wlan0", "down"],
                                    ["sudo", "ip", "link", "set", "wlan0", "up"]])
        self.assertEqual(f.played(), [["aplay", "-q", "-D", "voice", self.wav]])
        self.assertIs(r2["spoke"], True)
        r3 = self.step(f)  # next step: restart NM, no second speech
        self.assertEqual(r3["action"], nr.RESTART_NM)
        self.assertIn(["sudo", "systemctl", "restart", "NetworkManager"], f.sudo())
        self.assertEqual(len(f.played()), 1)

    def test_dnd_on_never_speaks(self):
        self.write_cfg(nao_perturbe=True)
        f = Fake(ping_code=1)
        self.step(f)
        r = self.step(f)
        self.assertEqual(f.played(), [])
        self.assertIs(r["spoke"], False)
        self.assertEqual(r["action"], nr.BOUNCE)
        self.assertEqual(len(f.sudo()), 2)

    def test_unreadable_config_counts_as_dnd(self):
        os.remove(self.cfg)
        f = Fake(ping_code=1)
        self.step(f)
        r = self.step(f)
        self.assertEqual(f.played(), [])
        self.assertEqual(r["action"], nr.BOUNCE)

    def test_state_saved_before_reboot(self):
        seen = []

        def check(argv):
            if argv[1:] == ["systemctl", "reboot"]:
                seen.append(self.state()["chain_reboots"])

        os.makedirs(os.path.dirname(self.state_path))
        nr.save_state(dict(nr.fresh_state(), boot_id="boot-1", failed_rounds=1, step=2), self.state_path)
        f = Fake(ping_code=1, on_sudo=check)
        r = self.step(f)
        self.assertEqual(r["action"], nr.REBOOT)
        self.assertEqual(seen, [[{"ts": 1000.0, "boot_id": "boot-1"}]])
        self.assertEqual(r["chain_reboots_24h"], 1)

    def test_reboot_not_issued_when_state_cannot_be_saved(self):
        nr_save = netwatch.save_state
        netwatch.save_state = lambda *a, **k: (_ for _ in ()).throw(OSError("disk"))
        try:
            os.makedirs(os.path.dirname(self.state_path))
            nr.save_state(dict(nr.fresh_state(), boot_id="boot-1", failed_rounds=1, step=2), self.state_path)
            f = Fake(ping_code=1)
            self.step(f)
        finally:
            netwatch.save_state = nr_save
        self.assertNotIn(["sudo", "systemctl", "reboot"], f.sudo())

    def test_dry_run_runs_no_sudo_and_keeps_state(self):
        os.makedirs(os.path.dirname(self.state_path))
        nr.save_state(dict(nr.fresh_state(), boot_id="boot-1", failed_rounds=1, step=2), self.state_path)
        before = open(self.state_path).read()
        f = Fake(ping_code=1)
        r = self.step(f, dry_run=True)
        self.assertEqual(r["action"], nr.REBOOT)
        self.assertIs(r["dry_run"], True)
        self.assertEqual([c for c in f.calls if c[0] == "sudo"], [])
        self.assertEqual(f.played(), [])
        self.assertEqual(open(self.state_path).read(), before)

    def test_ssid_not_visible_is_not_pi_fault(self):
        f = Fake(ping_code=1, visible=["Outra"])
        self.assertIs(netwatch.pi_at_fault("wlan0", f, sys_root=self.sys_root), False)
        for _ in range(5):
            r = self.step(f)
        self.assertIs(r["pi_at_fault"], False)
        self.assertNotIn(["sudo", "systemctl", "reboot"], f.sudo())
        self.assertIs(r["exhausted"], False)

    def test_ssid_visible_is_pi_fault(self):
        self.assertIs(netwatch.pi_at_fault("wlan0", Fake(), sys_root=self.sys_root), True)

    def test_eth_carrier_one_is_pi_fault(self):
        os.makedirs(os.path.join(self.sys_root, "eth0"))
        carrier = os.path.join(self.sys_root, "eth0", "carrier")
        open(carrier, "w").write("1\n")
        f = Fake(dev="eth0", profiles=[("cabo", "802-3-ethernet")])
        self.assertIs(netwatch.pi_at_fault("eth0", f, sys_root=self.sys_root), True)
        open(carrier, "w").write("0\n")
        self.assertIs(netwatch.pi_at_fault("eth0", f, sys_root=self.sys_root), False)
        os.remove(carrier)
        self.assertIs(netwatch.pi_at_fault("eth0", f, sys_root=self.sys_root), False)
        self.assertIs(netwatch.pi_at_fault("usb0", f, sys_root=self.sys_root), False)

    def test_open_conversation_skips(self):
        self.conv = {"estado": "ouvindo"}
        f = Fake(ping_code=1)
        for _ in range(4):
            r = self.step(f)
        self.assertEqual(r["action"], "none")
        self.assertEqual(f.sudo(), [])
        self.assertEqual(f.played(), [])

    def test_boot_grace_skips(self):
        self.set_uptime(60)
        f = Fake(ping_code=1)
        for _ in range(4):
            r = self.step(f)
        self.assertEqual(r["action"], "none")
        self.assertEqual(f.sudo(), [])

    def test_no_profile_no_route_is_idle(self):
        f = Fake(gw=None, profiles=[], state="disconnected", ping_code=1)
        self.assertIsNone(netwatch.monitored_interface(f))
        for _ in range(4):
            r = self.step(f)
        self.assertEqual(r["action"], "none")
        self.assertIsNone(r["interface"])
        self.assertEqual(f.sudo(), [])
        self.assertEqual(f.played(), [])

    def test_password_never_printed_or_passed_to_commands(self):
        f = Fake(ping_code=1)
        self.step(f)
        self.step(f)
        self.assertNotIn(self.PASSWORD, self.out)
        self.assertNotIn(self.PASSWORD, json.dumps(f.calls))
        self.assertEqual(self.http_calls[-1][1], {"Authorization": "Bearer " + self.PASSWORD})
        self.assertEqual(self.http_calls[-1][0], "http://127.0.0.1:8700/status")
        self.assertEqual(self.http_calls[-1][2], 3)


class HelpersTest(Base):
    def test_monitored_interface_prefers_route_then_profiles(self):
        self.assertEqual(netwatch.monitored_interface(Fake(dev="eth0")), "eth0")
        self.assertEqual(netwatch.monitored_interface(Fake(gw=None)), "wlan0")
        self.assertEqual(netwatch.monitored_interface(Fake(gw=None, profiles=[("c", "802-3-ethernet")])), "eth0")
        self.assertEqual(netwatch.monitored_interface(Fake(gw=None, profiles=[("v", "vpn")])), None)

    def test_healthy_needs_state_gateway_and_ping(self):
        self.assertIs(netwatch.healthy(Fake()), True)
        self.assertIs(netwatch.healthy(Fake(state="disconnected")), False)
        self.assertIs(netwatch.healthy(Fake(state=None)), False)
        self.assertIs(netwatch.healthy(Fake(gw=None)), False)
        self.assertIs(netwatch.healthy(Fake(ping_code=1)), False)

    def test_conversation_open_and_dnd_errors(self):
        self.assertIs(netwatch.conversation_open(self.cfg, http_get=self.http_get), False)
        self.conv = {"estado": "cerebro"}
        self.assertIs(netwatch.conversation_open(self.cfg, http_get=self.http_get), True)

        def boom(*a, **k):
            raise OSError("down")

        self.assertIs(netwatch.conversation_open(self.cfg, http_get=boom), False)
        self.assertIs(netwatch.conversation_open("/nonexistent/cfg", http_get=self.http_get), False)
        self.assertIs(netwatch.dnd_on(self.cfg), False)
        self.write_cfg(nao_perturbe=True)
        self.assertIs(netwatch.dnd_on(self.cfg), True)
        self.assertIs(netwatch.dnd_on("/nonexistent/cfg"), True)
        open(self.cfg, "w").write("{")
        self.assertIs(netwatch.dnd_on(self.cfg), True)

    def test_boot_id_and_uptime(self):
        self.assertEqual(netwatch.boot_id(self.boot_path), "boot-1")
        self.assertEqual(netwatch.uptime_s(self.up_path), 10_000.0)

    def test_run_action(self):
        f = Fake()
        sleeps = []
        self.assertIs(netwatch.run_action(nr.BOUNCE, "wlan0", f, sleep=sleeps.append), True)
        self.assertEqual(f.calls, [["sudo", "ip", "link", "set", "wlan0", "down"],
                                   ["sudo", "ip", "link", "set", "wlan0", "up"]])
        self.assertEqual(sleeps, [3])
        f = Fake()
        netwatch.run_action(nr.RESTART_NM, "wlan0", f, sleep=lambda s: None)
        netwatch.run_action(nr.REBOOT, "wlan0", f, sleep=lambda s: None)
        netwatch.run_action(nr.NONE, "wlan0", f, sleep=lambda s: None)
        self.assertEqual(f.calls, [["sudo", "systemctl", "restart", "NetworkManager"],
                                   ["sudo", "systemctl", "reboot"]])
        self.assertIs(netwatch.run_action(nr.RESTART_NM, "wlan0", Fake(sudo_code=1)), False)


if __name__ == "__main__":
    unittest.main()
