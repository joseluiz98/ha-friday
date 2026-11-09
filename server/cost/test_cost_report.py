import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

import cost_report as cr
import ledger

M = 1_000_000
SKU = {"F": "gemini-3.8-flash", "L": "gemini-3.8-live", "Z": "unmetered"}


def rec(day, id, source="brain", origin="main_chat", model="gemini-3.8-flash", **tok):
    ts = datetime(day.year, day.month, day.day, 15, tzinfo=timezone.utc)
    return ledger.make_record(ts, id, source, origin, model, "s", None, tok)


class ReportTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state = Path(self._tmp.name)

    def go(self, argv, today):
        return cr.run(argv, state_dir=self.state, today=today, sku_map=SKU)

    def official(self, month, days):
        d = self.state / "official"
        d.mkdir(exist_ok=True)
        (d / f"{month}.json").write_text(json.dumps({"pulled_at": "2026-10-24T10:00:00+00:00", "days": days}))

    def test_brl_format(self):
        self.assertEqual(cr.brl(1234.5), "R$ 1.234,50")
        self.assertEqual(cr.brl(0.5), "R$ 0,50")

    def test_day_ranks_origins(self):
        d = date(2026, 10, 23)
        ledger.append([rec(d, "a", origin="main_chat", input_text=M),
                       rec(d, "b", origin="automation", input_text=5 * M),
                       rec(d, "c", source="voice", origin="voice", model="gemini-3.8-live", input_audio=M)], self.state)
        out = self.go(["day", "2026-10-23"], date(2026, 10, 24))
        i = out.index("Por origem")
        sec = out[i:]
        self.assertLess(sec.index("automation"), sec.index("main_chat"))
        self.assertIn("estimado", out)

    def test_month_uses_official_for_closed_days(self):
        d = date(2026, 10, 10)
        ledger.append([rec(d, "a", input_text=M)], self.state)
        self.official("2026-10", {"2026-10-10": [{"sku_id": "F", "cost": 100.0, "credits": 0.0}]})
        out = self.go(["month", "2026-10"], date(2026, 10, 24))
        self.assertIn("R$ 100,00", out)
        self.assertIn("1 dia(s) pelo oficial", out)
        self.assertIn("dia(s) pelo estimado", out)

    def test_projection_applies_2027_price(self):
        recs = [rec(date(2026, 12, n), f"r{n}", input_text=M) for n in range(1, 31)]
        ledger.append(recs, self.state)
        out = self.go(["projection"], date(2026, 12, 31))
        # 1M tokens/day at USD 1.50 (2027 price) * 31 days * 7.4
        self.assertIn(cr.brl(1.5 * 31 * 7.4), out)
        self.assertIn("padrão 7,4", out)

    def test_projection_averages_each_model_over_its_own_coverage(self):
        # voice metered only since 19/10: 5 days of 1M input_audio, not diluted over 30
        recs = [rec(date(2026, 10, n), f"v{n}", source="voice", origin="voice", model="gemini-3.8-live", input_audio=M)
                for n in range(19, 24)]
        ledger.append(recs, self.state)
        out = self.go(["projection"], date(2026, 10, 24))
        self.assertIn(cr.brl(3.0 * 30 * 7.4), out)
        self.assertIn("desde 19/10", out)

    def test_unpriced_model_listed_not_zero(self):
        d = date(2026, 10, 23)
        ledger.append([rec(d, "a", model="gemini-9", input_text=1234)], self.state)
        out = self.go(["day", "2026-10-23"], date(2026, 10, 24))
        self.assertIn("sem preço", out)
        self.assertIn("gemini-9", out)
        self.assertIn("1.234", out)

    def test_range_filters_by_origin(self):
        d = date(2026, 10, 23)
        ledger.append([rec(d, "a", origin="main_chat", input_text=M), rec(d, "b", origin="automation", input_text=M)], self.state)
        out = self.go(["range", "2026-10-23", "2026-10-23", "--origin", "automation"], date(2026, 10, 24))
        self.assertIn("1 chamada", out)

    def test_footer_reports_skipped_lines(self):
        d = date(2026, 10, 23)
        ledger.append([rec(d, "a", input_text=M)], self.state)
        with open(self.state / "usage" / "2026-10-23.ndjson", "a") as fh:
            fh.write("{broken\n")
        out = self.go(["day", "2026-10-23"], date(2026, 10, 24))
        self.assertIn("1 linha(s) inválida(s)", out)


if __name__ == "__main__":
    unittest.main()
