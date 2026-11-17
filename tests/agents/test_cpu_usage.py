"""CPU usage from /proc/stat deltas, shared by the satellite and server agents."""

from friday_agent_core import cpu

A = "cpu  100 0 100 700 100 0 0 0 0 0\ncpu0 1 2 3 4 5 6 7 8 9 10\n"
B = "cpu  150 0 150 750 150 0 0 0 0 0\n"


def test_parse_proc_stat():
    assert cpu.parse_proc_stat(A) == (200, 1000)
    assert cpu.parse_proc_stat("x") is None


def test_first_sample_is_none_then_delta():
    u = cpu.CpuUsage()
    assert u.update(A) is None
    assert u.update(B) == 50.0


def test_iowait_counts_as_idle():
    u = cpu.CpuUsage()
    u.update(A)
    assert u.update("cpu  100 0 100 700 300 0 0 0 0 0\n") == 0.0


def test_guest_not_double_counted():  # guest/guest_nice fields ignored
    u = cpu.CpuUsage()
    u.update(A)
    assert u.update("cpu  150 0 150 750 150 0 0 0 999 999\n") == 50.0


def test_stalled_or_backwards_counters_are_none():
    u = cpu.CpuUsage()
    u.update(B)
    assert u.update(B) is None
    assert u.update(A) is None


def test_unreadable_line_keeps_previous_sample():
    u = cpu.CpuUsage()
    u.update(A)
    assert u.update("garbage") is None
    assert u.update(B) == 50.0


def test_truncated_line_is_none():
    assert cpu.parse_proc_stat("cpu  1 2 3\n") is None


def test_parse_loadavg():
    assert cpu.parse_loadavg("1.36 1.22 1.16 2/240 1234\n") == {"load_1m": 1.36, "load_5m": 1.22, "load_15m": 1.16}
    assert cpu.parse_loadavg("") is None
