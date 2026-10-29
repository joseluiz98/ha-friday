#!/usr/bin/env python3
"""Usage: ws_probe.py <ws-url> <token-file>. Prints only message types and keys; never the token."""
import asyncio
import sys

import aiohttp


async def main(url, token_file):
    token = open(token_file, encoding="utf-8").read().strip()
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(url, headers={"Authorization": f"Bearer {token}"}, timeout=10) as ws:
            msg = await ws.receive_json(timeout=10)
            print("type:", msg.get("type"))
            print("device keys:", sorted(msg.get("device", {})))
            print("capability keys:", [d["key"] for d in msg.get("capabilities", [])])
            print("state keys:", sorted(msg.get("state", {})))


asyncio.run(main(sys.argv[1], sys.argv[2]))
