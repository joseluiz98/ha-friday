"""Frozen contract of the update channel, protocol v1 (spec 2026-11-10 §8; plan Task 19).

The JSON examples in update_v1/ are the messages an installed satellite and the server exchange. The code of today
must still produce/accept exactly those fields: a NEW key is allowed (superset), a removed or renamed key fails with
"update protocol v1 broken: <key>". Never edit the examples to make a test pass; a breaking change is protocol v2."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agents" / "server"))
sys.path.insert(0, str(ROOT / "agents" / "satellite"))
from friday_server_agent.ota.http import offer_payload, parse_report  # noqa: E402
from friday_satellite_agent.ota import parse_package_manifest  # noqa: E402

from tests.agents.test_satellite_ota import World  # noqa: E402

EXAMPLES = Path(__file__).resolve().parent / "update_v1"


def load(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def broken(key) -> str:
    return f"update protocol v1 broken: {key}"


def assert_superset(produced: dict, frozen: dict) -> None:
    """Every frozen key is present, with the same JSON type (None may stand for a string: an absent URL)."""
    for key, value in frozen.items():
        assert key in produced, broken(key)
        if value is not None and produced[key] is not None:
            assert type(produced[key]) is type(value), f"{broken(key)} (type {type(produced[key]).__name__})"


def test_offer_response_with_a_target():
    frozen = load("offer_response.json")
    produced = offer_payload(frozen["tag"], sat_id="sat-000000", sha=frozen["sha"], sha256=frozen["sha256"],
                             size=frozen["size"], wheels=True, heavy=frozen["heavy"],
                             install_requested=frozen["install_requested"], auto_install=frozen["auto_install"],
                             reason=frozen["reason"])
    assert_superset(produced, frozen)
    assert produced["url"].startswith("/") and "sat_id=sat-000000" in produced["url"]
    assert produced["wheels_url"] and "sat_id=sat-000000" in produced["wheels_url"]


def test_offer_response_without_a_target():
    frozen = load("offer_none_response.json")
    produced = offer_payload(None, reason=frozen["reason"], auto_install=frozen["auto_install"])
    assert_superset(produced, frozen)
    assert produced["tag"] is None


def test_report_request_is_accepted():
    frozen = load("report_request.json")
    try:
        parsed = parse_report(dict(frozen))
    except ValueError as err:
        pytest.fail(f"{broken(err)}")
    assert_superset(parsed, frozen)
    assert {k: parsed[k] for k in frozen} == frozen


def test_package_manifest_is_accepted():
    frozen = load("package_manifest.json")
    try:
        parsed = parse_package_manifest(dict(frozen))
    except ValueError as err:
        pytest.fail(f"{broken(err)}")
    for key in frozen:
        if key != "schema":  # the schema number is checked by the parser, not echoed
            assert key in parsed, broken(key)
            assert parsed[key] == frozen[key], broken(key)


@pytest.mark.parametrize("key", ["sat_id", "tag", "result"])
def test_report_parser_notices_a_renamed_key(key):
    bad = load("report_request.json")
    bad[key + "_x"] = bad.pop(key)
    with pytest.raises(ValueError, match=key):
        parse_report(bad)


@pytest.mark.parametrize("key", ["schema", "tag", "sha"])
def test_manifest_parser_notices_a_renamed_key(key):
    bad = load("package_manifest.json")
    bad[key + "_x"] = bad.pop(key)
    with pytest.raises(ValueError, match=key):
        parse_package_manifest(bad)


async def test_offer_request_query_keys(tmp_path):
    """What the satellite sends on GET /v1/ota/offer (OtaClient.step builds the query), plain and after a button press."""
    frozen = load("offer_request.json")
    w = World(tmp_path)
    w.http.offer = {"tag": None, "reason": "up_to_date"}
    await w.c.step()
    plain = next(c[2] for c in w.http.calls if c[0] == "offer")
    for key in frozen["query"]:
        assert key in plain, broken(key)
    assert all(isinstance(v, str) for v in plain.values()), "the query is sent as strings"
    w.http.calls.clear()
    assert w.c.request_install() == {"ok": True}
    await w.c.step()
    pressed = next(c[2] for c in w.http.calls if c[0] == "offer")
    for key in list(frozen["query"]) + list(frozen["optional_query"]):
        assert key in pressed, broken(key)
    assert pressed["install"] == frozen["optional_query"]["install"]


def test_the_real_package_carries_the_frozen_manifest(tmp_path, monkeypatch):
    """The producer, not just the reader: the manifest.json the server packs has every frozen key, of the same type,
    and the satellite's reader accepts it."""
    import tarfile
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "server"))
    from friday_server_agent import provisioning as pv
    from friday_server_agent.ota import packages as pk
    from tests.agents.test_ota_packages import _extra_tree
    from tests.agents.test_server_provisioning import _make_sources
    archive, _ = _make_sources(tmp_path / "haos", monkeypatch)
    _extra_tree(tmp_path / "haos")
    info = pk.build_satellite(archive, "v9.9.9", "0" * 40, pv._vault(), str(tmp_path / "pkg"))
    with tarfile.open(info["path"]) as t:
        produced = json.load(t.extractfile("manifest.json"))
    for key, value in load("package_manifest.json").items():
        assert key in produced, broken(key)
        assert type(produced[key]) is type(value), f"{broken(key)} (type {type(produced[key]).__name__})"
    parse_package_manifest(produced)
