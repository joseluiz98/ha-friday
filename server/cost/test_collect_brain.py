import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

import zstandard

import collect_brain
import ledger

TS_MS = 1792846806549  # 2026-10-24T13:00:06Z


def make_db(path, events):
    c = sqlite3.connect(path)
    c.execute("create table session_windows(session_id text primary key, session_key text)")
    c.execute("create table transcript_events(session_id text, seq integer, event_json text, event_zstd blob)")
    c.execute("insert into session_windows values('s1','agent:friday:main')")
    for i, (ev, z) in enumerate(events):
        s = json.dumps(ev)
        if z:
            c.execute("insert into transcript_events values('s1',?,NULL,?)", (i, zstandard.ZstdCompressor().compress(s.encode())))
        else:
            c.execute("insert into transcript_events values('s1',?,?,NULL)", (i, s))
    c.commit()
    c.close()


def ev(id, provider="google", **usage):
    u = {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}
    u.update(usage)
    return {"type": "message", "id": id, "message": {"role": "assistant", "provider": provider,
            "model": "gemini-3.8-flash", "timestamp": TS_MS, "usage": u}}


class CollectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "agents" / "friday" / "agent").mkdir(parents=True)
        self.db = str(self.dir / "agents" / "friday" / "agent" / "openclaw-agent.sqlite")
        self.glob = str(self.dir / "agents" / "*" / "agent" / "openclaw-agent.sqlite")
        self.state = self.dir / "state"

    def tearDown(self):
        self.tmp.cleanup()

    def read(self):
        return ledger.read(date(2026, 10, 24), date(2026, 10, 24), self.state)

    def test_classify_examples(self):
        c = collect_brain.classify
        self.assertEqual(c("agent:friday:openai-user:voz-1791-22"), "voice_request")
        self.assertEqual(c("agent:friday:openai-user:voz-medicao-r1-0"), "test")
        self.assertEqual(c("agent:writer:cron:abc:run:def"), "automation")
        self.assertEqual(c("agent:friday:main"), "main_chat")
        self.assertEqual(c("agent:friday:ios-abc"), "app")
        self.assertEqual(c("agent:friday:explicit"), "other")
        self.assertEqual(c("agent:friday:openai-user:diag-1"), "test")
        self.assertEqual(c("agent:friday:teste-x"), "test")

    def test_collect_maps_usage_and_skips_non_google(self):
        make_db(self.db, [(ev("e1", input=3439, output=180, cacheRead=63195), False),
                          (ev("e2", provider="openclaw", input=5), False)])
        collect_brain.collect(self.glob, self.state)
        recs, _ = self.read()
        self.assertEqual(len(recs), 1)
        t = recs[0]["tokens"]
        self.assertEqual((t["input_text"], t["cache_read_text"], t["output_text"]), (3439, 63195, 180))
        self.assertEqual(recs[0]["id"], "brain:friday:e1")
        self.assertEqual(recs[0]["origin"], "main_chat")

    def test_collect_twice_no_duplicates(self):
        make_db(self.db, [(ev("e1", input=1), False)])
        self.assertEqual(collect_brain.collect(self.glob, self.state), {"friday": 1})
        self.assertEqual(collect_brain.collect(self.glob, self.state), {"friday": 0})
        self.assertEqual(len(self.read()[0]), 1)

    def test_collect_reads_zstd_event(self):
        make_db(self.db, [(ev("z1", input=7), True)])
        collect_brain.collect(self.glob, self.state)
        self.assertEqual(self.read()[0][0]["tokens"]["input_text"], 7)

    def test_unopenable_db_does_not_stop_others(self):
        make_db(self.db, [(ev("e1", input=1), False)])
        bad = self.dir / "agents" / "bad" / "agent"
        bad.mkdir(parents=True)
        (bad / "openclaw-agent.sqlite").write_text("not a database")
        res = collect_brain.collect(self.glob, self.state)
        self.assertEqual(res["friday"], 1)


if __name__ == "__main__":
    unittest.main()
