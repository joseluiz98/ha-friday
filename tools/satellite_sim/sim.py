#!/usr/bin/env python3
"""Simulated F.R.I.D.A.Y. satellite for testing wake-word arbitration with a single real satellite.

It authenticates to the voice gateway like a real satellite (Bearer token of its own registry entry), keeps
presence alive (so the real satellite sees "another satellite online") and sends wake claims with a chosen
voice level and score. Standard library only. LAN test tool: the gateway's self-signed certificate is not
verified. The token is read from a file and is never printed.

  sim.py --gateway https://<vm-address>:7443 --token-file /srv/friday/state/arbitration/sim.token --presence-only
  sim.py ... --claim --voice-dbfs -20 --score 0.9 [--delay-ms 0]
  sim.py ... --claim-loop --voice-dbfs -20 --score 0.9   (run on the VM; also keeps presence)
"""
import argparse
import http.client
import json
import os
import ssl
import sys
import threading
import time
import urllib.parse
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

DEFAULT_SID = "sat-f510a1"  # the id registered for the simulator; used to ignore its own rounds
DEFAULT_DIR = "/srv/friday/state/arbitration"
PRESENCE_EVERY_S = 4.0  # same cadence as the Pi client
POLL_EVERY_S = 0.02
CLAIM_TIMEOUT_S = 1.0
MIN_TOKEN_LEN = 32
# what an idle keep-alive connection closed by the server looks like on the next use
STALE_CONNECTION_ERRORS = (http.client.RemoteDisconnected, http.client.BadStatusLine, http.client.CannotSendRequest,
                           BrokenPipeError, ConnectionResetError, ConnectionAbortedError)
_SAO_PAULO = ZoneInfo("America/Sao_Paulo")


def build_claim_body(voice_dbfs, score, wake_word="friday", busy=False, claim_id=None):
    """Same shape the Pi client sends to POST /wake_claim."""
    return {"claim_id": claim_id or uuid.uuid4().hex, "wake_word": wake_word, "score": float(score),
            "voice_dbfs": float(voice_dbfs), "busy": bool(busy)}


def day_file_name(now):
    """The arbiter's log file for the instant `now` (day in America/Sao_Paulo)."""
    return datetime.fromtimestamp(now, _SAO_PAULO).strftime("%Y-%m-%d") + ".ndjson"


class RoundLogTail:
    """Reads the `round_opened` records appended to today's arbitration log. Starts at the current end of the
    file (history is not replayed); a half-written last line waits for its newline; a new day starts at 0."""

    def __init__(self, directory, clock=time.time):
        self._dir, self._clock = directory, clock
        self._path = self._current_path()
        self._offset = self._size(self._path)
        self._rest = b""

    def _current_path(self):
        return os.path.join(self._dir, day_file_name(self._clock()))

    @staticmethod
    def _denied(path, e):
        """Unreadable log = the claim loop would never claim: stop with a clear message instead of polling [] forever.
        On the VM /srv/friday/state is 0700 and the logs 0600, owned by root (the container writes them)."""
        return SystemExit(f"permission denied reading {path} ({type(e).__name__}): the arbitration log is root-only "
                          "on the VM, run the simulator with sudo")

    def _size(self, path):
        try:
            return os.stat(path).st_size
        except FileNotFoundError:
            return 0  # not written yet (no round today, or the directory does not exist yet)
        except PermissionError as e:
            raise self._denied(path, e)
        except OSError:
            return 0

    def poll(self):
        path = self._current_path()
        if path != self._path:
            self._path, self._offset, self._rest = path, 0, b""
        size = self._size(path)
        if size < self._offset:  # truncated or replaced
            self._offset, self._rest = 0, b""
        if size == self._offset:
            return []
        try:
            with open(path, "rb") as f:
                f.seek(self._offset)
                data = f.read(size - self._offset)
        except PermissionError as e:
            raise self._denied(path, e)
        except OSError:
            return []
        self._offset += len(data)
        data = self._rest + data
        *lines, self._rest = data.split(b"\n")
        rounds = []
        for raw in lines:
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("event") == "round_opened":
                rounds.append(rec)
        return rounds


def claim_for_foreign_rounds(records, own_sid, sender, voice_dbfs, score, delay_ms, seen, sleep=time.sleep):
    """Send one claim for each round opened by ANOTHER satellite (never our own, never twice for a round).
    Returns the verdict dicts."""
    results = []
    for rec in records:
        round_id = rec.get("round_id")
        if not round_id or rec.get("sid") == own_sid or round_id in seen:
            continue
        seen.add(round_id)
        if delay_ms:
            sleep(delay_ms / 1000.0)
        status, body = sender("/wake_claim", build_claim_body(voice_dbfs, score))
        results.append({"round_opened": round_id, "status": status, **(body if isinstance(body, dict) else {})})
    return results


def read_token(path):
    """The token from a file. Errors never include its content."""
    try:
        with open(path) as f:
            token = f.read().strip()
    except OSError as e:
        raise SystemExit(f"cannot read the token file {path}: {type(e).__name__}")
    if len(token) < MIN_TOKEN_LEN:
        raise SystemExit(f"the token in {path} is shorter than {MIN_TOKEN_LEN} characters")
    return token


class GatewayClient:
    """POST JSON with the satellite Bearer token over one keep-alive connection (so the TLS handshake does not
    eat the 200 ms window). `post` never raises and never exposes the token: it returns (None, {"error":
    <exception class>}) on failure. Thread-safe: a lock serializes the threads sharing one client (the claim loop
    sends presence through the claim client to keep its connection warm)."""

    def __init__(self, gateway, token, conn_factory=None, timeout_s=CLAIM_TIMEOUT_S):
        self._token = token
        self._timeout_s = timeout_s
        self._conn = None
        self._lock = threading.Lock()
        self._last_error = None
        parsed = urllib.parse.urlparse(gateway)
        self._host, self._port = parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
        self._https = parsed.scheme == "https"
        self._factory = conn_factory or self._real_connection

    def _real_connection(self):
        if not self._https:
            return http.client.HTTPConnection(self._host, self._port, timeout=self._timeout_s)
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # self-signed gateway cert, LAN test tool
        return http.client.HTTPSConnection(self._host, self._port, timeout=self._timeout_s, context=ctx)

    def close(self):
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def post(self, path, body):
        """One POST. A REUSED connection that fails at connection level (the server closed it while idle) is retried
        once on a fresh one; a fresh connection is never retried. The lock serializes the threads sharing a client."""
        with self._lock:
            reused = self._conn is not None
            result = self._post_once(path, body)
            if result is None and reused:
                result = self._post_once(path, body)
            return result if result is not None else (None, {"error": self._last_error})

    def _post_once(self, path, body):
        """(status, dict), or None on a connection-level failure that deserves a retry when reused. Other failures
        return (None, {"error": <class>}). Never exposes the token."""
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}
        try:
            if self._conn is None:
                self._conn = self._factory()
            self._conn.request("POST", path, body=json.dumps(body), headers=headers)
            resp = self._conn.getresponse()
            raw = resp.read()
            try:
                data = json.loads(raw)
            except ValueError:
                data = {}
            return resp.status, data if isinstance(data, dict) else {}
        except Exception as e:
            self.close()  # connection state unknown: the next call reconnects
            self._last_error = type(e).__name__
            if isinstance(e, STALE_CONNECTION_ERRORS):
                return None
            return None, {"error": self._last_error}


def presence_loop(client, stop, every_s=PRESENCE_EVERY_S, quiet=False):
    last = object()
    while not stop.is_set():
        status, body = client.post("/satellite_presence", {})
        state = (status, body.get("others_online") if status == 200 else body.get("error"))
        if state != last and not quiet:
            print(json.dumps({"presence": status, "others_online": body.get("others_online"),
                              "error": body.get("error")}), flush=True)
        last = state
        stop.wait(every_s)


def run_claim_loop(args, token):
    stop = threading.Event()
    claim_client = GatewayClient(args.gateway, token)  # presence shares it (locked): warms the connection every 4 s
    threading.Thread(target=presence_loop, args=(claim_client, stop), daemon=True).start()
    tail, seen = RoundLogTail(args.arbitration_dir), set()
    print(json.dumps({"claim_loop": "watching", "dir": args.arbitration_dir, "own_sid": args.sid}), flush=True)
    try:
        while True:
            for result in claim_for_foreign_rounds(tail.poll(), args.sid, claim_client.post, args.voice_dbfs,
                                                   args.score, args.delay_ms, seen):
                print(json.dumps(result), flush=True)
            time.sleep(POLL_EVERY_S)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--gateway", required=True, help="e.g. https://<vm-address>:7443")
    p.add_argument("--token-file", required=True)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--presence-only", action="store_true", help="send presence every 4 s until Ctrl-C")
    mode.add_argument("--claim", action="store_true", help="send one claim and print the verdict")
    mode.add_argument("--claim-loop", action="store_true",
                      help="keep presence and claim whenever another satellite opens a round (run on the VM)")
    p.add_argument("--voice-dbfs", type=float, default=-20.0)
    p.add_argument("--score", type=float, default=0.9)
    p.add_argument("--delay-ms", type=int, default=0)
    p.add_argument("--sid", default=DEFAULT_SID, help="the simulator's registered id (to ignore its own rounds)")
    p.add_argument("--arbitration-dir", default=os.environ.get("FRIDAY_ARBITRATION_DIR", DEFAULT_DIR))
    args = p.parse_args(argv)
    token = read_token(args.token_file)

    if args.presence_only:
        stop = threading.Event()
        try:
            presence_loop(GatewayClient(args.gateway, token), stop)
        except KeyboardInterrupt:
            stop.set()
        return 0
    if args.claim:
        if args.delay_ms:
            time.sleep(args.delay_ms / 1000.0)
        status, body = GatewayClient(args.gateway, token).post(
            "/wake_claim", build_claim_body(args.voice_dbfs, args.score))
        print(json.dumps({"status": status, **body}))
        return 0 if status == 200 else 1
    run_claim_loop(args, token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
