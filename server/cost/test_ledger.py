import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

import ledger


def rec(ts, id="a", **tok):
    return ledger.make_record(ts, id, "brain", "main_chat", "gemini-3.8-flash", "s", None, tok)


class LedgerTest(unittest.TestCase):
    def test_append_splits_by_brt_day(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            r1 = rec(datetime(2026, 10, 23, 2, 30, tzinfo=timezone.utc), "a")
            r2 = rec(datetime(2026, 10, 23, 3, 30, tzinfo=timezone.utc), "b")
            self.assertEqual(ledger.append([r1, r2], d), 2)
            self.assertTrue((d / "usage" / "2026-10-22.ndjson").exists())
            self.assertTrue((d / "usage" / "2026-10-23.ndjson").exists())

    def test_make_record_fills_missing_tokens_and_rejects_negative(self):
        r = rec(datetime(2026, 10, 23, tzinfo=timezone.utc), input_text=5)
        self.assertEqual(tuple(r["tokens"]), ledger.TOKEN_FIELDS)
        self.assertEqual(r["tokens"]["input_text"], 5)
        self.assertEqual(sum(r["tokens"].values()), 5)
        with self.assertRaises(ValueError):
            rec(datetime(2026, 10, 23, tzinfo=timezone.utc), output_text=-1)
        with self.assertRaises(ValueError):
            rec(datetime(2026, 10, 23))
        with self.assertRaises(ValueError):
            ledger.make_record(datetime(2026, 10, 23, tzinfo=timezone.utc), "x", "bogus", "o", "m", None, None, {})

    def test_read_dedupes_by_id(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            r = rec(datetime(2026, 10, 23, 12, tzinfo=timezone.utc), "dup")
            ledger.append([r], d)
            ledger.append([r], d)
            recs, skipped = ledger.read(date(2026, 10, 23), date(2026, 10, 23), d)
            self.assertEqual((len(recs), skipped), (1, 0))

    def test_read_skips_corrupt_line(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            r = rec(datetime(2026, 10, 23, 12, tzinfo=timezone.utc), "ok")
            ledger.append([r], d)
            f = d / "usage" / "2026-10-23.ndjson"
            with open(f, "a") as fh:
                fh.write('{"ts": "2026-10-2\n\n')
            recs, skipped = ledger.read(date(2026, 10, 23), date(2026, 10, 23), d)
            self.assertEqual((len(recs), skipped), (1, 1))


if __name__ == "__main__":
    unittest.main()
