"""Entry scan for house data before code enters the public repo (plan 2026-11-09 Task 2)."""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("inventory_scan", ROOT / "scripts" / "inventory_scan.py")
scan_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scan_mod)


def _cats(findings):
    return {(f.category, pathlib.Path(f.path).name, f.line) for f in findings}


def test_finds_value_from_known_list_in_json_and_md(tmp_path):
    (tmp_path / "a.json").write_text('{\n "who": "Zelda"\n}\n')
    (tmp_path / "b.md").write_text("line one\nask Zelda first\n")
    found = _cats(scan_mod.scan([str(tmp_path)], ["Zelda"]))
    assert ("person_name", "a.json", 2) in found
    assert ("person_name", "b.md", 2) in found


def test_finds_private_ipv4_and_mac(tmp_path):
    (tmp_path / "c.py").write_text('HOST = "192.168.77.9"\nMAC = "02:00:5E:AA:BB:CC"\n')
    found = _cats(scan_mod.scan([str(tmp_path)], []))
    assert ("ip_mac", "c.py", 1) in found
    assert ("ip_mac", "c.py", 2) in found


def test_finds_sensitive_value_in_file_name(tmp_path):
    (tmp_path / "zelda_notes.txt").write_text("nothing\n")
    found = scan_mod.scan([str(tmp_path)], ["Zelda"])
    assert any(f.line is None and f.path.endswith("zelda_notes.txt") for f in found)


def test_secret_patterns_match_current_sweep(tmp_path):
    (tmp_path / "s.sh").write_text("curl -H 'Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345'\n"
                                   "T=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.SflKxwRJ\n"
                                   "P=PVEAPIToken=ha@pve!x=uuid\n")
    found = _cats(scan_mod.scan([str(tmp_path)], []))
    assert {("secret", "s.sh", 1), ("secret", "s.sh", 2), ("secret", "s.sh", 3)} <= found


def test_report_masks_excerpts(tmp_path):
    (tmp_path / "a.py").write_text('NAME = "Zelda"\n')
    report = scan_mod.report(scan_mod.scan([str(tmp_path)], ["Zelda"]))
    assert "Zelda" not in report and "Ze" in report


def test_skips_bak_pycache_and_binary(tmp_path):
    (tmp_path / "x.py.bak.20261101").write_text('n = "Zelda"\n')
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "y.pyc").write_text('n = "Zelda"\n')
    (tmp_path / "z.bin").write_bytes(b"\x00\x01Zelda\x00")
    assert scan_mod.scan([str(tmp_path)], ["Zelda"]) == []


def test_long_string_with_known_value_is_house_fact(tmp_path):
    (tmp_path / "p.py").write_text('PROMPT = "Quando a Zelda pedir música na sala, prefira o streaming sempre"\n')
    found = _cats(scan_mod.scan([str(tmp_path)], ["Zelda"]))
    assert ("house_fact_text", "p.py", 1) in found


def test_satellite_id_is_device_id(tmp_path):
    (tmp_path / "t.py").write_text('SAT = "sat-0a1b2c"\n')
    assert ("device_id", "t.py", 1) in _cats(scan_mod.scan([str(tmp_path)], []))
