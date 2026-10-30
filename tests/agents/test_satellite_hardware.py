"""Hardware/network readings, mute and turn-sounds mapping of the satellite agent."""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "satellite"))

from friday_satellite_agent import capabilities as cap  # noqa: E402
from friday_satellite_agent import hardware as hw  # noqa: E402

NOW = datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc)


def test_cpu_temperature_parse():
    assert hw.parse_cpu_temperature("48312\n") == 48.3
    assert hw.parse_cpu_temperature("garbage") is None


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


FILES = {hw.THERMAL: "51000", hw.UPTIME: "3600.0 1.0",
         hw.WIRELESS: " wlan0: 0000 70. -48. -256 0 0 0 0 0 0"}


def fake_run(devices):
    def run(argv):
        if argv[0] == "vcgencmd":
            return "throttled=0x0"
        if argv[:4] == ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE"]:
            return devices
        if "IP4.ADDRESS" in argv:
            return "192.0.2.15/24\n"
        if "GENERAL.HWADDR" in argv:
            return "02\\:00\\:00\\:00\\:00\\:00\n"
        if argv[:4] == ["nmcli", "-t", "-f", "ACTIVE,SIGNAL"]:
            return "yes:80\n"
        if argv[:4] == ["nmcli", "-t", "-f", "ACTIVE,SSID"]:
            return "no:Other\nyes:ExampleNet\n"
        raise AssertionError(argv)
    return run


def test_sample_wifi_only():
    out = hw.sample(read=FILES.__getitem__, run=fake_run("eth0:ethernet:unavailable\nwlan0:wifi:connected\n"),
                    now=lambda: NOW)
    assert out == {"cpu_temperature": 51.0, "throttled": False, "uptime_since": "2026-10-29T11:00:00+00:00",
                   "network_interface": "wifi", "ip_address": "192.0.2.15",
                   "mac_address": "02:00:00:00:00:00", "wifi_ssid": "ExampleNet", "wifi_signal": -48}


def test_sample_signal_falls_back_to_nmcli_percent():
    files = dict(FILES, **{hw.WIRELESS: " wlan0: 0000 70. -11. -256 0 0 0 0 0 0"})
    out = hw.sample(read=files.__getitem__, run=fake_run("wlan0:wifi:connected\n"), now=lambda: NOW)
    assert out["wifi_signal"] == -60


def test_sample_ethernet_wins_and_has_no_wifi_keys():
    out = hw.sample(read=FILES.__getitem__, run=fake_run("eth0:ethernet:connected\nwlan0:wifi:connected\n"),
                    now=lambda: NOW)
    assert out["network_interface"] == "ethernet" and "wifi_ssid" not in out and "wifi_signal" not in out


def test_sample_survives_missing_sources():
    def read(path):
        raise OSError(path)

    def run(argv):
        raise FileNotFoundError(argv[0])

    try:
        hw.sample(read=read, run=run, now=lambda: NOW)
    except FileNotFoundError:
        pass  # nmcli missing is handled by with_network=False
    assert hw.sample(read=read, run=run, now=lambda: NOW, with_network=False) == {}


def test_descriptors_network_only_with_nmcli():
    keys = {d["key"] for d in hw.descriptors(network=False)}
    assert keys == {"cpu_temperature", "throttled", "uptime_since"}
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
