import unittest
from datetime import date, datetime, timezone

import ledger
import pricing

P = pricing.load_prices()
M = 1_000_000


def r(model, day=(2026, 10, 23), **tok):
    return ledger.make_record(datetime(*day, 15, tzinfo=timezone.utc), "x", "brain", "o", model, None, None, tok)


class PricingTest(unittest.TestCase):
    def test_flash_price_doubles_2027(self):
        rec = r("gemini-3.8-flash", input_text=M)
        self.assertAlmostEqual(pricing.list_cost_usd(rec, P, date(2026, 12, 31)), 0.75)
        self.assertAlmostEqual(pricing.list_cost_usd(rec, P, date(2027, 1, 1)), 1.50)

    def test_live_audio_and_text(self):
        rec = r("gemini-3.8-live", input_audio=M, output_audio=M, output_text=M)
        self.assertAlmostEqual(pricing.list_cost_usd(rec, P), 3 + 12 + 4.5)

    def test_cache_falls_back_to_input_price(self):
        self.assertAlmostEqual(pricing.list_cost_usd(r("gemini-3.8-live", cache_read_audio=M), P), 3.00)
        self.assertAlmostEqual(pricing.list_cost_usd(r("gemini-3.8-flash", cache_read_text=M), P), 0.075)

    def test_reasoning_priced_as_output(self):
        self.assertAlmostEqual(pricing.list_cost_usd(r("gemini-3.8-flash", reasoning=M), P), 3.75)

    def test_unknown_model_returns_none(self):
        self.assertIsNone(pricing.list_cost_usd(r("gemini-9", input_text=5), P))
        self.assertIsNone(pricing.price_for(P, "gemini-3.8-flash", date(2025, 1, 1)))
        self.assertIsNotNone(pricing.price_for(P, "models/gemini-3.8-flash", date(2026, 5, 1)))

    def test_audio_on_text_only_model_returns_none(self):
        self.assertIsNone(pricing.list_cost_usd(r("gemini-3.8-flash", input_audio=10), P))


if __name__ == "__main__":
    unittest.main()
