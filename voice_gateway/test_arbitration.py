import asyncio, os, tempfile, time, unittest

from arbitration import Arbiter, load_offsets


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


def claim(cid, voice, score=0.8, **extra):
    return {"claim_id": cid, "wake_word": "friday", "score": score, "voice_dbfs": voice, **extra}


class ArbiterTest(unittest.IsolatedAsyncioTestCase):
    def make(self, sids=("A", "B"), window_s=0.05, **kw):
        self.clock = FakeClock()
        self.logs = []
        arb = Arbiter(clock=self.clock, window_s=window_s, log=self.logs.append, **kw)
        for s in sids:
            arb.presence(s)
        return arb

    async def test_alone_goes_immediately(self):
        arb = self.make(sids=("A",), window_s=0.2)
        t0 = time.monotonic()
        r = await arb.claim("A", claim("a1", -30))
        self.assertLess(time.monotonic() - t0, 0.05)
        self.assertEqual((r["verdict"], r["reason"]), ("go", "alone"))
        self.assertTrue(r["round_id"])

    async def test_presence_counts_only_others(self):
        arb = self.make(sids=("A", "B"))
        self.assertEqual(arb.presence("A"), 1)
        self.assertEqual(arb.presence("C"), 2)

    async def test_two_claims_best_score_wins(self):
        arb = self.make()
        a, b = await asyncio.gather(arb.claim("A", claim("a1", -30)), arb.claim("B", claim("b1", -24)))
        self.assertEqual((b["verdict"], b["reason"]), ("go", "best"))
        self.assertEqual((a["verdict"], a["reason"]), ("lost", "best"))
        self.assertEqual(a["round_id"], b["round_id"])

    async def test_offset_changes_winner(self):
        arb = self.make(offsets={"A": 8})
        a, b = await asyncio.gather(arb.claim("A", claim("a1", -30)), arb.claim("B", claim("b1", -24)))
        self.assertEqual(a["verdict"], "go")
        self.assertEqual(b["verdict"], "lost")

    async def test_tie_under_1db_uses_score(self):
        arb = self.make()
        a, b = await asyncio.gather(arb.claim("A", claim("a1", -24.0, score=0.7)),
                                    arb.claim("B", claim("b1", -24.6, score=0.9)))
        self.assertEqual(b["verdict"], "go")
        self.assertEqual(a["verdict"], "lost")

    async def test_gap_over_1db_ignores_score(self):
        arb = self.make()
        a, b = await asyncio.gather(arb.claim("A", claim("a1", -24.0, score=0.1)),
                                    arb.claim("B", claim("b1", -25.5, score=0.99)))
        self.assertEqual(a["verdict"], "go")

    async def test_only_one_heard_after_window(self):
        arb = self.make()
        r = await arb.claim("A", claim("a1", -30))
        self.assertEqual((r["verdict"], r["reason"]), ("go", "only_one_heard"))

    async def test_window_closes_early_when_all_online_claimed(self):
        arb = self.make(window_s=1.0)
        t0 = time.monotonic()
        await asyncio.gather(arb.claim("A", claim("a1", -30)), arb.claim("B", claim("b1", -24)))
        self.assertLess(time.monotonic() - t0, 0.3)

    async def test_late_claim_lost(self):
        arb = self.make()
        r = await arb.claim("A", claim("a1", -30))
        self.assertEqual(r["reason"], "only_one_heard")
        self.clock.advance(0.5)
        late = await arb.claim("B", claim("b1", -20))
        self.assertEqual((late["verdict"], late["reason"]), ("lost", "late"))
        self.assertEqual(late["round_id"], r["round_id"])

    async def test_claim_after_same_activation_window_opens_new_round(self):
        arb = self.make()
        r = await arb.claim("A", claim("a1", -30))
        self.clock.advance(2.0)
        arb.presence("A")
        new = await arb.claim("B", claim("b1", -20))
        self.assertEqual((new["verdict"], new["reason"]), ("go", "only_one_heard"))
        self.assertNotEqual(new["round_id"], r["round_id"])

    async def test_same_sid_after_round_is_a_new_activation(self):
        arb = self.make()
        r = await arb.claim("A", claim("a1", -30))
        self.clock.advance(0.5)
        again = await arb.claim("A", claim("a2", -30))
        self.assertEqual(again["verdict"], "go")
        self.assertNotEqual(again["round_id"], r["round_id"])

    async def test_stale_presence_not_counted(self):
        arb = self.make(sids=("A",))
        arb.presence("B")
        self.clock.advance(31)
        arb.presence("A")
        r = await arb.claim("A", claim("a1", -30))
        self.assertEqual((r["verdict"], r["reason"]), ("go", "alone"))

    async def test_busy_does_not_weigh(self):
        arb = self.make()
        a, b = await asyncio.gather(arb.claim("A", claim("a1", -20, busy=True)), arb.claim("B", claim("b1", -26)))
        self.assertEqual(a["verdict"], "go")
        self.assertEqual(b["verdict"], "lost")
        self.clock.advance(5)
        arb.presence("A"); arb.presence("B")
        a, b = await asyncio.gather(arb.claim("A", claim("a2", -26, busy=True)), arb.claim("B", claim("b2", -20)))
        self.assertEqual(b["verdict"], "go")
        self.assertEqual(a["verdict"], "lost")

    async def test_record_only_never_joins_round(self):
        arb = self.make(window_s=0.1)
        task_a = asyncio.ensure_future(arb.claim("A", claim("a1", -30)))
        await asyncio.sleep(0)
        r = await arb.claim("C", claim("c1", -10, record_only=True))
        self.assertEqual((r["verdict"], r["reason"]), ("go", "record_only"))
        b = await arb.claim("B", claim("b1", -24))
        a = await task_a
        self.assertEqual((b["verdict"], a["verdict"]), ("go", "lost"))
        rounds = [l for l in self.logs if l.get("reason") == "best"]
        self.assertEqual(len(rounds[0]["candidates"]), 2)

    async def test_same_satellite_twice_keeps_best(self):
        arb = self.make()
        a1, a2, b = await asyncio.gather(arb.claim("A", claim("a1", -40, score=0.5)),
                                         arb.claim("A", claim("a2", -20, score=0.9)),
                                         arb.claim("B", claim("b1", -24)))
        self.assertEqual(a1["verdict"], "go")
        self.assertEqual(a2["verdict"], "go")
        self.assertEqual(b["verdict"], "lost")
        closed = [l for l in self.logs if "candidates" in l and l["reason"] == "best"][0]
        self.assertEqual(len(closed["candidates"]), 2)
        self.assertEqual([c["claim_id"] for c in closed["candidates"] if c["sid"] == "A"], ["a2"])

    async def test_same_satellite_twice_counts_once_for_early_close(self):
        arb = self.make(window_s=0.15)
        t0 = time.monotonic()
        await asyncio.gather(arb.claim("A", claim("a1", -40)), arb.claim("A", claim("a2", -20)))
        self.assertGreaterEqual(time.monotonic() - t0, 0.12)

    async def test_round_is_logged(self):
        arb = self.make(offsets={"A": 2})
        await asyncio.gather(arb.claim("A", claim("a1", -30, busy=True)), arb.claim("B", claim("b1", -24)))
        rec = [l for l in self.logs if "candidates" in l][0]
        self.assertEqual(rec["winner"], "B")
        self.assertEqual(rec["reason"], "best")
        self.assertIn("ts", rec)
        self.assertIn("window_ms", rec)
        self.assertTrue(rec["round_id"])
        by = {c["sid"]: c for c in rec["candidates"]}
        self.assertEqual(set(by), {"A", "B"})
        self.assertEqual(by["A"], {"sid": "A", "claim_id": "a1", "wake_word": "friday", "score": 0.8,
                                   "voice_dbfs": -30, "offset_db": 2, "busy": True})
        self.assertFalse(by["B"]["busy"])

    async def test_round_opened_is_logged(self):
        arb = self.make()
        await asyncio.gather(arb.claim("A", claim("a1", -30)), arb.claim("B", claim("b1", -24)))
        opened = [l for l in self.logs if l.get("event") == "round_opened"]
        self.assertEqual(len(opened), 1)
        self.assertEqual(opened[0]["sid"], "A")
        self.assertIn("ts", opened[0])
        closed = [l for l in self.logs if "candidates" in l][0]
        self.assertEqual(opened[0]["round_id"], closed["round_id"])

    async def test_alone_and_record_only_are_logged(self):
        arb = self.make(sids=("A",))
        await arb.claim("A", claim("a1", -30))
        await arb.claim("A", claim("a2", -30, record_only=True))
        self.assertEqual([l["reason"] for l in self.logs], ["alone", "record_only"])
        self.assertEqual(self.logs[0]["candidates"][0]["claim_id"], "a1")
        self.assertFalse([l for l in self.logs if l.get("event") == "round_opened"])

    async def test_bad_numbers_do_not_wedge_the_arbiter(self):
        arb = self.make()
        guard = 2.0
        a, b = await asyncio.wait_for(asyncio.gather(
            arb.claim("A", {"claim_id": "a1", "wake_word": "friday", "score": "high", "voice_dbfs": None}),
            arb.claim("B", {"claim_id": "b1", "wake_word": "friday", "score": None, "voice_dbfs": "loud"})), guard)
        self.assertEqual(sorted([a["verdict"], b["verdict"]]), ["go", "lost"])
        self.assertIsNone(arb._round)
        self.clock.advance(5)
        arb.presence("A"); arb.presence("B")
        a, b = await asyncio.wait_for(asyncio.gather(
            arb.claim("A", claim("a2", -30)), arb.claim("B", claim("b2", -24))), guard)
        self.assertEqual((b["verdict"], a["verdict"]), ("go", "lost"))
        rec = [l for l in self.logs if "candidates" in l][0]
        self.assertEqual({c["voice_dbfs"] for c in rec["candidates"]}, {-120.0})

    async def test_pick_failure_still_closes_round(self):
        import arbitration
        arb = self.make()
        orig = arbitration.pick
        arbitration.pick = lambda *a, **k: 1 / 0
        try:
            with self.assertLogs(level="ERROR"):
                a, b = await asyncio.wait_for(asyncio.gather(
                    arb.claim("A", claim("a1", -30)), arb.claim("B", claim("b1", -24))), 2.0)
        finally:
            arbitration.pick = orig
        self.assertEqual(sorted([a["verdict"], b["verdict"]]), ["go", "lost"])
        self.assertEqual([l["reason"] for l in self.logs if "candidates" in l], ["error"])
        self.clock.advance(5)
        arb.presence("A"); arb.presence("B")
        r = await asyncio.wait_for(arb.claim("A", claim("a2", -30)), 2.0)
        self.assertEqual(r["verdict"], "go")

    async def test_log_failure_does_not_break_claim(self):
        arb = self.make(sids=("A",))
        arb._log = lambda rec: 1 / 0
        with self.assertLogs(level="ERROR"):
            r = await arb.claim("A", claim("a1", -30))
        self.assertEqual(r["verdict"], "go")


class SetBusyTest(unittest.IsolatedAsyncioTestCase):
    async def test_set_busy_marks_candidate(self):
        clock = FakeClock()
        logs = []
        arb = Arbiter(clock=clock, window_s=0.05, log=logs.append)
        arb.presence("A")
        arb.presence("B")
        arb.set_busy("A", True)
        await asyncio.gather(arb.claim("A", claim("a1", -30)), arb.claim("B", claim("b1", -20)))
        rec = next(r for r in logs if "candidates" in r)
        busy = {c["sid"]: c["busy"] for c in rec["candidates"]}
        self.assertEqual(busy, {"A": True, "B": False})
        self.assertEqual(rec["winner"], "B")  # busy never weighs in
        arb.set_busy("A", False)
        logs.clear()
        clock.advance(5)  # past same_activation_s
        arb.presence("B")
        await asyncio.gather(arb.claim("A", claim("a2", -30)), arb.claim("B", claim("b2", -20)))
        rec = next(r for r in logs if "candidates" in r)
        self.assertFalse(any(c["busy"] for c in rec["candidates"]))


class LoadOffsetsTest(unittest.TestCase):
    def test_load_offsets_missing_file_is_empty(self):
        self.assertEqual(load_offsets("/nonexistent/offsets.json"), {})

    def test_load_offsets_reads_and_ignores_garbage(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "o.json")
            with open(p, "w") as f:
                f.write('{"A": 8, "B": -2.5, "C": "x"}')
            self.assertEqual(load_offsets(p), {"A": 8.0, "B": -2.5})
            with open(p, "w") as f:
                f.write("not json")
            self.assertEqual(load_offsets(p), {})
            with open(p, "w") as f:
                f.write("[1,2]")
            self.assertEqual(load_offsets(p), {})


if __name__ == "__main__":
    unittest.main()
