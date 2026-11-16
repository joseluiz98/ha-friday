import pytest
from friday_server_agent.ota import github as g

def fake(responses, calls):
    def fetch(url):
        calls.append(url)
        v = responses[url]
        if isinstance(v, Exception):
            raise v
        return v
    return fetch

BASE = "https://api.github.com/repos/o/r"
def rel(tag, draft=False):
    return {"tag_name": tag, "draft": draft, "published_at": "2026-11-10T12:00:00Z", "body": "notes", "html_url": "u"}
def runs(*pairs):
    return {"check_runs": [{"status": s, "conclusion": c} for s, c in pairs]}

def test_green_red_pending_and_drafts():
    calls = []
    w = g.GitHubWatcher("o/r", fake({
        f"{BASE}/releases?per_page=30": [rel("v0.3.0"), rel("v0.4.0"), rel("v0.5.0"), rel("v9.9.9", draft=True)],
        f"{BASE}/commits/v0.3.0": {"sha": "a" * 40}, f"{BASE}/commits/v0.3.0/check-runs?per_page=100": runs(("completed", "success"), ("completed", "skipped")),
        f"{BASE}/commits/v0.4.0": {"sha": "b" * 40}, f"{BASE}/commits/v0.4.0/check-runs?per_page=100": runs(("completed", "failure")),
        f"{BASE}/commits/v0.5.0": {"sha": "c" * 40}, f"{BASE}/commits/v0.5.0/check-runs?per_page=100": runs(("in_progress", None)),
    }, calls))
    out = w.poll({})
    assert {t: i.release.ci for t, i in out.items()} == {"v0.3.0": "green", "v0.4.0": "red", "v0.5.0": "pending"}
    assert out["v0.3.0"].release.sha == "a" * 40 and out["v0.3.0"].release.published_at == 1794312000.0

def test_pending_ci_is_not_eligible():
    from friday_agent_core.ota import channels as c
    w = g.GitHubWatcher("o/r", fake({f"{BASE}/releases?per_page=30": [rel("v0.5.0")],
        f"{BASE}/commits/v0.5.0": {"sha": "c" * 40}, f"{BASE}/commits/v0.5.0/check-runs?per_page=100": runs()}, []))
    assert c.eligible([i.release for i in w.poll({}).values()]) == []  # no check-runs yet = pending

def test_final_ci_not_refetched_and_deleted_release_disappears():
    calls = []
    first = g.GitHubWatcher("o/r", fake({f"{BASE}/releases?per_page=30": [rel("v0.3.0")],
        f"{BASE}/commits/v0.3.0": {"sha": "a" * 40}, f"{BASE}/commits/v0.3.0/check-runs?per_page=100": runs(("completed", "success"))}, [])).poll({})
    w = g.GitHubWatcher("o/r", fake({f"{BASE}/releases?per_page=30": [rel("v0.3.0")],
        f"{BASE}/commits/v0.3.0": {"sha": "a" * 40}}, calls))
    assert w.poll(first)["v0.3.0"].release.ci == "green"
    assert not any("check-runs" in u for u in calls)

def test_deleted_release_disappears():
    known = {"v0.3.0": g.ReleaseInfo(release=__import__("friday_agent_core.ota.channels", fromlist=["x"]).Release("v0.3.0", "a"*40, 0.0, "green"), notes="", url="")}
    w = g.GitHubWatcher("o/r", fake({f"{BASE}/releases?per_page=30": []}, []))
    assert w.poll(known) == {}

def test_network_error_raises():
    w = g.GitHubWatcher("o/r", fake({f"{BASE}/releases?per_page=30": OSError("down")}, []))
    with pytest.raises(g.GitHubError):
        w.poll({})

def _watcher(tag, sha, rs, calls, prerelease=False, now=1794312000.0 + 3600):
    r = rel(tag)
    r["prerelease"] = prerelease
    return g.GitHubWatcher("o/r", fake({f"{BASE}/releases?per_page=30": [r],
        f"{BASE}/commits/{tag}": {"sha": sha}, f"{BASE}/commits/{tag}/check-runs?per_page=100": rs}, calls),
        clock=lambda: now)

def test_pending_is_requeried_next_poll():
    first = _watcher("v0.5.0", "c" * 40, runs(("in_progress", None)), []).poll({})
    assert first["v0.5.0"].release.ci == "pending"
    calls = []
    out = _watcher("v0.5.0", "c" * 40, runs(("completed", "success")), calls).poll(first)
    assert out["v0.5.0"].release.ci == "green" and any("check-runs" in u for u in calls)

@pytest.mark.parametrize("conclusion", ["cancelled", "timed_out", "stale", "action_required", None])
def test_cancelled_counts_as_pending(conclusion):
    out = _watcher("v0.5.0", "c" * 40, runs(("completed", conclusion)), []).poll({})
    assert out["v0.5.0"].release.ci == "pending"

def test_failure_beats_pending_and_unknown_is_red():
    assert _watcher("v1.0.0", "a" * 40, runs(("completed", "failure"), ("in_progress", None)), []).poll({})["v1.0.0"].release.ci == "red"
    assert _watcher("v1.0.0", "a" * 40, runs(("completed", "startup_failure")), []).poll({})["v1.0.0"].release.ci == "red"
    assert _watcher("v1.0.0", "a" * 40, runs(("completed", "weird")), []).poll({})["v1.0.0"].release.ci == "red"

def test_full_poll_notices_moved_tag_and_red_to_green():
    red = _watcher("v0.4.0", "b" * 40, runs(("completed", "failure")), []).poll({})
    assert red["v0.4.0"].release.ci == "red"
    calls = []
    w = _watcher("v0.4.0", "d" * 40, runs(("completed", "success")), calls)
    # FINDING 8 (16/11): a red CI is rechecked on every poll (a rerun turns it green); only green is final
    assert w.poll(red)["v0.4.0"].release.ci == "green"
    green = w.poll(red)
    calls.clear()
    w.poll(green)
    assert not any("check-runs" in u for u in calls)
    out = w.poll(red, full=True)["v0.4.0"].release
    assert out.ci == "green" and out.sha == "d" * 40

def test_prerelease_flag_propagates():
    out = _watcher("v0.6.0", "e" * 40, runs(("completed", "success")), [], prerelease=True).poll({})
    assert out["v0.6.0"].release.prerelease is True
    assert _watcher("v0.6.0", "e" * 40, runs(("completed", "success")), []).poll({})["v0.6.0"].release.prerelease is False

def test_malformed_payload_raises():
    w = g.GitHubWatcher("o/r", fake({f"{BASE}/releases?per_page=30": [{"draft": False}]}, []))
    with pytest.raises(g.GitHubError):
        w.poll({})
    w = g.GitHubWatcher("o/r", fake({f"{BASE}/releases?per_page=30": [rel("v0.5.0")],
        f"{BASE}/commits/v0.5.0": {"nosha": 1}}, []))
    with pytest.raises(g.GitHubError):
        w.poll({})

def test_tag_is_url_quoted():
    calls = []
    w = g.GitHubWatcher("o/r", fake({f"{BASE}/releases?per_page=30": [rel("v1/x y")],
        f"{BASE}/commits/v1%2Fx%20y": {"sha": "a" * 40}, f"{BASE}/commits/v1%2Fx%20y/check-runs?per_page=100": runs()}, calls))
    assert w.poll({})["v1/x y"].release.ci == "pending"


def test_an_old_red_stays_final_on_normal_polls():
    red = _watcher("v0.4.0", "b" * 40, runs(("completed", "failure")), []).poll({})
    calls = []
    w = _watcher("v0.4.0", "d" * 40, runs(("completed", "success")), calls, now=1794312000.0 + 3 * 86400)
    assert w.poll(red)["v0.4.0"].release.ci == "red"
    assert not any("check-runs" in u for u in calls)
