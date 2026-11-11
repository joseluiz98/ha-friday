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
