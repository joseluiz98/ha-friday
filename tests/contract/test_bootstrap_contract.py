"""Frozen contract (spec 2026-11-10 §16; plan Task 16): the v0.2.1 satellite agent accepts the update bundle the
current server builds (the OTA satellite package) and installs the new agent from it."""
import hashlib
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "server"))
from friday_server_agent import provisioning as pv  # noqa: E402
from friday_server_agent.ota import packages as pk  # noqa: E402

from tests.agents.test_ota_packages import _extra_tree  # noqa: E402
from tests.agents.test_server_provisioning import _make_sources  # noqa: E402

FROZEN = Path(__file__).resolve().parent / "v0.2.1" / "agents"

RUNNER = textwrap.dedent('''
    import asyncio, hashlib, json, shutil, sys
    from friday_satellite_agent import provision
    pkg, home = sys.argv[1], sys.argv[2]
    applied = []

    async def fetch(url, dest):
        shutil.copyfile(pkg, dest)
        return hashlib.sha256(open(dest, "rb").read()).hexdigest()

    async def apply_agent(stage):
        import os
        applied.append(sorted(os.listdir(stage)))
        return 0

    async def restart():
        return {"ok": True}

    async def sleep(s):
        return None

    class Program:
        async def command(self, body):
            return {"ok": True}

    async def main():
        p = provision.Provisioner(etc_dir=home + "/etc", config_path=home + "/etc/config.json", ssh_dir=home + "/ssh",
                                  program_dir=home + "/satelite", bundles_dir=home + "/bundles",
                                  voices_dir=home + "/vozes", models_dir=home + "/modelos", program=Program(),
                                  restart_program=restart, apply_agent=apply_agent, fetch=fetch,
                                  exit_fn=lambda c: None, loop_sleep=sleep)
        sha = hashlib.sha256(open(pkg, "rb").read()).hexdigest()
        res = await p.apply_bundle("update", "http://vm:8801/v1/bundles/tok", sha)
        print(json.dumps({"res": res, "agent": applied}))

    asyncio.run(main())
''')


def test_v021_agent_accepts_the_new_update_bundle(tmp_path, monkeypatch):
    cfg = tmp_path / "haos"
    archive, _ = _make_sources(cfg, monkeypatch)
    _extra_tree(cfg)
    info = pk.build_satellite(archive, "v0.3.0", "a" * 40, pv._vault(), str(tmp_path / "pkg"))
    home = tmp_path / "home"
    (home / "satelite").mkdir(parents=True)
    (home / "etc").mkdir()
    env = {**os.environ, "PYTHONPATH": f"{FROZEN / 'satellite'}{os.pathsep}{FROZEN / 'common'}"}
    r = subprocess.run([sys.executable, "-c", RUNNER, info["path"], str(home)], capture_output=True, text=True,
                       env=env, cwd=str(tmp_path), timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["res"]["ok"] is True, out
    assert (home / "satelite" / "friday_satelite.py").exists()  # the program landed where the old layout keeps it
    assert out["agent"] and {"friday_satellite_agent", "friday_agent_core"} <= set(out["agent"][0])
    assert hashlib.sha256(Path(info["path"]).read_bytes()).hexdigest() == info["sha256"]
