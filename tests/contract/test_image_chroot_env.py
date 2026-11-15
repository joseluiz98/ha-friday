"""The image tooling never leaks the host's TMPDIR into the arm64 chroot.

14/11 rehearsal: the VM's builder runs build.sh as root from a systemd unit (SUDO empty) with TMPDIR set to a host
work folder; `chroot` kept the environment, the path does not exist inside the image, and apt's dpkg-deb failed
("unable to create temporary directory"). By hand it worked only because sudo resets the environment.
"""

import re
from pathlib import Path

IMAGE = Path(__file__).resolve().parents[2] / "satellite" / "image"
CALL = re.compile(r'\bchroot\s+"\$MNT"')


def _calls():
    for script in sorted(IMAGE.glob("*.sh")):
        for n, line in enumerate(script.read_text().splitlines(), 1):
            if CALL.search(line) and not line.lstrip().startswith("#"):
                yield script.name, n, line


def test_there_are_chroot_calls_to_check():
    assert any(name == "build.sh" for name, _, _ in _calls())


def test_every_chroot_call_resets_tmpdir():
    bad = [f"{name}:{n}" for name, n, line in _calls() if "env TMPDIR=/tmp" not in line]
    assert not bad, f"chroot without env TMPDIR=/tmp: {bad}"
