"""Pure logic of the reminder event (which reminders rang)."""

from datetime import timedelta

from homeassistant.util import dt as dt_util

from custom_components.friday.reminders import rung


def test_rung_only_reminders_gone_at_their_time():
    now = dt_util.utcnow()
    due = {"name": "a", "at": (now - timedelta(seconds=30)).isoformat()}
    later = {"name": "b", "at": (now + timedelta(hours=2)).isoformat()}
    assert rung([due, later], [later], now) == [due]
    # cancelled long before its time: not rung
    assert rung([due, later], [due], now) == []
    # recurring reminder moved to its next time: rung
    moved = {"name": "a", "at": (now + timedelta(days=1)).isoformat()}
    assert rung([due], [moved], now) == [due]
