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
    def __init__(self, tmp, releases, manifests=None, own="v99.0.0"):
        self.releases = {r.release.tag: r for r in releases}
        # releases of the OTA era unless a test says otherwise ({} = every release is pre-OTA)
        self.manifests = {r.release.sha: man() for r in releases} if manifests is None else manifests
        self.built, self.pushed, self.now, self.rebuilds = [], [], 1000.0, []
        self.repo_calls, self.repo_down, self.build_fails, self.gate = [], False, False, None
        self.store = st.OtaStore(str(tmp / "state.json"), str(tmp / "events.ndjson"))
        self.tokens = st.TokenBook(str(tmp / "tokens.json"))
        world = self

        class Watcher:
            def poll(self, known, full=False):
                world.full = full
                return dict(world.releases)

        class Repo:
            def ensure(self):
                world.repo_calls.append("ensure")
                if world.repo_down:
                    raise OSError("down")

            def fetch_tags(self):
                world.repo_calls.append("fetch")

            def manifest(self, sha):
                return world.manifests.get(sha)

        class Notifier:
            def queue(self, text, pushes):
                world.pushed.append(text)

            def flush(self, pushes):
                return 0

        def build(repo, tag, sha, vault, root, wheels, built_by="", satellite_only=False):
            if satellite_only:
                world.rebuilds.append(True)
            if world.gate is not None:
                world.gate.wait(5)
            world.built.append(tag)
            if world.build_fails:
                raise RuntimeError("no vault")
            return {"tag": tag, "sha": sha, "satellite": {"path": f"{root}/packages/{tag}/satellite.tar.gz",
                    "sha256": "s" * 64, "size": 10, "lock_sha256": "L" * 64, "wheels": f"/w/{tag}.tar.gz",
                    "protocol": {}}, "server": {"path": "a.tar"}, "built_by": built_by}
        self.own = own
        # by default the server already runs the newest release (the server-first gate is open)
        newest = max(self.releases, key=ch.version_key) if self.releases else None
        self.store.data["server_ota"]["installed_tag"] = newest
        self.orch = orc.Orchestrator(self.store, self.tokens, Watcher(), Repo(), vault=None, notifier=Notifier(),
                                     build=build, wheels=lambda *a: "", root=str(tmp / "ota"),
                                     clock=lambda: world.now, prune=lambda root, keep: [],
                                     own_version=lambda: world.own)

    def server_at(self, tag):
        self.store.data["server_ota"]["installed_tag"] = tag

    def agent_at(self, tag):
        self.own = tag

    def offer(self, channel="stable", installed="v0.2.0", lock="L" * 64, sat=SAT):
        return self.orch.offer(sat, channel, installed, 1, lock)


def test_stable_satellite_gets_normal_release_not_prerelease(tmp_path):
    w = World(tmp_path, [rel("v0.2.0"), rel("v0.3.0"), rel("v0.4.0", pre=True)])
    w.orch.poll()
    o = w.offer("stable")
    assert o["tag"] == "v0.3.0" and o["url"] == f"/v1/ota/package/v0.3.0/satellite.tar.gz?sat_id={SAT}"
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
    assert o["wheels_url"] == f"/v1/ota/package/v0.3.0/wheels.tar.gz?sat_id={SAT}"


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


def test_offer_urls_carry_the_sat_id(tmp_path):
    w = World(tmp_path, [rel("v0.3.0")])
    w.orch.poll()
    o = w.offer("stable", lock="X" * 64)
    assert o["url"] == f"/v1/ota/package/v0.3.0/satellite.tar.gz?sat_id={SAT}"
    assert o["wheels_url"] == f"/v1/ota/package/v0.3.0/wheels.tar.gz?sat_id={SAT}"


def test_poll_clones_and_fetches_and_survives_a_down_repo(tmp_path):
    w = World(tmp_path, [rel("v0.3.0")])
    w.orch.poll()
    assert w.repo_calls[:2] == ["ensure", "fetch"]
    w.repo_down = True
    w.orch.poll()  # no exception
    assert w.offer("stable")["tag"] == "v0.3.0"


def test_report_is_not_blocked_by_a_slow_build(tmp_path):
    import threading
    w = World(tmp_path, [rel("v0.3.0")])
    w.gate = threading.Event()
    t = threading.Thread(target=w.orch.poll)
    t.start()
    done = threading.Event()
    threading.Thread(target=lambda: (w.orch.report({"sat_id": SAT, "tag": "v0.3.0", "result": "failed",
                                                     "stage": "download", "error": "x"}), done.set())).start()
    assert done.wait(2), "report waited for the build"
    w.gate.set()
    t.join(5)


def test_no_downgrade_offer(tmp_path):
    w = World(tmp_path, [rel("v0.3.0")])
    w.orch.poll()
    o = w.offer("stable", installed="v0.4.0")
    assert o["tag"] is None and o["reason"] == "newer_installed"


def test_transient_alpha_failure_does_not_block_promotion(tmp_path):
    w = World(tmp_path, [rel("v0.3.0"), rel("v0.4.0", pre=True)])
    w.orch.poll()
    w.offer("alpha", sat="sat-0000a1")
    w.orch.report({"sat_id": "sat-0000a1", "tag": "v0.4.0", "result": "failed", "stage": "download", "error": "net"})
    w.orch.report({"sat_id": "sat-0000a1", "tag": "v0.4.0", "result": "installed", "stage": "", "error": ""})
    w.now += SOAK_S
    assert w.offer("stable")["tag"] == "v0.4.0"


def test_failure_after_promotion_does_not_demote(tmp_path):
    w = World(tmp_path, [rel("v0.3.0"), rel("v0.4.0", pre=True)])
    w.orch.poll()
    w.offer("alpha", sat="sat-0000a1")
    w.orch.report({"sat_id": "sat-0000a1", "tag": "v0.4.0", "result": "installed", "stage": "", "error": ""})
    w.now += SOAK_S
    w.orch.report({"sat_id": "sat-0000a1", "tag": "v0.4.0", "result": "rolled_back", "stage": "health", "error": "e"})
    assert w.offer("stable")["tag"] == "v0.4.0"


def test_exhausted_pushes_once_even_with_duplicate_reports(tmp_path):
    w = World(tmp_path, [rel("v0.3.0")])
    w.orch.poll()
    for _ in range(7):
        w.orch.report({"sat_id": SAT, "tag": "v0.3.0", "result": "failed", "stage": "download", "error": "x"})
    assert len(w.pushed) == 1


def test_package_file_only_for_targets_or_installed(tmp_path):
    w = World(tmp_path, [rel("v0.2.0"), rel("v0.3.0"), rel("v0.4.0", pre=True)])
    w.orch.poll()
    for tag in ("v0.3.0", "v0.4.0", "v0.2.0"):
        p = tmp_path / "ota" / "packages" / tag
        p.mkdir(parents=True, exist_ok=True)
        (p / "satellite.tar.gz").write_bytes(b"x")
    assert w.orch.package_file("v0.3.0", "satellite.tar.gz")
    assert w.orch.package_file("v0.4.0", "satellite.tar.gz") is None  # nobody on alpha
    assert w.orch.package_file("v0.2.0", "satellite.tar.gz") is None


def test_failed_build_backs_off(tmp_path):
    w = World(tmp_path, [rel("v0.3.0")])
    w.build_fails = True
    w.orch.poll()
    w.orch.poll()
    w.offer("stable")
    assert w.built == ["v0.3.0"]
    w.now += 3601
    w.orch.poll()
    assert w.built == ["v0.3.0", "v0.3.0"]


def test_corrupt_state_is_backed_up_and_reset(tmp_path):
    (tmp_path / "state.json").write_text("[]")
    s = st.OtaStore(str(tmp_path / "state.json"), str(tmp_path / "e.ndjson"))
    assert s.data["devices"] == {} and list(tmp_path.glob("state.json.corrupt-*"))
    (tmp_path / "state.json").write_text('{"devices": null}')
    assert st.OtaStore(str(tmp_path / "state.json"), str(tmp_path / "e.ndjson")).data["devices"] == {}


def test_token_book_refuses_to_issue_over_a_corrupt_file(tmp_path):
    b = st.TokenBook(str(tmp_path / "t.json"))
    b.issue("sat-0000b2")
    (tmp_path / "t.json").write_text("{broken")
    with pytest.raises(st.TokenBookError):
        b.issue(SAT)
    assert (tmp_path / "t.json").read_text() == "{broken"
    assert b.check(SAT, "anything") is False



def test_a_pre_ota_release_is_never_a_channel_target(tmp_path):
    old, new = rel("v0.2.1"), rel("v0.3.0", pre=True)
    w = World(tmp_path, [old, new], {new.release.sha: man()})  # v0.2.1 has no ota/release.json
    w.server_at(None)  # the server too predates the OTA
    w.orch.poll()
    assert w.orch.target("stable") is None and w.orch.target("alpha") == "v0.3.0"
    assert w.orch.offer(SAT, "stable", None, 1, "")["tag"] is None
    assert w.orch.package_for_update() is None  # the bootstrap packs the agent's own version instead
    assert w.orch.server_entity()["latest"] is None


def test_an_offer_with_new_wheels_is_heavy(tmp_path):
    w = World(tmp_path, [rel("v0.3.0")])
    w.orch.poll()
    same = w.orch.offer(SAT, "stable", None, 1, "L" * 64)
    other = w.orch.offer(SAT, "stable", None, 1, "x" * 64)
    assert same["wheels_url"] is None and same["heavy"] is False
    assert other["wheels_url"] and other["heavy"] is True


def test_alpha_rollback_that_withdraws_the_release_pushes_once(tmp_path):
    """FINDING 7 (15/11): the withdrawn release left the satellite 'up_to_date' with no retry and no push."""
    w = World(tmp_path, [rel("v0.3.0"), rel("v0.4.0", pre=True)])
    w.orch.poll()
    w.offer("alpha", sat="sat-0000a1")
    w.orch.report({"sat_id": "sat-0000a1", "tag": "v0.4.0", "result": "rolled_back", "stage": "health",
                   "error": "program not answering"})
    assert len(w.pushed) == 1 and "v0.4.0" in w.pushed[0] and "program not answering" in w.pushed[0]
    w.orch.report({"sat_id": "sat-0000a1", "tag": "v0.4.0", "result": "rolled_back", "stage": "health", "error": "e"})
    assert len(w.pushed) == 1


def test_a_resent_report_counts_once(tmp_path):
    # the VM processed it but the reply was lost: the satellite sends the same report again (Task 14 N4)
    w = World(tmp_path, [rel("v0.3.0")])
    w.orch.poll()
    body = {"sat_id": SAT, "tag": "v0.3.0", "result": "failed", "stage": "download", "error": "x", "id": "a" * 32}
    assert w.orch.report(dict(body)) == {"ok": True}
    assert w.orch.report(dict(body)) == {"ok": True, "duplicate": True}
    assert w.orch.store.attempts(SAT).failures == 1
    w.orch.report({**body, "id": "b" * 32})
    assert w.orch.store.attempts(SAT).failures == 2


def test_a_report_id_must_be_hex(tmp_path):
    w = World(tmp_path, [rel("v0.3.0")])
    import pytest
    with pytest.raises(ValueError, match="id"):
        w.orch.report({"sat_id": SAT, "tag": "v0.3.0", "result": "failed", "id": "../x"})


# ---- automatic promotion on GitHub
class FakePublisher:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def promote(self, tag):
        self.calls.append(tag)
        if self.fail:
            raise gh.GitHubError(self.fail)


def _soaked_world(tmp_path, releases, publisher, tag="v0.4.0"):
    w = World(tmp_path, releases)
    w.orch.publisher = publisher
    w.orch.poll()
    w.offer("alpha", sat="sat-0000a1")
    w.orch.report({"sat_id": "sat-0000a1", "tag": tag, "result": "installed", "stage": "", "error": ""})
    w.now += SOAK_S
    return w


def test_soaked_prerelease_is_promoted_once(tmp_path):
    pub = FakePublisher()
    w = _soaked_world(tmp_path, [rel("v0.3.0"), rel("v0.4.0", pre=True)], pub)
    w.orch.poll()
    w.orch.poll()
    assert pub.calls == ["v0.4.0"]
    assert w.pushed == ["F.R.I.D.A.Y. v0.4.0 passou na prova do alpha e agora é a versão stable."]
    assert w.store.data["promotions"]["v0.4.0"]["result"] == "promoted"


def test_not_yet_soaked_prerelease_is_not_promoted(tmp_path):
    pub = FakePublisher()
    w = _soaked_world(tmp_path, [rel("v0.3.0"), rel("v0.4.0", pre=True)], pub)
    w.now -= SOAK_S // 2
    w.orch.poll()
    assert pub.calls == []


def test_older_soaked_prerelease_is_not_promoted_over_a_newer_stable(tmp_path):
    pub = FakePublisher()
    w = _soaked_world(tmp_path, [rel("v0.4.0", pre=True), rel("v0.5.0")], pub)
    w.orch.poll()
    assert pub.calls == [] and w.pushed == []
    assert w.store.data["promotions"]["v0.4.0"]["result"] == "superseded"


def test_failed_promotion_retries_after_an_hour(tmp_path):
    pub = FakePublisher(fail="http 502")
    w = _soaked_world(tmp_path, [rel("v0.3.0"), rel("v0.4.0", pre=True)], pub)
    w.orch.poll()
    w.orch.poll()
    assert pub.calls == ["v0.4.0"] and w.pushed == []
    w.now += orc.PROMOTE_RETRY_S
    pub.fail = None
    w.orch.poll()
    assert pub.calls == ["v0.4.0", "v0.4.0"]
    assert w.store.data["promotions"]["v0.4.0"]["result"] == "promoted" and len(w.pushed) == 1


def test_no_publisher_is_a_noop(tmp_path):
    w = _soaked_world(tmp_path, [rel("v0.3.0"), rel("v0.4.0", pre=True)], None)
    w.orch.poll()
    assert "promotions" not in w.store.data or w.store.data["promotions"] == {}


def test_promotion_survives_restart_without_repeating(tmp_path):
    pub = FakePublisher()
    w = _soaked_world(tmp_path, [rel("v0.3.0"), rel("v0.4.0", pre=True)], pub)
    w.orch.poll()
    w2 = World(tmp_path, [rel("v0.3.0"), rel("v0.4.0", pre=True)])
    w2.orch.publisher = pub
    w2.now = w.now
    w2.orch.poll()
    assert pub.calls == ["v0.4.0"]


def _soak_all(w, *tags):
    for t in tags:
        w.store.data["soak"][t] = {"started_at": w.now - SOAK_S - 1, "failed": False}


def test_two_soaked_prereleases_promote_only_the_newest(tmp_path):
    pub = FakePublisher()
    w = World(tmp_path, [rel("v0.3.0"), rel("v0.4.0", pre=True), rel("v0.4.1", pre=True)])
    w.orch.publisher = pub
    w.orch.poll()
    _soak_all(w, "v0.4.0", "v0.4.1")
    w.orch.poll()
    w.orch.poll()
    assert pub.calls == ["v0.4.1"]
    assert w.store.data["promotions"]["v0.4.1"]["result"] == "promoted"
    assert w.store.data["promotions"]["v0.4.0"]["result"] == "superseded"


def test_a_failed_older_promotion_is_superseded_after_a_newer_one_succeeds(tmp_path):
    pub = FakePublisher(fail="http 502")
    w = World(tmp_path, [rel("v0.3.0"), rel("v0.4.0", pre=True), rel("v0.4.1", pre=True)])
    w.orch.publisher = pub
    w.orch.poll()
    _soak_all(w, "v0.4.0")  # only the older one has soaked so far
    w.orch.poll()
    assert pub.calls == ["v0.4.0"] and "v0.4.0" not in w.store.data["promotions"]
    _soak_all(w, "v0.4.1")  # the newer one finishes its soak; GitHub recovers
    pub.fail = None
    w.orch.poll()
    assert pub.calls[-1] == "v0.4.1"
    w.now += orc.PROMOTE_RETRY_S
    w.orch.poll()
    assert pub.calls == ["v0.4.0", "v0.4.1"]  # v0.4.0 is never promoted after v0.4.1 ("latest" must not go back)
    assert w.store.data["promotions"]["v0.4.0"]["result"] == "superseded"
    assert w.store.data["promotions"]["v0.4.1"]["result"] == "promoted"


def test_a_tag_promoted_by_us_counts_as_stable(tmp_path):
    pub = FakePublisher()
    # the stored release flag stays pre-release until a full poll, but v0.4.0 was promoted by this agent
    w = World(tmp_path, [rel("v0.3.0"), rel("v0.3.5", pre=True), rel("v0.4.0", pre=True)])
    w.orch.publisher = pub
    w.orch.poll()
    w.store.data["promotions"] = {"v0.4.0": {"at": w.now, "result": "promoted"}}
    _soak_all(w, "v0.3.5")
    w.orch.poll()
    assert pub.calls == []
    assert w.store.data["promotions"]["v0.3.5"]["result"] == "superseded"


# ---- server-first gate, built_by, offered marker (plan 2026-11-18 Task 3)
from friday_server_agent.ota import packages as pk  # noqa: E402


def _ota_root(tmp_path):
    return str(tmp_path / "ota")


def test_offer_waits_for_server_install(tmp_path):
    w = World(tmp_path, [rel("v0.3.0"), rel("v0.4.0")])
    w.server_at("v0.3.0")
    w.orch.poll()
    o = w.offer("stable", installed="v0.3.0")
    assert o["tag"] is None and o["reason"] == "waiting_server"
    w.server_at("v0.4.0")
    assert w.offer("stable", installed="v0.3.0")["tag"] == "v0.4.0"


def test_offer_marks_package_offered(tmp_path):
    w = World(tmp_path, [rel("v0.3.0")])
    w.orch.poll()
    assert not pk.is_offered(_ota_root(tmp_path), "v0.3.0")
    assert w.offer("stable")["tag"] == "v0.3.0"
    assert pk.is_offered(_ota_root(tmp_path), "v0.3.0")


def test_stale_package_rebuilt_when_not_held(tmp_path):
    w = World(tmp_path, [rel("v0.4.0")], own="v0.3.0")
    w.orch.poll()
    assert w.built == ["v0.4.0"] and w.orch.meta["v0.4.0"]["built_by"] == "v0.3.0"
    w.agent_at("v0.4.0")
    assert w.offer("stable", installed="v0.3.0")["tag"] == "v0.4.0"
    assert w.built == ["v0.4.0", "v0.4.0"] and w.orch.meta["v0.4.0"]["built_by"] == "v0.4.0"


def test_installed_package_without_built_by_is_kept(tmp_path):
    w = World(tmp_path, [rel("v0.4.0")])
    w.orch.meta["v0.4.0"] = {"tag": "v0.4.0", "sha": "x", "server": {"path": "a.tar"},
                             "satellite": {"path": "p", "sha256": "k" * 64, "size": 10, "lock_sha256": "L" * 64,
                                           "wheels": "/w"}}  # built by the v0.3.9 agent: no built_by
    w.orch.poll()
    w.offer("stable", installed="v0.4.0", sat="sat-0000b2")  # a device already runs it
    o = w.offer("stable", installed="v0.3.0")
    assert o["tag"] == "v0.4.0" and o["sha256"] == "k" * 64 and w.built == []


class FakeImages:
    def __init__(self):
        self.started = []

    def status(self):
        return {"status": None}

    def start(self, tag, sha):
        self.started.append(tag)
        return {"ok": True}


def test_older_agent_refuses_satellite_package(tmp_path):
    w = World(tmp_path, [rel("v0.4.0")], own="v0.3.0")
    w.orch.images = FakeImages()
    w.store.data["server_ota"]["image_auto"] = "v0.4.0"  # keep the poll's image step out of the way
    w.orch.poll()
    o = w.offer("stable", installed="v0.3.0")
    assert o["tag"] is None and o["reason"] == "server_older"
    assert w.orch.image_build("v0.4.0") == {"ok": False, "error": "server_older"}
    assert w.orch.images.started == []
    assert w.orch.package_for_update() is None
    assert not pk.is_offered(_ota_root(tmp_path), "v0.4.0")


class FakeInstaller:
    def __init__(self):
        self.applied = []

    def recover(self):
        return None

    def absorb_self_result(self):
        return None

    def prune_snapshots(self):
        pass

    def apply(self, tag, sha, man, manual=False):
        self.applied.append(tag)
        return {"result": "deferred"}


def test_server_step_still_builds_archive_with_older_agent(tmp_path):
    w = World(tmp_path, [rel("v0.3.0"), rel("v0.4.0")], own="v0.3.0")
    w.server_at("v0.3.0")
    w.orch.installer = FakeInstaller()
    w.orch.poll()
    assert w.orch.installer.applied == ["v0.4.0"]
    assert "v0.4.0" in w.built and w.orch.meta["v0.4.0"]["server"]["path"]


def test_manual_satellite_install_requests_server_install(tmp_path):
    w = World(tmp_path, [rel("v0.3.0"), rel("v0.4.0")])
    w.server_at("v0.3.0")
    w.orch.poll()
    w.store.data["server_ota"]["attempts"] = {"version": "v0.4.0", "failures": 5, "exhausted": True}
    w.offer("stable", installed="v0.3.0")
    assert w.orch.install(SAT) == "v0.4.0"
    so = w.store.data["server_ota"]
    assert so["install_requested"] is True and so["attempts"] is None
    events = [json.loads(line) for line in (tmp_path / "events.ndjson").read_text().splitlines()]
    assert any(e["device"] == "server" and e["tag"] == "v0.4.0" and e["event"] == "install_requested" for e in events)


def test_manual_satellite_install_leaves_an_up_to_date_server_alone(tmp_path):
    w = World(tmp_path, [rel("v0.4.0")])
    w.orch.poll()
    w.orch.install(SAT)
    assert not w.store.data["server_ota"].get("install_requested")


def test_waiting_server_listed_after_an_hour_without_telegram(tmp_path):
    w = World(tmp_path, [rel("v0.3.0"), rel("v0.4.0")])
    w.server_at("v0.3.0")
    w.orch.poll()
    t0 = w.now
    assert w.offer("stable", installed="v0.3.0")["reason"] == "waiting_server"
    w.now += 59 * 60
    w.offer("stable", installed="v0.3.0")
    assert w.orch.waiting_server() == {}
    w.now += 2 * 60
    w.offer("stable", installed="v0.3.0")
    assert w.orch.waiting_server() == {SAT: {"tag": "v0.4.0", "since": t0, "why": "aguardando a janela de instalação"}}
    assert w.pushed == []


def test_waiting_server_since_clears_on_another_answer(tmp_path):
    w = World(tmp_path, [rel("v0.3.0"), rel("v0.4.0")])
    w.server_at("v0.3.0")
    w.orch.poll()
    w.offer("stable", installed="v0.3.0")
    assert w.store.device(SAT).get("waiting_server_since") == w.now
    w.now += 3601
    w.server_at("v0.4.0")
    w.offer("stable", installed="v0.3.0")
    assert w.store.device(SAT).get("waiting_server_since") is None
    assert w.orch.waiting_server() == {}


def test_waiting_reason_says_auto_install_is_off(tmp_path):
    w = World(tmp_path, [rel("v0.3.0"), rel("v0.4.0")])
    w.server_at("v0.3.0")
    w.orch.set_auto(False)
    w.orch.poll()
    w.offer("stable", installed="v0.3.0")
    w.now += 3601
    w.offer("stable", installed="v0.3.0")
    assert w.orch.waiting_server()[SAT]["why"] == "atualização automática desligada"


# ---- review findings (2026-11-18): what counts as held, which tags rebuild, describe versions
def _legacy(w, tag="v0.4.0"):
    """A package packed by the v0.3.9 agent: no built_by."""
    w.orch.meta[tag] = {"tag": tag, "sha": "x", "server": {"path": "a.tar"},
                        "satellite": {"path": "p", "sha256": "k" * 64, "size": 10, "lock_sha256": "L" * 64,
                                      "wheels": "/w"}}


def test_legacy_package_held_by_install_requested_event(tmp_path):
    w = World(tmp_path, [rel("v0.4.0")])
    _legacy(w)
    w.orch.poll()
    w.store.event("sat-0000b2", "v0.4.0", "install_requested")
    o = w.offer("stable", installed="v0.3.0")
    assert o["tag"] == "v0.4.0" and o["sha256"] == "k" * 64 and w.built == []


@pytest.mark.parametrize("field", ["attempts", "last_report"])
def test_legacy_package_held_by_device_last_attempt(tmp_path, field):
    w = World(tmp_path, [rel("v0.4.0")])
    _legacy(w)
    w.orch.poll()
    d = w.store.device("sat-0000b2")
    d[field] = ({"version": "v0.4.0", "failures": 1} if field == "attempts"
                else {"at": 1.0, "tag": "v0.4.0", "result": "failed", "stage": "download", "error": "x"})
    o = w.offer("stable", installed="v0.3.0")
    assert o["tag"] == "v0.4.0" and o["sha256"] == "k" * 64 and w.built == []


def test_package_file_marks_offered(tmp_path):
    w = World(tmp_path, [rel("v0.4.0")])
    w.orch.poll()
    meta = w.orch.meta["v0.4.0"]
    Path(meta["satellite"]["path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(meta["satellite"]["path"]).write_bytes(b"x")
    assert not pk.is_offered(_ota_root(tmp_path), "v0.4.0")
    assert w.orch.package_file("v0.4.0", "satellite.tar.gz") == meta["satellite"]["path"]
    assert pk.is_offered(_ota_root(tmp_path), "v0.4.0")


def test_pre_contract_tag_without_built_by_never_rebuilt(tmp_path):
    w = World(tmp_path, [rel("v0.3.9")])
    _legacy(w, "v0.3.9")
    w.orch.poll()
    o = w.offer("stable", installed="v0.3.0")
    assert o["tag"] == "v0.3.9" and o["sha256"] == "k" * 64 and w.built == []


def test_pre_contract_tag_kept_even_by_older_agent(tmp_path):
    w = World(tmp_path, [rel("v0.3.9")], own="v0.3.8")
    _legacy(w, "v0.3.9")
    w.orch.poll()
    assert w.offer("stable", installed="v0.3.0")["tag"] == "v0.3.9" and w.built == []


def test_rebuild_is_satellite_only(tmp_path):
    w = World(tmp_path, [rel("v0.4.0")], own="v0.3.0")
    w.orch.poll()
    w.agent_at("v0.4.0")
    w.offer("stable", installed="v0.3.0")
    assert w.built == ["v0.4.0", "v0.4.0"] and w.rebuilds == [True]


def test_own_version_describe_string_parsed(tmp_path):
    w = World(tmp_path, [rel("v0.4.0")], own="v0.3.0")
    w.orch.poll()
    w.agent_at("v0.4.0-3-gabc1234")  # deploy.sh writes `git describe`
    assert w.offer("stable", installed="v0.3.0")["tag"] == "v0.4.0"
    assert w.built == ["v0.4.0", "v0.4.0"]


def test_built_by_describe_string_counts_as_fresh(tmp_path):
    w = World(tmp_path, [rel("v0.4.0")], own="v0.4.0-1-gdeadbee")
    w.orch.poll()
    assert w.orch.meta["v0.4.0"]["built_by"] == "v0.4.0-1-gdeadbee"
    w.agent_at("v0.5.0")
    assert w.offer("stable", installed="v0.3.0")["tag"] == "v0.4.0" and w.built == ["v0.4.0"]
