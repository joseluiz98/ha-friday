"""The unit that serves the satellite images to the Raspberry Pi Imager (plan 2026-11-17-diagnostic-sensors, Task 12)."""

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
UNIT = REPO / "server" / "systemd" / "friday-images-http.service"


def _lines():
    return [ln.strip() for ln in UNIT.read_text().splitlines() if ln.strip() and not ln.startswith("#")]


def test_unit_serves_only_the_images_folder_read_only():
    lines = _lines()
    assert ("ExecStart=/usr/bin/python3 -m http.server 8088 "
            "--directory /mnt/haos/media/friday/imagens") in lines
    assert not any("--bind" in ln for ln in lines)  # the unit cannot know the VM's own address
    for want in ("RequiresMountsFor=/mnt/haos/media/friday/imagens", "After=network-online.target",
                 "Wants=network-online.target", "Restart=always", "RestartSec=30", "WantedBy=multi-user.target"):
        assert want in lines, want
    assert any(ln.startswith("[Install]") for ln in lines)


def test_unit_explains_why_it_exists():
    head = UNIT.read_text().splitlines()[0]
    assert head.startswith("#") and "Imager" in UNIT.read_text().split("[Unit]")[0]


def test_install_copies_the_unit_and_enables_it():
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "FRIDAY_OPT": f"{tmp}/opt", "FRIDAY_UNITS": f"{tmp}/units",
               "FRIDAY_NO_SYSTEMCTL": "1", "FRIDAY_VERSION_LINE": "v0.0.0 " + "a" * 40}
        r = subprocess.run(["bash", str(REPO / "server" / "install.sh")], env=env, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        assert (Path(tmp) / "units" / "friday-images-http.service").read_text() == UNIT.read_text()
    text = (REPO / "server" / "install.sh").read_text()
    block = text.split('if [ "${FRIDAY_NO_SYSTEMCTL:-}" != 1 ]; then')[1].split("\nfi")[0]
    assert "systemctl enable --now friday-images-http.service" in block
