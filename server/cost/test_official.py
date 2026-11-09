import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import ledger
import official
import pricing

P = pricing.load_prices()
M = 1_000_000
SA = {"client_email": "svc@x.iam", "private_key": "-----BEGIN PRIVATE KEY-----\nSECRETKEYBODY\n-----END PRIVATE KEY-----\n",
      "token_uri": "https://oauth2.googleapis.com/token"}


class Seq:
    """Fake http returning queued responses; records calls."""
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, method, url, headers=None, body=None):
        self.calls.append((method, url))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def rows_resp(rows, complete=True):
    return {"jobComplete": complete, "jobReference": {"jobId": "j1", "location": "US"},
            "schema": {"fields": [{"name": "day", "type": "STRING"}, {"name": "sku_id", "type": "STRING"},
                                  {"name": "sku", "type": "STRING"}, {"name": "cost_type", "type": "STRING"},
                                  {"name": "currency", "type": "STRING"}, {"name": "cost", "type": "FLOAT"},
                                  {"name": "credits", "type": "FLOAT"}, {"name": "usage_amount", "type": "FLOAT"},
                                  {"name": "usage_unit", "type": "STRING"}]},
            "rows": [{"f": [{"v": str(x)} for x in r]} for r in rows]}


def write_official(state, month, days):
    d = Path(state) / "official"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{month}.json").write_text(json.dumps({"pulled_at": "x", "days": days}))


def row(sku_id, cost, credits=0.0):
    return {"sku_id": sku_id, "sku": "s", "cost_type": "regular", "currency": "BRL", "cost": cost,
            "credits": credits, "usage_amount": 1.0, "usage_unit": "u"}


class OfficialTest(unittest.TestCase):
    def test_access_token_signs_with_openssl_and_deletes_key(self):
        seen = {}

        def run(cmd, **kw):
            seen["cmd"] = cmd
            path = cmd[cmd.index("-sign") + 1]
            seen["existed"] = os.path.exists(path)
            seen["content"] = Path(path).read_text()
            seen["mode"] = oct(os.stat(path).st_mode & 0o777)
            seen["path"] = path
            return SimpleNamespace(stdout=b"sig", returncode=0)

        http = Seq({"access_token": "TOK"})
        self.assertEqual(official.access_token(SA, run=run, http=http), "TOK")
        self.assertEqual(seen["cmd"][:4], ["openssl", "dgst", "-sha256", "-sign"])
        self.assertTrue(seen["existed"])
        self.assertEqual(seen["content"], SA["private_key"])
        self.assertEqual(seen["mode"], "0o600")
        self.assertFalse(os.path.exists(seen["path"]))

    def test_query_polls_until_complete(self):
        http = Seq({"jobComplete": False, "jobReference": {"jobId": "j1", "location": "US"}},
                   rows_resp([["2026-10-23", "A", "sku", "regular", "BRL", 10, 0, 1, "u"]]))
        out = official.run_query("proj", "select 1", "TOK", http, sleep=lambda s: None)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["cost"], 10.0)
        self.assertEqual(out[0]["sku_id"], "A")
        self.assertEqual(http.calls[0][0], "POST")
        self.assertEqual(http.calls[1][0], "GET")

    def test_pull_without_config_is_noop(self):
        with tempfile.TemporaryDirectory() as d:
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(official.main(state_dir=Path(d)), 0)
            self.assertIn("BigQuery não configurado", buf.getvalue())
            self.assertFalse((Path(d) / "official").exists())

    def test_pull_writes_days_and_keeps_others(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            write_official(d, "2026-10", {"2026-10-01": [row("A", 1.0)], "2026-10-22": [row("A", 99.0)]})
            (d / "gcp.json").write_text(json.dumps({"project": "p", "dataset": "ds"}))
            (d / "gcp-service-account.json").write_text(json.dumps(SA))
            http = Seq({"access_token": "TOK"},
                       {"tables": [{"tableReference": {"tableId": "gcp_billing_export_v1_X"}}]},
                       rows_resp([["2026-10-22", "A", "sku", "regular", "BRL", 5, 0, 1, "u"],
                                  ["2026-10-23", "A", "sku", "regular", "BRL", 6, 0, 1, "u"]]))
            run = lambda cmd, **kw: SimpleNamespace(stdout=b"sig", returncode=0)
            n = official.pull(state_dir=d, http=http, run=run, sleep=lambda s: None)
            self.assertEqual(n, 2)
            data = json.loads((d / "official" / "2026-10.json").read_text())
            self.assertEqual(sorted(data["days"]), ["2026-10-01", "2026-10-22", "2026-10-23"])
            self.assertEqual(data["days"]["2026-10-22"][0]["cost"], 5.0)
            self.assertIn("pulled_at", data)

    def test_official_by_day_nets_credits_and_maps_skus(self):
        with tempfile.TemporaryDirectory() as d:
            write_official(d, "2026-10", {"2026-10-10": [row("F", 10.0, -2.0), row("Z", 3.0), row("Q", 1.0)]})
            sku_map = {"F": "gemini-3.8-flash", "Z": "unmetered"}
            res = official.official_by_day(date(2026, 10, 10), date(2026, 10, 10), Path(d), sku_map=sku_map,
                                           today=date(2026, 10, 24))
            r = res[date(2026, 10, 10)]
            self.assertAlmostEqual(r["by_model"]["gemini-3.8-flash"], 8.0)
            self.assertAlmostEqual(r["by_model"]["unmetered"], 3.0)
            self.assertAlmostEqual(r["by_model"]["unmapped"], 1.0)
            self.assertAlmostEqual(r["total_brl"], 12.0)
            self.assertTrue(r["closed"])
            res = official.official_by_day(date(2026, 10, 10), date(2026, 10, 10), Path(d), sku_map=sku_map,
                                           today=date(2026, 10, 12))
            self.assertFalse(res[date(2026, 10, 10)]["closed"])

    def test_month_factor_fallbacks(self):
        def rec(day, id):
            return ledger.make_record(datetime(2026, 10, day, 15, tzinfo=timezone.utc), id, "brain", "o",
                                      "gemini-3.8-flash", None, None, {"input_text": M})
        records = [rec(10, "a")]  # list cost 0.75 USD
        official_ = {date(2026, 10, 10): {"total_brl": 6.0, "by_model": {"gemini-3.8-flash": 6.0}, "closed": True}}
        f, label = official.month_factor(date(2026, 10, 1), records, P, official_, today=date(2026, 10, 24))
        self.assertAlmostEqual(f, 8.0)
        self.assertIn("outubro", label.lower() + "outubro")  # label is non-empty text
        # no closed day with usage in November -> previous month
        f, label = official.month_factor(date(2026, 11, 1), records, P, official_, today=date(2026, 11, 2))
        self.assertAlmostEqual(f, 8.0)
        self.assertEqual(label, "mês anterior")
        # nothing at all
        f, label = official.month_factor(date(2026, 10, 1), [], P, {}, today=date(2026, 10, 24))
        self.assertEqual((f, label), (7.4, "padrão 7,4"))

    def test_error_messages_mask_secrets(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "gcp.json").write_text(json.dumps({"project": "p", "dataset": "ds"}))
            (d / "gcp-service-account.json").write_text(json.dumps(SA))
            boom = RuntimeError("fail " + SA["private_key"] + " tok=TOKENVALUE123 SECRETKEYBODY")
            http = Seq({"access_token": "TOKENVALUE123"}, boom)
            run = lambda cmd, **kw: SimpleNamespace(stdout=b"sig", returncode=0)
            err = io.StringIO()
            with redirect_stderr(err):
                rc = official.main(state_dir=d, http=http, run=run, sleep=lambda s: None)
            self.assertEqual(rc, 1)
            text = err.getvalue()
            self.assertNotIn("SECRETKEYBODY", text)
            self.assertNotIn("TOKENVALUE123", text)
            self.assertIn("***", text)


if __name__ == "__main__":
    unittest.main()
