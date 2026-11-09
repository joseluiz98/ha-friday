import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import usage_ledger as ul


class UsageLedgerTest(unittest.TestCase):
    def test_voice_tokens_gross_split(self):
        t = ul.voice_tokens(20751, 118, 0, 177, 620, 100, 0)
        self.assertEqual(t["input_text"], 20131)
        self.assertEqual(t["input_audio"], 620)
        self.assertEqual(t["output_text"], 18)
        self.assertEqual(t["output_audio"], 100)
        self.assertEqual(t["reasoning"], 177)
        self.assertEqual(t["cache_read_text"], 0)

    def test_voice_tokens_with_cache_split(self):
        t = ul.voice_tokens(1000, 10, 400, None, 100, None, 50)
        self.assertEqual((t["cache_read_audio"], t["cache_read_text"], t["input_audio"], t["input_text"]),
                         (50, 350, 50, 550))

    def test_voice_tokens_never_negative(self):
        t = ul.voice_tokens(100, 5, 50, None, 300, 20, 80)
        self.assertTrue(all(v >= 0 for v in t.values()), t)
        self.assertEqual(set(t), set(ul.TOKEN_FIELDS))

    def test_append_voice_writes_line_and_never_raises(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            ts = datetime(2026, 10, 23, 15, tzinfo=timezone.utc)
            ul.append_voice(ts, "s1", 3, "sat-1", "models/gemini-3.8-live", ul.voice_tokens(10, 1, 0, 0, 0, 0, 0), d)
            lines = (d / "usage" / "2026-10-23.ndjson").read_text().splitlines()
            self.assertEqual(len(lines), 1)
            r = json.loads(lines[0])
            self.assertEqual((r["id"], r["source"], r["origin"], r["satellite"]), ("voice:s1:3", "voice", "voice", "sat-1"))
            blocker = d / "blocked"
            blocker.write_text("x")
            ul.append_voice(ts, "s1", 4, None, "m", {}, blocker)  # must not raise


if __name__ == "__main__":
    unittest.main()
