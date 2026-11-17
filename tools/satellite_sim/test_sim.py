import http.client
import json
import os
import tempfile
import unittest

import sim

TOKEN = "t" * 40


def line(**kw):
    return json.dumps(kw) + "\n"


class ClaimBodyTest(unittest.TestCase):
    def test_build_claim_body(self):
        body = sim.build_claim_body(voice_dbfs=-20, score=0.9)
        self.assertEqual(set(body), {"claim_id", "wake_word", "score", "voice_dbfs", "busy"})
        self.assertEqual((body["wake_word"], body["score"], body["voice_dbfs"], body["busy"]),
                         ("friday", 0.9, -20.0, False))
        self.assertIsInstance(body["voice_dbfs"], float)
        self.assertTrue(body["claim_id"] and isinstance(body["claim_id"], str))
        self.assertNotEqual(body["claim_id"], sim.build_claim_body(-20, 0.9)["claim_id"])
        self.assertTrue(json.dumps(body))  # serializable

    def test_claim_id_can_be_given(self):
        self.assertEqual(sim.build_claim_body(-1, 0.5, claim_id="abc")["claim_id"], "abc")


class TailTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        self.now = 1_800_000_000.0  # a fixed instant
        self.path = os.path.join(self.dir, sim.day_file_name(self.now))

    def tail(self):
        return sim.RoundLogTail(self.dir, clock=lambda: self.now)

    def append(self, text):
        with open(self.path, "a") as f:
            f.write(text)

    def test_detects_new_round_from_log_growth(self):
        t = self.tail()
        self.assertEqual(t.poll(), [])  # file does not exist yet
        self.append(line(event="round_opened", round_id="r1", sid="sat-aaaaaa", ts=1))
        self.assertEqual([r["round_id"] for r in t.poll()], ["r1"])
        self.assertEqual(t.poll(), [])  # nothing new: nothing returned
        self.append(line(event="verdict", round_id="r1"))  # other events are not rounds
        self.assertEqual(t.poll(), [])
        self.append(line(event="round_opened", round_id="r2", sid="sat-bbbbbb", ts=2))
        self.assertEqual([r["round_id"] for r in t.poll()], ["r2"])

    def test_history_before_start_is_ignored(self):
        self.append(line(event="round_opened", round_id="old", sid="sat-aaaaaa", ts=1))
        t = self.tail()
        self.assertEqual(t.poll(), [])
        self.append(line(event="round_opened", round_id="new", sid="sat-aaaaaa", ts=2))
        self.assertEqual([r["round_id"] for r in t.poll()], ["new"])

    def test_partial_line_waits_for_newline(self):
        t = self.tail()
        full = line(event="round_opened", round_id="r1", sid="sat-aaaaaa", ts=1)
        self.append(full[:20])
        self.assertEqual(t.poll(), [])
        self.append(full[20:])
        self.assertEqual([r["round_id"] for r in t.poll()], ["r1"])

    def test_garbage_lines_are_skipped(self):
        t = self.tail()
        self.append("not json\n[1,2]\n" + line(event="round_opened", round_id="r1", sid="sat-aaaaaa"))
        self.assertEqual([r["round_id"] for r in t.poll()], ["r1"])

    def test_day_rollover_reads_new_file_from_start(self):
        t = self.tail()
        self.append(line(event="round_opened", round_id="r1", sid="sat-aaaaaa"))
        self.assertEqual(len(t.poll()), 1)
        self.now += 86400
        self.path = os.path.join(self.dir, sim.day_file_name(self.now))
        self.append(line(event="round_opened", round_id="r2", sid="sat-aaaaaa"))
        self.assertEqual([r["round_id"] for r in t.poll()], ["r2"])

    def test_day_is_sao_paulo(self):
        # 2026-11-16 02:00 UTC is still 11-15 23:00 in Sao Paulo (UTC-3)
        self.assertEqual(sim.day_file_name(1_794_794_400.0), "2026-11-15.ndjson")


class LoopStepTest(unittest.TestCase):
    def test_claims_only_for_foreign_rounds_once(self):
        sent = []
        sender = lambda path, body: sent.append((path, body)) or (200, {"verdict": "lost", "reason": "x", "round_id": "r"})
        seen = set()
        records = [
            {"event": "round_opened", "round_id": "r1", "sid": "sat-f510a1"},   # our own: ignored
            {"event": "round_opened", "round_id": "r2", "sid": "sat-aaaaaa"},   # foreign: claim
            {"event": "round_opened", "round_id": "r2", "sid": "sat-aaaaaa"},   # duplicate: ignored
        ]
        results = sim.claim_for_foreign_rounds(records, "sat-f510a1", sender, -20.0, 0.9, 0, seen)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], "/wake_claim")
        self.assertEqual(sent[0][1]["voice_dbfs"], -20.0)
        self.assertEqual(results[0]["verdict"], "lost")

    def test_delay_is_applied_before_sending(self):
        order = []
        sender = lambda p, b: order.append("send") or (200, {})
        sim.claim_for_foreign_rounds([{"event": "round_opened", "round_id": "r", "sid": "sat-aaaaaa"}],
                                     "sat-f510a1", sender, -20.0, 0.9, 150, set(),
                                     sleep=lambda s: order.append(("sleep", s)))
        self.assertEqual(order, [("sleep", 0.15), "send"])


class ClientTest(unittest.TestCase):
    def test_read_token_strips_and_validates(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "sim.token")
            with open(p, "w") as f:
                f.write(TOKEN + "\n")
            self.assertEqual(sim.read_token(p), TOKEN)
            with open(p, "w") as f:
                f.write("qZx7Kp\n")
            with self.assertRaises(SystemExit) as cm:
                sim.read_token(p)
            self.assertNotIn("qZx7Kp", str(cm.exception))
            with self.assertRaises(SystemExit):
                sim.read_token(os.path.join(d, "missing"))

    def test_post_error_never_leaks_token(self):
        class Boom(Exception):
            pass

        class Conn:
            def request(self, *a, **k):
                raise Boom(f"Bearer {TOKEN}")

            def close(self):
                pass

        client = sim.GatewayClient("https://127.0.0.1:7443", TOKEN, conn_factory=lambda: Conn())
        status, body = client.post("/wake_claim", {})
        self.assertIsNone(status)
        self.assertNotIn(TOKEN, json.dumps(body))
        self.assertEqual(body["error"], "Boom")

    def test_authorization_header_is_bearer(self):
        seen = {}

        class Resp:
            status = 200

            def read(self):
                return b'{"others_online": 1}'

        class Conn:
            def request(self, method, path, body=None, headers=None):
                seen.update(method=method, path=path, headers=headers)

            def getresponse(self):
                return Resp()

            def close(self):
                pass

        client = sim.GatewayClient("https://127.0.0.1:7443", TOKEN, conn_factory=lambda: Conn())
        self.assertEqual(client.post("/satellite_presence", {}), (200, {"others_online": 1}))
        self.assertEqual(seen["headers"]["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual((seen["method"], seen["path"]), ("POST", "/satellite_presence"))


class _Resp:
    status = 200

    def __init__(self, data=b'{"verdict": "lost", "reason": "r", "round_id": "x"}'):
        self._data = data

    def read(self):
        return self._data


class _ScriptedConn:
    """Replies per request: an exception instance is raised, anything else answers 200."""

    def __init__(self, script, log):
        self.script, self.log = list(script), log

    def request(self, *a, **k):
        self.log.append("request")
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step

    def getresponse(self):
        return _Resp()

    def close(self):
        pass


def _factory(per_connection_scripts, log):
    scripts = list(per_connection_scripts)

    def make():
        log.append("connect")
        return _ScriptedConn(scripts.pop(0), log)
    return make


class RetryTest(unittest.TestCase):
    def test_reused_connection_failure_is_retried_once_on_a_fresh_one(self):
        log = []
        # 1st connection: first call ok, then the server closed it while idle; 2nd connection works.
        client = sim.GatewayClient("https://x:1", TOKEN, conn_factory=_factory(
            [["ok", http.client.RemoteDisconnected("Bearer " + TOKEN)], ["ok"]], log))
        self.assertEqual(client.post("/satellite_presence", {})[0], 200)
        status, body = client.post("/wake_claim", {})
        self.assertEqual((status, body["verdict"]), (200, "lost"))
        self.assertEqual(log.count("connect"), 2)

    def test_fresh_connection_failure_is_not_retried(self):
        log = []
        client = sim.GatewayClient("https://x:1", TOKEN, conn_factory=_factory(
            [[ConnectionResetError(TOKEN)], ["ok"]], log))
        status, body = client.post("/wake_claim", {})
        self.assertIsNone(status)
        self.assertEqual(body, {"error": "ConnectionResetError"})
        self.assertEqual((log.count("connect"), log.count("request")), (1, 1))

    def test_reused_connection_failing_twice_gives_error_without_token(self):
        log = []
        client = sim.GatewayClient("https://x:1", TOKEN, conn_factory=_factory(
            [["ok", BrokenPipeError(TOKEN)], [BrokenPipeError(TOKEN)]], log))
        client.post("/satellite_presence", {})
        status, body = client.post("/wake_claim", {})
        self.assertEqual((status, body), (None, {"error": "BrokenPipeError"}))
        self.assertEqual(log.count("connect"), 2)  # one retry only

    def test_non_connection_error_on_reused_connection_is_not_retried(self):
        log = []
        client = sim.GatewayClient("https://x:1", TOKEN, conn_factory=_factory(
            [["ok", TimeoutError()], ["ok"]], log))
        client.post("/satellite_presence", {})
        self.assertEqual(client.post("/wake_claim", {}), (None, {"error": "TimeoutError"}))
        self.assertEqual(log.count("connect"), 1)

    def test_round_opened_without_round_id_is_skipped(self):
        sent, seen = [], set()
        sender = lambda p, b: sent.append(b) or (200, {})
        recs = [{"event": "round_opened", "sid": "sat-aaaaaa"},
                {"event": "round_opened", "round_id": "", "sid": "sat-aaaaaa"}]
        self.assertEqual(sim.claim_for_foreign_rounds(recs, "sat-f510a1", sender, -20.0, 0.9, 0, seen), [])
        self.assertEqual((sent, seen), ([], set()))


if __name__ == "__main__":
    unittest.main()
