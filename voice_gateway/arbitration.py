"""Wake-word arbiter between satellites (spec 2026-11-16-satellite-arbitration, section 5.2).

When several satellites hear the same wake word, each one sends a claim; the arbiter opens a short round, picks one
winner and tells every claimant `go` or `lost`. Pure logic: no FastAPI, no Pipecat. The gateway wraps it in HTTP
routes. It uses its own clock (arrival time), never the satellites'.

The winning criterion lives in `pick` only, so it can be swapped without touching the round mechanics.
"""
import asyncio, json, logging, math, time, uuid

TIE_DB = 1.0  # voice gap below this counts as a tie, decided by the wake-word score
# How long a round waits for the other satellites. Satellites up to v0.4.4 give up on the answer after 0.3 s and act as
# `go` (network time included), so this stays under that budget until they all run a newer program. Measured 24/11:
# 24 two-satellite rounds closed in 27-194 ms, so the old 0.2 s cut the tail off (a real activation lost the round).
WINDOW_S = 0.25


def _num(value, default: float) -> float:
    """Coerce to a finite float; None, strings and NaN/inf fall back to `default` (a claim must never break a round)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _adjusted(sid: str, claim: dict, offsets: dict) -> float:
    return _num(claim.get("voice_dbfs"), -120.0) + _num(offsets.get(sid), 0.0)


def pick(candidates: dict, offsets: dict) -> str:
    """Return the winning sid. Score = voice_dbfs + device offset; a gap under 1 dB to the loudest is a tie that the
    higher wake-word `score` decides."""
    adj = {sid: _adjusted(sid, c, offsets) for sid, c in candidates.items()}
    top = max(adj.values())
    contenders = [sid for sid in candidates if top - adj[sid] < TIE_DB]
    return max(contenders, key=lambda sid: (_num(candidates[sid].get("score"), 0.0), adj[sid], sid))


class _Round:
    def __init__(self, round_id: str, start: float):
        self.id, self.start = round_id, start
        self.candidates: dict[str, dict] = {}
        self.done = asyncio.Event()
        self.timer = None
        self.winner = None
        self.other_busy: dict[str, bool] = {}  # per sid, taken when its claim arrived (log only)


class Arbiter:
    def __init__(self, clock=time.monotonic, window_s=WINDOW_S, same_activation_s=1.5, online_s=30.0,
                 offsets: dict | None = None, log=None):
        self._clock = clock
        self.window_s, self.same_activation_s, self.online_s = window_s, same_activation_s, online_s
        self.offsets = dict(offsets or {})
        self._log = log
        self._seen: dict[str, float] = {}
        self._busy: set[str] = set()  # sids with a conversation open; shown in the log only, never weighs in
        self._round: _Round | None = None
        self._last: _Round | None = None  # last closed round, for late claims

    def _emit(self, record: dict) -> None:
        if self._log is None:
            return
        try:
            self._log(record)
        except Exception:
            logging.exception("arbitration: log failed")

    def _online(self) -> set:
        now = self._clock()
        return {sid for sid, t in self._seen.items() if now - t <= self.online_s}

    def presence(self, sid: str) -> int:
        """Record that `sid` is alive; return how many OTHER satellites are online."""
        self._seen[sid] = self._clock()
        return len(self._online() - {sid})

    def set_busy(self, sid: str, busy: bool) -> None:
        """Mark `sid` as having a conversation open (or not). Informational: it is logged, never used to decide."""
        (self._busy.add if busy else self._busy.discard)(sid)

    def _other_busy(self, sid: str) -> bool:
        """Spec §5.3 `outro_falando`: another satellite has a conversation open. Log only, never decides."""
        return bool(self._busy - {sid})

    def _candidate(self, sid: str, claim: dict, other_busy: bool) -> dict:
        return {"sid": sid, "claim_id": claim.get("claim_id"), "wake_word": claim.get("wake_word"),
                "score": _num(claim.get("score"), 0.0), "voice_dbfs": _num(claim.get("voice_dbfs"), -120.0),
                "offset_db": _num(self.offsets.get(sid), 0.0), "busy": bool(claim.get("busy", False)) or sid in self._busy,
                "other_busy": other_busy}

    def _log_single(self, sid: str, claim: dict, round_id: str, reason: str) -> None:
        self._emit({"ts": time.time(), "round_id": round_id, "window_ms": 0,
                    "candidates": [self._candidate(sid, claim, self._other_busy(sid))], "winner": sid, "reason": reason})

    async def claim(self, sid: str, claim: dict) -> dict:
        self.presence(sid)
        if claim.get("record_only"):
            round_id = uuid.uuid4().hex[:12]
            self._log_single(sid, claim, round_id, "record_only")
            return {"verdict": "go", "reason": "record_only", "round_id": round_id}

        rnd = self._round
        if rnd is None:
            last = self._last
            if (last is not None and sid not in last.candidates
                    and self._clock() - last.start < self.same_activation_s):
                self._emit({"event": "late_claim", "ts": time.time(), "round_id": last.id, "sid": sid,
                            "winner": last.winner, "delay_ms": round((self._clock() - last.start) * 1000),
                            "score": _num(claim.get("score"), 0.0),
                            "voice_dbfs": _num(claim.get("voice_dbfs"), -120.0)})
                return {"verdict": "lost", "reason": "late", "round_id": last.id}
            if not (self._online() - {sid}):
                round_id = uuid.uuid4().hex[:12]
                self._log_single(sid, claim, round_id, "alone")
                return {"verdict": "go", "reason": "alone", "round_id": round_id}
            rnd = self._round = _Round(uuid.uuid4().hex[:12], self._clock())
            rnd.timer = asyncio.ensure_future(self._expire(rnd))
            self._emit({"event": "round_opened", "round_id": rnd.id, "sid": sid, "ts": time.time()})

        previous = rnd.candidates.get(sid)
        if previous is None or pick({"old": previous, "new": claim}, {}) == "new":
            rnd.candidates[sid] = claim  # same satellite twice: keep the best one
            rnd.other_busy[sid] = self._other_busy(sid)
        if self._online() <= set(rnd.candidates):
            self._close(rnd)
        await rnd.done.wait()
        if sid == rnd.winner:
            return {"verdict": "go", "reason": "only_one_heard" if len(rnd.candidates) == 1 else "best",
                    "round_id": rnd.id}
        return {"verdict": "lost", "reason": "best", "round_id": rnd.id}

    async def _expire(self, rnd: _Round) -> None:
        await asyncio.sleep(self.window_s)
        self._close(rnd)

    def _close(self, rnd: _Round) -> None:
        if rnd.done.is_set():
            return
        reason = "only_one_heard" if len(rnd.candidates) == 1 else "best"
        try:
            try:
                rnd.winner = pick(rnd.candidates, self.offsets)
            except Exception:
                logging.exception("arbitration: pick failed, falling back to the first candidate")
                rnd.winner, reason = next(iter(rnd.candidates)), "error"
            self._emit({"ts": time.time(), "round_id": rnd.id,
                        "window_ms": round((self._clock() - rnd.start) * 1000),
                        "candidates": [self._candidate(s, c, rnd.other_busy.get(s, False))
                                       for s, c in rnd.candidates.items()],
                        "winner": rnd.winner, "reason": reason})
        finally:
            # Always release the round: a failure here must never leave a dead round open.
            if self._round is rnd:
                self._round = None
            self._last = rnd
            if rnd.timer is not None and rnd.timer is not asyncio.current_task():
                rnd.timer.cancel()
            rnd.done.set()


def load_offsets(path: str) -> dict:
    """Read `{sid: dB}`; a missing or invalid file means no offsets."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): float(v) for k, v in data.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)}
