"""CPU usage like the Windows Task Manager, shared by the satellite and server agents.

Usage = share of time the CPU was busy, averaged over all cores, from the difference between two samples of the
aggregate `cpu` line of /proc/stat. iowait counts as idle (a task stuck in I/O is not using the CPU), steal counts as
busy. The load average is not a CPU percentage: it passes 100 % and counts tasks stuck in I/O, so it only survives as
an attribute (`parse_loadavg`).
"""

from __future__ import annotations


def parse_proc_stat(text: str) -> tuple[int, int] | None:
    """`(busy, total)` jiffies of the aggregate `cpu ` line, or None if it is missing or unreadable.

    total = user nice system idle iowait irq softirq steal (guest/guest_nice are already inside user/nice);
    idle = idle + iowait.
    """
    for line in text.splitlines():
        parts = line.split()
        if parts and parts[0] == "cpu":
            try:
                fields = [int(x) for x in parts[1:9]]
            except ValueError:
                return None
            if len(fields) < 8:
                return None
            total = sum(fields)
            idle = fields[3] + fields[4]
            return total - idle, total
    return None


class CpuUsage:
    """Remembers the previous sample; `update` returns the usage since then (0-100) or None."""

    def __init__(self) -> None:
        self._prev: tuple[int, int] | None = None

    def update(self, stat_text: str) -> float | None:
        cur = parse_proc_stat(stat_text)
        if cur is None:
            return None  # an unreadable line keeps the previous sample
        prev, self._prev = self._prev, cur
        if prev is None:
            return None
        d_total = cur[1] - prev[1]
        d_busy = cur[0] - prev[0]
        if d_total <= 0 or d_busy < 0:
            return None
        return round(max(0.0, min(100.0, 100 * d_busy / d_total)), 1)


def parse_loadavg(text: str) -> dict | None:
    """`1.36 1.22 1.16 2/240 1234` -> {"load_1m": 1.36, "load_5m": 1.22, "load_15m": 1.16}."""
    try:
        a, b, c = (float(x) for x in text.split()[:3])
    except ValueError:
        return None
    return {"load_1m": a, "load_5m": b, "load_15m": c}
