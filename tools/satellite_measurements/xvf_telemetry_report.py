"""Read-only report over the XVF3800 telemetry, the TV's HA history and the satellite journal (two-beam study).

    python3 xvf_telemetry_report.py --telemetry DIR --tv-history FILE.json [--conversations DIR] \
        [--journal FILE] --out FILE.json

--telemetry: the NDJSON files of xvf_telemetry.py. --tv-history: the list-of-lists from
/api/history/period/<start>?filter_entity_id=<tv>&minimal_response&no_attributes. --journal: a `journalctl -o cat`
export of the satellite program (two_beam_* event lines). --conversations: the conversation NDJSON dir (turn counts).
Nothing is written but --out.
"""
import argparse
import datetime
import glob
import json
import os
import statistics

OFF_STATES = ("off", "unavailable", "unknown", "standby")


def circ_dist(a, b):
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def circ_median(vals):
    """Degree minimising the sum of circular distances (1 degree resolution)."""
    if not vals:
        return None
    counts = [0] * 360
    for v in vals:
        counts[int(round(v)) % 360] += 1
    occupied = [(b, c) for b, c in enumerate(counts) if c]
    best = min(range(360), key=lambda t: sum(c * circ_dist(t, b) for b, c in occupied))
    return float(best)


def percentile(vals, p):
    if not vals:
        return None
    s = sorted(vals)
    return s[min(len(s) - 1, max(0, int(-(-p * len(s) // 100)) - 1))]


def circ_summary(vals):
    med = circ_median(vals)
    if med is None:
        return {"n": 0, "median_deg": None, "spread_p90_deg": None}
    return {"n": len(vals), "median_deg": med,
            "spread_p90_deg": round(percentile([circ_dist(v, med) for v in vals], 90), 1)}


def parse_ts(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def tv_on_intervals(history):
    """[(start, end)] epoch intervals where the TV was on; the last one is open-ended (end = inf)."""
    series = history[0] if history and isinstance(history[0], list) else history
    events = sorted((parse_ts(e["last_changed"]), e["state"]) for e in series or [] if e.get("last_changed"))
    out, start = [], None
    for t, st in events:
        on = st not in OFF_STATES
        if on and start is None:
            start = t
        elif not on and start is not None:
            out.append((start, t))
            start = None
    if start is not None:
        out.append((start, float("inf")))
    return out


def _in(t, intervals):
    return any(a <= t < b for a, b in intervals)


def load_telemetry(directory):
    recs = []
    for path in sorted(glob.glob(os.path.join(directory, "*.ndjson"))):
        with open(path) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and not r.get("slow") and "t" in r:
                    recs.append(r)
    return recs


def _get(r, key, i):
    v = r.get(key)
    try:
        return v[i]
    except (TypeError, IndexError):
        return None


def telemetry_report(recs, tv_intervals):
    tv, callers, seps, ratios = [], [], [], []
    follow = follow_den = 0
    import math
    for r in recs:
        fx = r.get("fx")
        az2, e2 = _get(r, "az", 2), _get(r, "e", 2)
        if fx == 0 and az2 is not None and e2 is not None and e2 > 0 and _in(r["t"], tv_intervals):
            tv.append(az2)
        if fx == 1:
            az0, az3 = _get(r, "az", 0), _get(r, "az", 3)
            if az0 is not None:
                callers.append(az0)
            if az0 is not None and az2 is not None and az3 is not None:
                follow_den += 1
                if circ_dist(az3, az2) <= 5 and circ_dist(az3, az0) > 20:
                    follow += 1
            e0, e1 = _get(r, "e", 0), _get(r, "e", 1)
            if e0 is not None and e1 is not None and e0 > 0 and e1 > 0:
                ratios.append(10 * math.log10(e0 / e1))
    tv_s, ca_s = circ_summary(tv), circ_summary(callers)
    sep = None
    if tv_s["median_deg"] is not None and ca_s["median_deg"] is not None:
        sep = round(circ_dist(tv_s["median_deg"], ca_s["median_deg"]), 1)
    return {
        "tv": tv_s, "callers": ca_s, "separation_deg": sep,
        "autoselect_follows_free_in_fixed": {"n": follow_den, "fraction": round(follow / follow_den, 3) if follow_den else None},
        "energy": {"n": len(ratios), "median_db": round(statistics.median(ratios), 1) if ratios else None,
                   "p10_db": round(percentile(ratios, 10), 1) if ratios else None,
                   "p90_db": round(percentile(ratios, 90), 1) if ratios else None},
    }


def journal_summary(lines):
    modes, by, releases = {}, {}, {}
    ceiling = dropped = convs = reaims = 0
    ratios, stale = [], 0.0
    for line in lines:
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        if "two_beam_mode" in obj and isinstance(obj["two_beam_mode"], dict):
            k = obj["two_beam_mode"].get("reason", "?")
            modes[k] = modes.get(k, 0) + 1
        elif "two_beam_turn_ceiling" in obj:
            ceiling += 1
        elif "two_beam_dropped_candidate" in obj:
            dropped += 1
            d = obj["two_beam_dropped_candidate"]
            if isinstance(d, dict) and d.get("ratio_db_median") is not None:
                ratios.append(d["ratio_db_median"])
        elif "two_beam_summary" in obj and isinstance(obj["two_beam_summary"], dict):
            s = obj["two_beam_summary"]
            convs += 1
            for k, v in (s.get("by") or {}).items():
                by[k] = by.get(k, 0) + v
            reaims += s.get("reaims") or 0
            stale += s.get("energy_stale_s") or 0
            rel = s.get("release", "?")
            releases[rel] = releases.get(rel, 0) + 1
    return {
        "conversations_by_reason": modes, "turn_ends_by": by, "turn_ceiling_count": ceiling,
        "dropped_candidates": {"n": dropped, "median_ratio_db": round(statistics.median(ratios), 1) if ratios else None},
        "summaries": convs, "reaims_total": reaims,
        "reaims_per_conversation": round(reaims / convs, 2) if convs else None,
        "energy_stale_s_total": round(stale, 1), "releases_by_reason": releases,
    }


def conversations_summary(directory):
    n = 0
    for path in glob.glob(os.path.join(directory, "*.ndjson")):
        with open(path) as f:
            n += sum(1 for line in f if line.strip())
    return {"records": n}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--telemetry", required=True)
    ap.add_argument("--tv-history", required=True)
    ap.add_argument("--conversations")
    ap.add_argument("--journal")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    with open(a.tv_history) as f:
        intervals = tv_on_intervals(json.load(f))
    report = telemetry_report(load_telemetry(a.telemetry), intervals)
    if a.journal:
        with open(a.journal) as f:
            report["journal"] = journal_summary(f)
    if a.conversations:
        report["conversations"] = conversations_summary(a.conversations)
    with open(a.out, "w") as f:
        json.dump(report, f, indent=2)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
