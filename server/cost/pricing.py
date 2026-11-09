"""List-price cost in USD from token records. Prices in prices.json (USD per 1M tokens)."""
import json
from datetime import date, datetime
from pathlib import Path

from ledger import BRT

PRICES_PATH = Path(__file__).with_name("prices.json")


def load_prices(path=PRICES_PATH):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def price_for(prices, model, day):
    model = model.removeprefix("models/")
    best = None
    for e in prices.get(model, []):
        if date.fromisoformat(e["effective_from"]) <= day:
            if best is None or e["effective_from"] > best["effective_from"]:
                best = e
    return best


def list_cost_usd(record, prices, day=None):
    if day is None:
        day = datetime.fromisoformat(record["ts"]).astimezone(BRT).date()
    p = price_for(prices, record["model"], day)
    if p is None:
        return None
    t = record["tokens"]
    cache = p.get("cache_read")
    mapping = (
        ("input_text", p.get("input_text")),
        ("input_audio", p.get("input_audio")),
        ("cache_read_text", cache if cache is not None else p.get("input_text")),
        ("cache_read_audio", cache if cache is not None else p.get("input_audio")),
        ("output_text", p.get("output_text")),
        ("output_audio", p.get("output_audio")),
        ("reasoning", p.get("output_text")),
    )
    total = 0.0
    for field, price in mapping:
        n = t.get(field, 0)
        if n > 0:
            if price is None:
                return None
            total += n * price / 1_000_000
    return total
