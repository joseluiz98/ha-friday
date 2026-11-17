"""The unit that serves the satellite images to the Raspberry Pi Imager (plan 2026-11-17-diagnostic-sensors, Task 12)."""

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
UNIT = REPO / "server" / "systemd" / "friday-images-http.service"


def _lines():
    return [ln.strip() for ln in UNIT.read_text().splitlines() if ln.strip() and not ln.startswith("#")]


def _directives():
    out = {}
    for ln in _lines():
        if "=" in ln and not ln.startswith("["):
            k, v = ln.split("=", 1)
            out.setdefault(k, []).append(v)
    return out


def test_unit_serves_only_the_images_folder_on_the_wrapper_port():
    d = _directives()
    assert d["ExecStart"] == ["/usr/bin/python3 /opt/friday/images/images_http.py 8088 /mnt/haos/media/friday/imagens"]
    assert "--bind" not in UNIT.read_text().replace("# ", "").split("[Service]")[1]  # the wrapper picks the address
    assert d["RequiresMountsFor"] == ["/mnt/haos/media/friday/imagens"]
    assert d["Restart"] == ["always"] and d["RestartSec"] == ["30"]
    assert d["WantedBy"] == ["multi-user.target"]


def test_unit_is_confined():
    """Root stays (the CIFS share is root-only), but the process sees almost nothing and can do almost nothing."""
    d = _directives()
    for want in ("ProtectSystem=strict", "ProtectHome=yes", "PrivateTmp=yes", "NoNewPrivileges=yes",
                 "MemoryMax=64M", "RestrictAddressFamilies=AF_INET AF_INET6"):
        k, v = want.split("=", 1)
        assert d.get(k) == [v], want
    assert d.get("CapabilityBoundingSet") == [""]  # empty: no capabilities at all
    assert d["ReadOnlyPaths"] == ["/mnt/haos/media/friday/imagens"]
    hidden = {p.lstrip("-") for v in d["InaccessiblePaths"] for p in v.split()}
    assert {"/mnt/haos/config", "/mnt/haos/share", "/srv/friday"} <= hidden
    assert "/mnt/haos/media" not in hidden  # the images live there
    assert "User" not in d and "DynamicUser" not in d  # documented ruling: root, because of the share


def test_unit_explains_why_it_exists():
    head = UNIT.read_text().split("[Unit]")[0]
    assert "Imager" in head and "root" in head and "bind" in head.lower()


def _wrapper():
    import importlib.util

    spec = importlib.util.spec_from_file_location("images_http", REPO / "server" / "images" / "images_http.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_wrapper_binds_to_one_address_never_all_interfaces():
    w = _wrapper()
    argv = w.command("8088", "/mnt/haos/media/friday/imagens", env={"FRIDAY_IMAGES_BIND": "10.0.0.9"})
    assert argv[:3] == ["/usr/bin/python3", "-m", "http.server"]
    assert argv[3:] == ["8088", "--bind", "10.0.0.9", "--directory", "/mnt/haos/media/friday/imagens"]
    auto = w.command("8088", "/d", env={}, address=lambda: "10.0.0.7")
    assert auto[auto.index("--bind") + 1] == "10.0.0.7"


def test_wrapper_refuses_a_wildcard_or_missing_address():
    import pytest

    w = _wrapper()
    for bad in ("", "0.0.0.0", "::", "127.0.0.1"):
        with pytest.raises(SystemExit):
            w.command("8088", "/d", env={}, address=lambda b=bad: b)


def test_install_copies_the_unit_and_enables_it():
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "FRIDAY_OPT": f"{tmp}/opt", "FRIDAY_UNITS": f"{tmp}/units",
               "FRIDAY_NO_SYSTEMCTL": "1", "FRIDAY_VERSION_LINE": "v0.0.0 " + "a" * 40}
        r = subprocess.run(["bash", str(REPO / "server" / "install.sh")], env=env, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        assert (Path(tmp) / "units" / "friday-images-http.service").read_text() == UNIT.read_text()
        wrapper = Path(tmp) / "opt" / "images" / "images_http.py"
        assert wrapper.read_text() == (REPO / "server" / "images" / "images_http.py").read_text()
    text = (REPO / "server" / "install.sh").read_text()
    block = text.split('if [ "${FRIDAY_NO_SYSTEMCTL:-}" != 1 ]; then')[1].split("\nfi")[0]
    assert "systemctl enable --now friday-images-http.service" in block
