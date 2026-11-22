from friday_agent_core.ota import channels as c
from friday_agent_core.ota.constants import SOAK_S

R = lambda t, ci="green", pre=False: c.Release(tag=t, sha=t * 3, published_at=0.0, ci=ci, prerelease=pre)

def test_version_key_orders_semver_not_text():
    assert sorted(["v0.10.0", "v0.9.1", "v0.2.0"], key=c.version_key) == ["v0.2.0", "v0.9.1", "v0.10.0"]

def test_eligible_drops_red_pending_and_bad_tags():
    out = c.eligible([R("v0.3.0", "red"), R("v0.2.0"), R("nightly"), R("v0.4.0", "pending")])
    assert [r.tag for r in out] == ["v0.2.0"]

def test_normal_release_is_stable_right_away():
    assert c.stable_tag([R("v0.2.0"), R("v0.3.0")], {}, now=0) == "v0.3.0"

def test_prerelease_stays_out_of_stable_until_soaked():
    rel = [R("v0.2.0"), R("v0.3.0", pre=True)]
    assert c.stable_tag(rel, {}, now=10 * SOAK_S) == "v0.2.0"  # never soaked: no alpha device proved it
    soak = {"v0.3.0": c.SoakRecord(started_at=1000.0, failed=False)}
    assert c.stable_tag(rel, soak, now=1000 + SOAK_S - 1) == "v0.2.0"
    assert c.stable_tag(rel, soak, now=1000 + SOAK_S) == "v0.3.0"

def test_failed_prerelease_never_promotes_and_alpha_skips_it():
    soak = {"v0.3.0": c.SoakRecord(started_at=0.0, failed=True)}
    rel = [R("v0.2.0"), R("v0.3.0", pre=True)]
    assert c.stable_tag(rel, soak, now=10 * SOAK_S) == "v0.2.0"
    assert c.target_for("alpha", rel, soak, now=0) == "v0.2.0"

def test_alpha_gets_newest_of_any_kind():
    assert c.target_for("alpha", [R("v0.2.0"), R("v0.3.0", pre=True)], {}, now=0) == "v0.3.0"
    assert c.target_for("alpha", [R("v0.3.0", pre=True), R("v0.4.0")], {}, now=0) == "v0.4.0"

def test_newer_normal_release_supersedes_unproven_prerelease():
    rel = [R("v0.2.0"), R("v0.3.0", pre=True), R("v0.4.0")]
    assert c.target_for("stable", rel, {}, now=0) == "v0.4.0"

def test_unknown_channel_is_stable():
    assert c.target_for("beta?", [R("v0.2.0"), R("v0.3.0", pre=True)], {}, now=0) == "v0.2.0"

def test_server_follows_alpha_when_any_satellite_is_alpha():
    assert c.server_channel(["stable", "alpha"]) == "alpha" and c.server_channel(["stable"]) == "stable"
    assert c.server_channel([]) == "stable"


def test_promoted_false_when_blocked():
    rel = R("v0.3.0", pre=True)
    soak = {"v0.3.0": c.SoakRecord(1000.0, False, blocked=True)}
    assert c.promoted(rel, soak, now=1000 + 10 * SOAK_S) is False
    soak = {"v0.3.0": c.SoakRecord(1000.0, False)}  # blocked defaults to False
    assert c.promoted(rel, soak, now=1000 + SOAK_S) is True
