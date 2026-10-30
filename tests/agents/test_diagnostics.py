import json
import sys
from pathlib import Path

AG = Path(__file__).resolve().parents[2] / "agents"
for sub in ("common", "satellite", "server"):
    sys.path.insert(0, str(AG / sub))

from friday_agent_core import diagnostics as d
from friday_satellite_agent import diagnostics as sat
from friday_server_agent import diagnostics as srv


def test_redact_text():
    s = ("Authorization: Bearer abc.def-123 x PVEAPIToken=root@pam!t=uuid-1 "
         "psk=wifipass password: hunter2 literal-s3cret end")
    out = d.redact_text(s, ("literal-s3cret",))
    for bad in ("abc.def", "uuid-1", "wifipass", "hunter2", "literal-s3cret"):
        assert bad not in out


def test_redact_data_nested():
    out = d.redact_data({"a": {"Proxmox_Token_Secret": "x", "keep": 1}, "l": [{"senha_x": "y"}]})
    assert out == {"a": {"Proxmox_Token_Secret": d.REDACTED, "keep": 1}, "l": [{"senha_x": d.REDACTED}]}


def test_cap_keeps_tail():
    out = d.cap("a" * 100 + "END", 10)
    assert out.endswith("END") and len(out) < 60


async def test_run_text_timeout_and_missing():
    assert "timeout" in await d.run_text(["sleep", "5"], timeout=0.2)
    assert "unavailable" in await d.run_text(["/nonexistent-bin"])


async def test_satellite_collect(tmp_path):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"senha_porta_local": "hunter2", "porta_local": 8700}))
    calls = []

    async def run(argv, timeout, literals):
        calls.append(argv)
        return d.redact_text("line password=hunter2 tok hunter2", literals)

    out = await sat.collect(str(cfg), {"x": 1}, run)
    assert out["config"]["senha_porta_local"] == d.REDACTED and out["config"]["porta_local"] == 8700
    assert "hunter2" not in json.dumps(out)
    assert [c[2] for c in calls] == ["friday-satelite", "friday-satellite-agent"]
    assert calls[0][4] == "300" and calls[1][4] == "100"


async def test_satellite_missing_config(tmp_path):
    async def run(*a):
        return ""

    assert (await sat.collect(str(tmp_path / "no"), {}, run))["config"] == "unavailable"


async def test_server_collect():
    async def run(argv, timeout, literals):
        return d.redact_text(" ".join(argv) + " Bearer zzz", literals)

    out = await srv.collect({"voice": True, "api_token": "q"}, (), run)
    assert out["state"]["api_token"] == d.REDACTED
    assert "docker logs --tail 300 friday" in out["logs"]["friday"] and "zzz" not in out["logs"]["friday"]
