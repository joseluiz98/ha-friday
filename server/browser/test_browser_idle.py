import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import browser_idle as b

STAT = "{pid} ({comm}) S 1 1 1 0 -1 4194560 100 0 0 0 {ut} {st} 0 0 20 0 5 0 {start} 1000 100 0"


def scan_line(pid, ut, st, start, comm="chromium"):
    return f"{pid} " + STAT.format(pid=pid, comm=comm, ut=ut, st=st, start=start)


class Parse(unittest.TestCase):
    def test_parses_ticks_and_start(self):
        p = b.parse_scan(scan_line(10, 7, 3, 555) + "\n" + scan_line(11, 1, 1, 560, comm="Web Content (x)"))
        self.assertEqual(p, {10: (555, 10), 11: (560, 2)})

    def test_garbage_lines_ignored(self):
        self.assertEqual(b.parse_scan("\nnot a stat line\n10 broken"), {})


class Delta(unittest.TestCase):
    def test_same_process_counts_difference(self):
        self.assertEqual(b.cpu_delta({"10": [5, 100]}, {10: (5, 130)}), 30)

    def test_reused_pid_counts_all_ticks(self):
        self.assertEqual(b.cpu_delta({"10": [5, 100]}, {10: (9, 40)}), 40)

    def test_new_pid_counts_all_ticks_and_vanished_nothing(self):
        self.assertEqual(b.cpu_delta({"10": [5, 100], "11": [6, 50]}, {10: (5, 100), 12: (7, 20)}), 20)


class Decide(unittest.TestCase):
    P = {10: (5, 100), 11: (6, 50)}

    def run_series(self, steps, activity=None):
        """steps: list of (now, procs); returns the list of close decisions."""
        state, out = {}, []
        for now, procs in steps:
            state, close = b.decide(state, procs, activity, now)
            out.append(close)
        return out

    def test_no_chromium_clears_state(self):
        self.assertEqual(b.decide({"last_active": 1}, {}, None, 100), ({}, False))

    def test_closes_only_after_idle_window_from_first_sight(self):
        steps = [(t, self.P) for t in range(0, 700, 120)]       # 0,120,...,600
        self.assertEqual(self.run_series(steps), [False] * 5 + [True])

    def test_cpu_use_resets_the_window(self):
        busy = {10: (5, 400), 11: (6, 50)}
        steps = [(0, self.P), (120, self.P), (240, busy), (600, busy), (830, busy)]
        self.assertEqual(self.run_series(steps), [False, False, False, False, False])
        self.assertTrue(self.run_series(steps + [(840, busy)])[-1])

    def test_small_background_noise_is_still_idle(self):
        t, p, closes = 0, dict(self.P), []
        state = {}
        for _ in range(8):
            state, c = b.decide(state, p, None, t)
            closes.append(c)
            t += 120
            p = {10: (5, p[10][1] + 12), 11: (6, p[11][1] + 3)}
        self.assertTrue(closes[-1])

    def test_brain_turn_keeps_it_open(self):
        act = {"updated_at": 590, "brain_turns": 1}
        state, close = b.decide({"procs": {"10": [5, 100], "11": [6, 50]}, "last_active": 0}, self.P, act, 600)
        self.assertFalse(close)
        self.assertEqual(state["last_active"], 600)

    def test_stale_activity_file_is_ignored(self):
        state = {"procs": {"10": [5, 100], "11": [6, 50]}, "last_active": 0}
        _, close = b.decide(state, self.P, {"updated_at": 100, "brain_turns": 1}, 700)
        self.assertTrue(close)

    def test_missing_activity_file_is_ignored(self):
        state = {"procs": {"10": [5, 100], "11": [6, 50]}, "last_active": 0}
        self.assertTrue(b.decide(state, self.P, None, 700)[1])

    def test_relaunched_browser_restarts_the_window(self):
        state = {"procs": {"10": [5, 100]}, "last_active": 0}
        new = {20: (900, 165)}                                   # all-new processes burn their startup ticks
        st, close = b.decide(state, new, None, 700)
        self.assertFalse(close)
        self.assertEqual(st["last_active"], 700)


class Brain(unittest.TestCase):
    def test_brain_busy(self):
        self.assertTrue(b.brain_busy({"updated_at": 100, "brain_turns": 2}, 150))
        self.assertFalse(b.brain_busy({"updated_at": 100, "brain_turns": 0}, 150))
        self.assertFalse(b.brain_busy({"updated_at": 100, "brain_turns": 2}, 300))
        self.assertFalse(b.brain_busy(None, 150))


class Scan(unittest.TestCase):
    def test_scan_selects_by_executable_directory_only(self):
        self.assertTrue(b.SCAN.count(b.CHROMIUM_DIR) == 1)
        self.assertNotIn("pkill", b.SCAN)
        self.assertNotIn("pgrep", b.SCAN)


class State(unittest.TestCase):
    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            self.assertIsNone(b.load(p))
            b.save(p, {"last_active": 5})
            self.assertEqual(b.load(p), {"last_active": 5})
            with open(p, "w") as f:
                f.write("{broken")
            self.assertIsNone(b.load(p))


if __name__ == "__main__":
    unittest.main()
