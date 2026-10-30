"""Satellite agent startup guards."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "satellite"))


@pytest.mark.parametrize("password", ["", "   "])
async def test_satellite_agent_refuses_empty_local_password(tmp_path, monkeypatch, password):
    from friday_satellite_agent import __main__ as m
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"senha_porta_local": password, "porta_local": 8700}))
    monkeypatch.setattr(m, "CONFIG", str(cfg))
    with pytest.raises(SystemExit):
        await m.main()
