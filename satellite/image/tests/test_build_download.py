"""build.sh base image download (ledger 2026-11-10-friday-ota Task 12 minor): an interrupted curl must not leave a
truncated .xz that every later build trusts. build.sh needs root and loop devices, so this checks its text and runs
its download lines against a fake curl/xz."""

import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

BUILD = Path(__file__).resolve().parents[1] / "build.sh"


def _download_block() -> str:
    text = BUILD.read_text()
    start = text.index('echo "== base image"')
    return text[start:text.index('echo "== grow"')]


def _run(tmp: Path, curl_ok: bool, xz_ok: bool) -> subprocess.CompletedProcess:
    bin_ = tmp / "bin"
    bin_.mkdir(exist_ok=True)
    (bin_ / "curl").write_text(textwrap.dedent(f"""\
        #!/bin/sh
        while [ "$1" != "-o" ]; do shift; done
        echo partial > "$2"
        exit {0 if curl_ok else 18}
        """))
    (bin_ / "xz").write_text(f"#!/bin/sh\nexit {0 if xz_ok else 1}\n")
    for f in bin_.iterdir():
        f.chmod(0o755)
    script = (f'set -e\nBASE_XZ={tmp}/base.img.xz\nBASE_IMG={tmp}/base.img\nBASE_URL=http://x\nBASE_SIZE=1\n'
              f'BASE_SHA256=0\n' + _download_block())
    return subprocess.run(["bash", "-c", script], env={"PATH": f"{bin_}:/usr/bin:/bin"}, capture_output=True,
                          text=True)


class TestBaseDownload(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.tmp = Path(self._d.name)

    def tearDown(self):
        self._d.cleanup()

    def test_a_cut_download_leaves_no_xz(self):
        self.assertNotEqual(_run(self.tmp, curl_ok=False, xz_ok=True).returncode, 0)
        self.assertFalse((self.tmp / "base.img.xz").exists() or (self.tmp / "base.img.xz.part").exists())

    def test_an_xz_that_does_not_decompress_is_dropped(self):
        self.assertNotEqual(_run(self.tmp, curl_ok=True, xz_ok=False).returncode, 0)
        self.assertFalse((self.tmp / "base.img.xz").exists())
