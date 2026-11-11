"""OTA orchestrator (plan 2026-11-10 Task 8): state, targets, offers, reports, soak, pushes, retention."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "server"))
from friday_agent_core.ota import channels as ch, manifest as mf  # noqa: E402
from friday_agent_core.ota.constants import SOAK_S  # noqa: E402
from friday_server_agent.ota import github as gh, orchestrator as orc, store as st  # noqa: E402

SAT = "sat-00aa11"


def rel(tag, pre=False, ci="green"):
    return gh.ReleaseInfo(release=ch.Release(tag, tag.replace(".", "") * 8, 0.0, ci, pre), notes=f"notes {tag}", url="u")


def man(server_proto=1, requires=1):
    return mf.parse({"schema": 1, "protocol": {"satellite": 1, "server": server_proto, "update": 1},
                     "min_protocol": {"satellite": 1}, "requires": {"satellite": {"server": requires}}, "heavy": [],
                     "components": {c: [c] for c in mf.COMPONENT_ORDER}})


class World:
    def __init__(self, tmp, releases, manifests=None):
        self.releases = {r.release.tag: r for r in releases}
        self.manifests = manifests or {}
        self.built, self.pushed, self.now = [], [], 1000.0
        self.store = st.OtaStore(str(tmp / "state.json"), str(tmp / "events.ndjson"))
        self.tokens = st.TokenBook(str(tmp / "tokens.json"))
        world = self

        class Watcher:
            def poll(self, known, full=False):
                world.full = full
                return dict(world.releases)

        class Repo:
            def manifest(self, sha):
                return world.manifests.get(sha)

        class Notifier:
            def queue(self, text, pushes):
                world.pushed.append(text)

            def flush(self, pushes):
                return 0

        def build(repo, tag, sha, vault, root, wheels):
            world.built.append(tag)
            return {"tag": tag, "sha": sha, "satellite": {"path": f"{root}/packages/{tag}/satellite.tar.gz",
                    "sha256": "s" * 64, "size": 10, "lock_sha256": "L" * 64, "wheels": f"/w/{tag}.tar.gz",
                    "protocol": {}}, "server": {"path": "a.tar"}}
        self.orch = orc.Orchestrator(self.store, self.tokens, Watcher(), Repo(), vault=None, notifier=Notifier(),
                                     build=build, wheels=lambda *a: "", root=str(tmp / "ota"),
                                     clock=lambda: world.now, prune=lambda root, keep: [])

    def offer(self, channel="stable", installed="v0.2.0", lock="L" * 64, sat=SAT):
        return self.orch.offer(sat, channel, installed, 1, lock)


def test_stable_satellite_gets_normal_release_not_prerelease(tmp_path):
    w = World(tmp_path, [rel("v0.2.0"), rel("v0.3.0"), rel("v0.4.0", pre=True)])
    w.orch.poll()
    o = w.offer("stable")
    assert o["tag"] == "v0.3.0" and o["url"] == "/v1/ota/package/v0.3.0/satellite.tar.gz"
    assert o["wheels_url"] is None and o["auto_install"] is True and o["install_requested"] is False


def test_alpha_satellite_gets_prerelease(tmp_path):
    w = World(tmp_path, [rel("v0.3.0"), rel("v0.4.0", pre=True)])
    w.orch.poll()
    assert w.offer("alpha")["tag"] == "v0.4.0"


def test_only_targets_are_built(tmp_path):
    w = World(tmp_path, [rel("v0.1.0"), rel("v0.2.0"), rel("v0.3.0", pre=True), rel("v0.5.0", ci="pending")])
    w.offer("alpha")  # registers an alpha device before the poll
    w.orch.poll()
    assert sorted(w.built) == ["v0.2.0", "v0.3.0"]


def test_up_to_date_gets_nothing(tmp_path):
    w = World(tmp_path, [rel("v0.3.0")])
    w.orch.poll()
    assert w.offer("stable", installed="v0.3.0")["tag"] is None


def test_wheels_offered_only_when_lock_differs(tmp_path):
    w = World(tmp_path, [rel("v0.3.0")])
    w.orch.poll()
    o = w.offer("stable", lock="X" * 64)
    assert o["wheels_url"] == "/v1/ota/package/v0.3.0/wheels.tar.gz"


def test_satellite_offer_waits_for_required_server_protocol(tmp_path):
    r = rel("v0.3.0")
    w = World(tmp_path, [r], manifests={r.release.sha: man(server_proto=2, requires=2)})
    w.orch.poll()
    o = w.offer("stable")
    assert o["tag"] is None and o["reason"] == "waiting_server"
    w.store.set_server_protocol(2)
    assert w.offer("stable")["tag"] == "v0.3.0"


def test_failures_back_off_then_push_once(tmp_path):
    w = World(tmp_path, [rel("v0.3.0")])
    w.orch.poll()
    w.offer("stable")
    for i in range(5):
        w.orch.report({"sat_id": SAT, "tag": "v0.3.0", "result": "rolled_back", "stage": "health", "error": "no_program"})
        if i < 4:
            assert w.offer("stable")["tag"] is None  # waiting next_at
            w.now += 86400 + 1
    assert w.offer("stable")["tag"] is None and w.offer("stable")["reason"] == "exhausted"
    assert len(w.pushed) == 1 and "v0.3.0" in w.pushed[0] and "health" in w.pushed[0]


def test_manual_install_resets_attempts(tmp_path):
    w = World(tmp_path, [rel("v0.3.0")])
    w.orch.poll()
    w.offer("stable")
    for _ in range(5):
        w.orch.report({"sat_id": SAT, "tag": "v0.3.0", "result": "failed", "stage": "download", "error": "x"})
        w.now += 86400 + 1
    w.orch.install(sat_id=SAT)
    o = w.offer("stable")
    assert o["tag"] == "v0.3.0" and o["install_requested"] is True


def test_installed_report_clears_request_and_attempts(tmp_path):
    w = World(tmp_path, [rel("v0.3.0")])
    w.orch.poll()
    w.orch.install(sat_id=SAT)
    w.orch.report({"sat_id": SAT, "tag": "v0.3.0", "result": "installed", "stage": "", "error": ""})
    d = w.store.device(SAT)
    assert d["installed"] == "v0.3.0" and d["install_requested"] is False and d["attempts"] is None


def test_alpha_health_starts_soak_and_promotes(tmp_path):
    w = World(tmp_path, [rel("v0.3.0"), rel("v0.4.0", pre=True)])
    w.orch.poll()
    w.offer("alpha", sat="sat-0000a1")
    w.orch.report({"sat_id": "sat-0000a1", "tag": "v0.4.0", "result": "installed", "stage": "", "error": ""})
    assert w.offer("stable")["tag"] == "v0.3.0"
    w.now += SOAK_S
    w.orch.poll()
    assert w.offer("stable")["tag"] == "v0.4.0"


def test_alpha_failure_blocks_promotion(tmp_path):
    w = World(tmp_path, [rel("v0.3.0"), rel("v0.4.0", pre=True)])
    w.orch.poll()
    w.offer("alpha", sat="sat-0000a1")
    w.orch.report({"sat_id": "sat-0000a1", "tag": "v0.4.0", "result": "installed", "stage": "", "error": ""})
    w.orch.report({"sat_id": "sat-0000a1", "tag": "v0.4.0", "result": "rolled_back", "stage": "health", "error": "e"})
    w.now += 10 * SOAK_S
    w.orch.poll()
    assert w.offer("stable")["tag"] == "v0.3.0"


def test_auto_install_off_is_in_the_offer(tmp_path):
    w = World(tmp_path, [rel("v0.3.0")])
    w.orch.poll()
    w.orch.set_auto(False)
    assert w.offer("stable")["auto_install"] is False


def test_state_survives_restart(tmp_path):
    w = World(tmp_path, [rel("v0.3.0")])
    w.orch.poll()
    w.orch.set_auto(False)
    w.offer("alpha")
    again = st.OtaStore(str(tmp_path / "state.json"), str(tmp_path / "events.ndjson"))
    assert again.auto_install is False and again.device(SAT)["channel"] == "alpha"
    assert "v0.3.0" in again.release_infos()


def test_package_for_update_is_the_stable_target(tmp_path):
    w = World(tmp_path, [rel("v0.3.0"), rel("v0.4.0", pre=True)])
    w.orch.poll()
    assert w.orch.package_for_update().endswith("packages/v0.3.0/satellite.tar.gz")


def test_events_are_logged_without_secrets(tmp_path):
    w = World(tmp_path, [rel("v0.3.0")])
    w.orch.poll()
    tok = w.tokens.issue(SAT)
    w.orch.report({"sat_id": SAT, "tag": "v0.3.0", "result": "failed", "stage": "download", "error": "timeout"})
    text = (tmp_path / "events.ndjson").read_text()
    assert '"event": "failed"' in text and tok not in text


def test_token_book(tmp_path):
    b = st.TokenBook(str(tmp_path / "t.json"))
    tok = b.issue(SAT)
    assert b.check(SAT, tok) and not b.check(SAT, tok + "x") and not b.check("sat-ffffff", tok)
    assert tok not in (tmp_path / "t.json").read_text()
    assert oct((tmp_path / "t.json").stat().st_mode & 0o777) == "0o600"
    tok2 = b.issue(SAT)
    assert not b.check(SAT, tok) and b.check(SAT, tok2)


def test_bad_report_rejected(tmp_path):
    w = World(tmp_path, [rel("v0.3.0")])
    with pytest.raises(ValueError, match="result"):
        w.orch.report({"sat_id": SAT, "tag": "v0.3.0", "result": "maybe", "stage": "", "error": ""})


def test_retention_keeps_targets_and_installed(tmp_path):
    w = World(tmp_path, [rel("v0.1.0"), rel("v0.2.0"), rel("v0.3.0"), rel("v0.4.0"), rel("v0.5.0", pre=True)])
    kept = []
    w.orch._prune = lambda root, keep: kept.append(set(keep)) or []
    w.offer("stable", installed="v0.1.0")
    w.orch.poll()
    assert kept and kept[-1] == {"v0.1.0", "v0.3.0", "v0.4.0"}  # installed + the KEEP_RELEASES newest stable
