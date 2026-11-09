#!/usr/bin/env python3
"""Local manifest for Raspberry Pi Imager 2.x ("Use custom" -> pick the .json), for a F.R.I.D.A.Y. satellite image.

The entry mirrors what `create_local_json.py` copies from the official list for Raspberry Pi OS Lite (64-bit):
`init_format: cloudinit-rpi` (the Imager writes hostname, Wi-Fi and user into the boot partition's cloud-init seed),
Pi 3 64-bit device tag. Sizes and hashes are computed from the real files; the .xz is decompressed as a stream.

  make_manifest.py IMAGE.img.xz --url URL --out IMAGE.json [--date YYYY-MM-DD] [--extract-sha256 H --extract-size N]
"""
import argparse
import hashlib
import json
import lzma
import os
import sys
from datetime import date

CHUNK = 1 << 20
ICON = "https://downloads.raspberrypi.com/raspios_armhf/Raspberry_Pi_OS_(32-bit).png"
DEVICES = ["pi3-64bit"]  # the Imager's tag for a Raspberry Pi 3 running 64-bit Raspberry Pi OS


def sha256_file(path: str) -> tuple[str, int]:
    h, n = hashlib.sha256(), 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
            n += len(chunk)
    return h.hexdigest(), n


def extracted_sha256(path: str) -> tuple[str, int]:
    h, n = hashlib.sha256(), 0
    with lzma.open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
            n += len(chunk)
    return h.hexdigest(), n


def build(image_xz: str, url: str, release: str, extract: tuple[str, int] | None = None) -> dict:
    dl_sha, dl_size = sha256_file(image_xz)
    ex_sha, ex_size = extract or extracted_sha256(image_xz)
    return {"os_list": [{
        "name": f"F.R.I.D.A.Y. Satellite {release}",
        "description": "Raspberry Pi OS Lite (64-bit, Trixie) with the F.R.I.D.A.Y. voice satellite pre-installed. "
                       "Set Wi-Fi (2.4 GHz) and hostname in the customisation step; leave the username empty or "
                       "jose. It announces itself to Home Assistant on first boot.",
        "url": url,
        "icon": ICON,
        "release_date": release,
        "extract_size": ex_size,
        "extract_sha256": ex_sha,
        "image_download_size": dl_size,
        "image_download_sha256": dl_sha,
        "devices": list(DEVICES),
        "init_format": "cloudinit-rpi",
        "architecture": "arm64",
        "capabilities": [],
    }]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image_xz")
    ap.add_argument("--url", required=True, help="where the PC running the Imager downloads the .img.xz from")
    ap.add_argument("--out", required=True)
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--extract-sha256", help="skip decompressing: hash of the raw .img (build.sh already has it)")
    ap.add_argument("--extract-size", type=int)
    a = ap.parse_args(argv)
    if bool(a.extract_sha256) != bool(a.extract_size):
        ap.error("--extract-sha256 and --extract-size go together")
    if not a.url.startswith(("http://", "https://", "file://")):
        ap.error("--url must be http(s):// or file://")
    extract = (a.extract_sha256, a.extract_size) if a.extract_sha256 else None
    data = build(a.image_xz, a.url, a.date, extract)
    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, a.out)
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
