import copy, json, os, tempfile, unittest

import net_recovery as nr


def obs(**kw):
    base = dict(has_profile=True, healthy=False, pi_at_fault=True, conversation_open=False, boot_id="b1", uptime_s=10_000)
    base.update(kw)
    return nr.Observation(**base)


def run_rounds(state, n, now=1000.0, **kw):
    """Run n rounds; return (final_state, [Decision...])."""
    out = []
    for i in range(n):
        d = nr.decide(state, obs(**kw), now + i)
        state = d.state
        out.append(d)
    return state, out


class DecideTest(unittest.TestCase):
    def test_healthy_resets_everything(self):
        st = dict(nr.fresh_state(), failed_rounds=5, step=3, exhausted=True, announced=True)
        d = nr.decide(st, obs(healthy=True, boot_id="b1"), 1000.0)
        self.assertEqual(d.action, nr.NONE)
        self.assertIs(d.state["exhausted"], False)
        self.assertEqual(d.state["failed_rounds"], 0)
        self.assertIs(d.state["announced"], False)
        self.assertIs(d.speak, False)

    def test_two_rounds_before_first_step_and_speaks_once(self):
        _, ds = run_rounds(nr.fresh_state(), 3)
        self.assertEqual([d.action for d in ds], [nr.NONE, nr.BOUNCE, nr.RESTART_NM])
        self.assertEqual([d.speak for d in ds], [False, True, False])

    def test_full_ladder_then_reboot_recorded(self):
        st, ds = run_rounds(nr.fresh_state(), 4, now=5000.0)
        self.assertEqual([d.action for d in ds], [nr.NONE, nr.BOUNCE, nr.RESTART_NM, nr.REBOOT])
        self.assertEqual(len(st["chain_reboots"]), 1)
        self.assertEqual(st["chain_reboots"][0], {"ts": 5003.0, "boot_id": "b1"})

    def test_new_boot_restarts_ladder_keeps_cap(self):
        st, _ = run_rounds(nr.fresh_state(), 4)
        st, ds = run_rounds(st, 2, boot_id="b2")
        self.assertEqual([d.action for d in ds], [nr.NONE, nr.BOUNCE])
        self.assertEqual(len(st["chain_reboots"]), 1)

    def test_reboot_cap_and_window(self):
        now = 1_000_000.0
        full = [{"ts": now - 100, "boot_id": "x%d" % i} for i in range(3)]
        st = dict(nr.fresh_state(), boot_id="b1", chain_reboots=full)
        st, ds = run_rounds(st, 4, now=now)
        self.assertEqual(ds[3].action in (nr.BOUNCE, nr.RESTART_NM), True)
        self.assertNotIn(nr.REBOOT, [d.action for d in ds])
        self.assertIs(ds[3].state["exhausted"], True)
        # same state once the oldest entries left the 24 h window
        st2 = dict(nr.fresh_state(), boot_id="b1", chain_reboots=full)
        later = now - 100 + nr.REBOOT_WINDOW_S + 1
        _, ds2 = run_rounds(st2, 4, now=later)
        self.assertEqual(ds2[3].action, nr.REBOOT)
        self.assertIs(ds2[3].state["exhausted"], False)

    def test_manual_reboot_not_counted(self):
        st = dict(nr.fresh_state(), boot_id="b1", failed_rounds=1)
        d = nr.decide(st, obs(boot_id="b2"), 1000.0)
        self.assertEqual(d.state["chain_reboots"], [])

    def test_not_pi_fault_never_reboots_nor_exhausts(self):
        st, ds = run_rounds(nr.fresh_state(), 20, pi_at_fault=False)
        acts = [d.action for d in ds]
        self.assertNotIn(nr.REBOOT, acts)
        self.assertEqual(acts[0], nr.NONE)
        self.assertEqual(acts[1:5], [nr.BOUNCE, nr.RESTART_NM, nr.BOUNCE, nr.RESTART_NM])
        self.assertTrue(all(d.state["exhausted"] is False for d in ds))
        self.assertEqual(sum(1 for d in ds if d.speak), 1)

    def test_grace_and_conversation_do_not_count(self):
        st, ds = run_rounds(nr.fresh_state(), 5, uptime_s=nr.BOOT_GRACE_S - 1)
        self.assertTrue(all(d.action == nr.NONE for d in ds))
        st, ds = run_rounds(st, 5, conversation_open=True)
        self.assertTrue(all(d.action == nr.NONE and not d.speak for d in ds))
        self.assertEqual(st["failed_rounds"], 0)

    def test_no_profile_is_idle(self):
        st = dict(nr.fresh_state(), boot_id="b1", exhausted=True, announced=True, failed_rounds=4, step=2)
        d = nr.decide(st, obs(has_profile=False), 1000.0)
        self.assertEqual(d.action, nr.NONE)
        self.assertIs(d.state["exhausted"], False)
        self.assertIs(d.speak, False)

    def test_malformed_chain_reboots_are_dropped(self):
        # revisão 08/11: um registro sem "ts" (ou com ts de texto) no arquivo não pode derrubar o vigia
        st = nr.fresh_state()
        st.update(boot_id="b1", chain_reboots=[{"boot_id": "x"}, {"ts": "abc"}, "lixo", {"ts": 900.0, "boot_id": "b0"}])
        d = nr.decide(st, obs(healthy=True), 1000.0)
        self.assertEqual(d.state["chain_reboots"], [{"ts": 900.0, "boot_id": "b0"}])

    def test_decide_does_not_mutate_input(self):
        st = dict(nr.fresh_state(), boot_id="b0", failed_rounds=1, chain_reboots=[{"ts": 1.0, "boot_id": "z"}])
        before = copy.deepcopy(st)
        nr.decide(st, obs(boot_id="b1"), 1000.0)
        nr.decide(st, obs(boot_id="b0"), 1000.0)
        self.assertEqual(st, before)


class StateTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = os.path.join(self.d, "sub", "state.json")

    def _write(self, text):
        os.makedirs(os.path.dirname(self.p), exist_ok=True)
        with open(self.p, "w") as f:
            f.write(text)

    def test_load_state_corrupt_or_missing(self):
        self.assertEqual(nr.load_state(self.p), nr.fresh_state())
        for bad in ("{", "[]", "x"):
            self._write(bad)
            self.assertEqual(nr.load_state(self.p), nr.fresh_state())
        self._write('{"step": 2}')
        expected = dict(nr.fresh_state(), step=2)
        self.assertEqual(nr.load_state(self.p), expected)

    def test_save_then_load_roundtrip(self):
        st = dict(nr.fresh_state(), boot_id="b1", step=2, chain_reboots=[{"ts": 1.5, "boot_id": "b1"}])
        nr.save_state(st, self.p)
        self.assertEqual(nr.load_state(self.p), st)
        self.assertEqual(os.listdir(os.path.dirname(self.p)), ["state.json"])  # no temp file left

    def test_read_exhausted_missing_or_corrupt(self):
        self.assertIs(nr.read_exhausted(self.p), False)
        self._write("x")
        self.assertIs(nr.read_exhausted(self.p), False)
        self._write('{"exhausted": true}')
        self.assertIs(nr.read_exhausted(self.p), True)
        self._write('{"exhausted": false}')
        self.assertIs(nr.read_exhausted(self.p), False)


if __name__ == "__main__":
    unittest.main()
