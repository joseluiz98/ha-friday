"""Contract between the VM's wheels bundle (ota/wheels.py) and the satellite helper (friday-ota-apply).

14/11 rehearsal: the VM wrote members `wheels/<file>.whl` and the helper hands the extraction folder itself to
`pip --find-links`, which does not look into subfolders: every staging failed with "No matching distribution". Both
sides had passed their own tests with fakes; this one runs the real producer and the real extractor.
"""

import importlib.machinery
import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agents" / "server"))
from friday_server_agent.ota import wheels as wh  # noqa: E402

HELPER = ROOT / "agents" / "satellite" / "friday-ota-apply"
WHEELS = ("aiohappyeyeballs-2.7.1-py3-none-any.whl", "numpy-2.5.3-cp313-cp313-manylinux_2_28_aarch64.whl")


def _helper():
    loader = importlib.machinery.SourceFileLoader("friday_ota_apply", str(HELPER))
    spec = importlib.util.spec_from_loader("friday_ota_apply", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _fake_pip_wheel(cmd, **_kw):
    out = cmd[cmd.index("-v") + 1].split(":", 1)[0]  # the first mount is the output folder (/w)
    for name in WHEELS:
        Path(out, name).write_bytes(b"wheel")
    return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")


def test_the_helper_finds_the_vm_wheels_where_pip_looks(tmp_path):
    lock = tmp_path / "requirements.lock"
    lock.write_text("aiohappyeyeballs==2.7.1\n")
    bundle = wh.build([str(lock)], "a" * 64, out_root=str(tmp_path / "cache"), run=_fake_pip_wheel)
    dest = tmp_path / "extracted"
    dest.mkdir()
    _helper().extract(bundle, str(dest))
    # the helper passes `dest` to pip --find-links: the wheels must be right there, not in a subfolder
    assert sorted(os.listdir(dest)) == sorted(WHEELS)
