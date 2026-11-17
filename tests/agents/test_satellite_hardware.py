"""Hardware/network readings, mute and turn-sounds mapping of the satellite agent."""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "satellite"))

from friday_agent_core.cpu import CpuUsage  # noqa: E402
from friday_satellite_agent import capabilities as cap  # noqa: E402
from friday_satellite_agent import hardware as hw  # noqa: E402

NOW = datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc)


def test_cpu_temperature_parse():
    assert hw.parse_cpu_temperature("48312\n") == 48.3
    assert hw.parse_cpu_temperature("garbage") is None


def test_memory_usage_like_windows():
    pct, attrs = hw.parse_memory_usage(FILES[hw.MEMINFO])
    assert pct == 41.5 and attrs == {"available_mb": 531, "total_mb": 907}
    assert hw.parse_memory_usage("") is None


def test_throttled_parse():
    assert hw.parse_throttled("throttled=0x0\n") is False
    assert hw.parse_throttled("throttled=0x50005") is True
    assert hw.parse_throttled("") is None


def test_boot_time_rounded_to_minute():
    assert hw.parse_boot_time("3600.52 100.0", NOW) == "2026-10-29T11:00:00+00:00"
    assert hw.parse_boot_time("3600.52 100.0", NOW) == hw.parse_boot_time("3610.0 1.0", NOW)
    assert hw.parse_boot_time("", NOW) is None


def test_signal_dbm():
    text = "Inter-|\n face |\n wlan0: 0000   70.  -52.  -256   0 0 0 0 0 0\n"
    assert hw.parse_signal_dbm(text, "wlan0") == -52
    assert hw.parse_signal_dbm(text, "wlan1") is None


def test_signal_nonsense_is_rejected_and_falls_back_to_percent():
    bad = " wlan0: 0000 70. -11. -256 0 0 0 0 0 0"
    assert hw.parse_signal_dbm(bad, "wlan0") is None
    assert hw.dbm_from_percent("60") == -70 and hw.dbm_from_percent("x") is None


FILES = {hw.THERMAL: "51000", hw.UPTIME: "3600.0 1.0", hw.LOADAVG: "2.00 1.50 1.00 3/250 999",
         hw.WIRELESS: " wlan0: 0000 70. -48. -256 0 0 0 0 0 0",
         hw.MEMINFO: "MemTotal:         929456 kB\nMemFree:  100 kB\nMemAvailable:     543744 kB\n"}
ST = type("St", (), {"f_blocks": 1000, "f_bfree": 550, "f_bavail": 500, "f_frsize": 4096})()
LEGACY = {"undervoltage_now": False, "undervoltage_since_boot": False, "heat_limited": False, "cpu_clock": 1200,
          "sd_usage": 47}


def fake_run(devices):
    def run(argv):
        if argv == ["vcgencmd", "measure_clock", "arm"]:
            return "frequency(48)=1200000000"
        if argv[0] == "vcgencmd":
            return "throttled=0x0"
        if argv[:4] == ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE"]:
            return devices
        if "IP4.ADDRESS" in argv:
            return "192.0.2.15/24\n"
        if "GENERAL.HWADDR" in argv:
            return "B8\\:27\\:EB\\:00\\:00\\:01\n"
        if argv[:4] == ["nmcli", "-t", "-f", "ACTIVE,SIGNAL"]:
            return "yes:80\n"
        if argv[:4] == ["nmcli", "-t", "-f", "ACTIVE,SSID"]:
            return "no:Other\nyes:ExampleNet\n"
        raise AssertionError(argv)
    return run


def test_sample_wifi_only():
    out = hw.sample(read=FILES.__getitem__, run=fake_run("eth0:ethernet:unavailable\nwlan0:wifi:connected\n"),
                    now=lambda: NOW, statvfs=lambda: ST)
    assert out == {"cpu_temperature": 51.0, "throttled": False, "memory_usage": 41.5,
                   "memory_usage_attributes": {"available_mb": 531, "total_mb": 907},
                   "uptime_since": "2026-10-29T11:00:00+00:00", **LEGACY,
                   "network_interface": "wifi", "ip_address": "192.0.2.15", "wifi_ip_address": "192.0.2.15",
                   "mac_address": "B8:27:EB:00:00:01", "wifi_ssid": "ExampleNet", "wifi_signal": -48}


def test_sample_cpu_usage_needs_two_samples():
    files = dict(FILES, **{hw.STAT: "cpu  100 0 100 700 100 0 0 0 0 0\n"})
    u = CpuUsage()
    first = hw.sample(read=files.__getitem__, run=fake_run(""), now=lambda: NOW, cpu=u, with_network=False)
    assert "cpu_usage" not in first and "cpu_usage_attributes" not in first
    files[hw.STAT] = "cpu  150 0 150 750 150 0 0 0 0 0\n"
    second = hw.sample(read=files.__getitem__, run=fake_run(""), now=lambda: NOW, cpu=u, with_network=False)
    assert second["cpu_usage"] == 50.0
    assert second["cpu_usage_attributes"] == {"load_1m": 2.0, "load_5m": 1.5, "load_15m": 1.0}


def test_sample_signal_falls_back_to_nmcli_percent():
    files = dict(FILES, **{hw.WIRELESS: " wlan0: 0000 70. -11. -256 0 0 0 0 0 0"})
    out = hw.sample(read=files.__getitem__, run=fake_run("wlan0:wifi:connected\n"), now=lambda: NOW)
    assert out["wifi_signal"] == -60


def test_sample_ethernet_wins_and_has_no_wifi_keys():
    out = hw.sample(read=FILES.__getitem__, run=fake_run("eth0:ethernet:connected\nwlan0:wifi:connected\n"),
                    now=lambda: NOW)
    assert out["network_interface"] == "ethernet" and "wifi_ssid" not in out and "wifi_signal" not in out
    assert out["wifi_ip_address"] == "192.0.2.15"  # the doorbell's fallback when the cable does not answer


def test_throttle_flags_like_the_old_status_page():
    assert hw.parse_throttle_flags("throttled=0x50005") == {
        "undervoltage_now": True, "undervoltage_since_boot": True, "heat_limited": True}
    assert hw.parse_throttle_flags("throttled=0x10000") == {
        "undervoltage_now": False, "undervoltage_since_boot": True, "heat_limited": False}
    assert hw.parse_throttle_flags("throttled=0x2")["heat_limited"] is True
    assert hw.parse_throttle_flags("nonsense") == {}


def test_clock_memory_and_disk_parse():
    assert hw.parse_clock_mhz("frequency(48)=600117000") == 600 and hw.parse_clock_mhz("x") is None
    assert hw.parse_meminfo_mb(FILES[hw.MEMINFO], "MemAvailable") == 531
    assert hw.parse_meminfo_mb("", "MemAvailable") is None
    assert hw.disk_usage_pct(ST) == 47


def test_sample_survives_missing_sources():
    def read(path):
        raise OSError(path)

    def run(argv):
        raise FileNotFoundError(argv[0])

    try:
        hw.sample(read=read, run=run, now=lambda: NOW)
    except FileNotFoundError:
        pass  # nmcli missing is handled by with_network=False
    def statvfs():
        raise OSError("/")

    assert hw.sample(read=read, run=run, now=lambda: NOW, with_network=False, statvfs=statvfs) == {}


def test_descriptors_network_only_with_nmcli():
    keys = {d["key"] for d in hw.descriptors(network=False)}
    assert keys == {"cpu_temperature", "cpu_usage", "throttled", "uptime_since", "undervoltage_now",
                    "undervoltage_since_boot", "heat_limited", "cpu_clock", "memory_usage", "sd_usage"}
    d = {x["key"]: x for x in hw.descriptors(network=True)}
    assert d["throttled"]["platform"] == "binary_sensor" and d["throttled"]["device_class"] == "problem"
    assert d["wifi_signal"]["device_class"] == "signal_strength" and d["wifi_signal"]["unit"] == "dBm"
    assert d["mac_address"]["category"] == "diagnostic"
    assert d["wifi_set"]["hidden"] is True
    assert d["wifi_change"]["options"] == ["idle", "running", "ok", "rolled_back"]


def test_mute_and_turn_sounds_mapping():
    m = cap.Mapper()
    st = m.state({"mudo": True, "sons_turno": False}, [], None)
    assert st["mute"] is True and st["turn_sounds"] is False
    default = m.state({}, [], None)  # older program config: today's behaviour
    assert default["mute"] is False and default["turn_sounds"] is True
    assert m.to_program({}, "mute", True) == {"config": {"mudo": True}}
    assert m.to_program({}, "turn_sounds", False) == {"config": {"sons_turno": False}}
    caps = {d["key"]: d for d in cap.controls_from_config({}, [])}
    assert caps["mute"]["platform"] == "switch" and caps["mute"].get("category") is None
    assert caps["turn_sounds"]["platform"] == "switch" and caps["turn_sounds"]["category"] == "config"


def test_wifi_ssid_is_diagnostic_and_disabled_by_default():
    d = {x["key"]: x for x in hw.descriptors(network=True)}
    assert d["wifi_ssid"]["category"] == "diagnostic" and d["wifi_ssid"]["enabled_default"] is False
    assert d["wifi_signal"].get("enabled_default", True) is True


def test_hardware_descriptors_names_and_categories():
    d = {x["key"]: x for x in hw.descriptors(network=True)}
    assert d["cpu_usage"]["name"] == "CPU Usage" and d["cpu_usage"]["unit"] == "%"
    assert d["cpu_usage"]["state_class"] == "measurement"
    assert d["memory_usage"]["name"] == "Memory Usage" and d["memory_usage"]["unit"] == "%"
    assert not {"cpu_load", "memory_available", "memory_total"} & set(d)
    shown = [x for x in d.values() if not x.get("hidden")]  # the hidden Wi-Fi button is a command, not a sensor
    assert all(x.get("category") in ("config", "diagnostic") for x in shown)
