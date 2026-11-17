#!/usr/bin/python3
"""Starts the read-only images server (friday-images-http.service) bound to ONE address: the VM's LAN address.

http.server without --bind listens on every interface (the Docker bridges and IPv6 included). The unit cannot know the
VM's address, so this wrapper finds it: FRIDAY_IMAGES_BIND if set, else the source address the kernel picks for the
default route (a UDP connect sends nothing). No address, or a wildcard/loopback one: exit non-zero and let systemd retry,
never fall back to listening everywhere.   Usage: images_http.py <port> <directory>
"""

from __future__ import annotations

import os
import socket
import sys

PYTHON = "/usr/bin/python3"
REFUSED = ("", "0.0.0.0", "::", "127.0.0.1", "::1")


def lan_address() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 9))  # TEST-NET-1: only asks the routing table, nothing is sent
            return s.getsockname()[0]
        except OSError:
            return ""


def command(port: str, directory: str, env=None, address=lan_address) -> list[str]:
    env = os.environ if env is None else env
    addr = env.get("FRIDAY_IMAGES_BIND") or address()
    if addr in REFUSED:
        raise SystemExit(f"images_http: no LAN address to bind ({addr or 'none'}); refusing to listen everywhere")
    return [PYTHON, "-m", "http.server", port, "--bind", addr, "--directory", directory]


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        raise SystemExit("usage: images_http.py <port> <directory>")
    argv_exec = command(argv[1], argv[2])
    os.execv(argv_exec[0], argv_exec)


if __name__ == "__main__":
    main(sys.argv)
