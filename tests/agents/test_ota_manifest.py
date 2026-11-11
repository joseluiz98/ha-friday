import json, pathlib, pytest
from friday_agent_core.ota import constants as k, manifest as m

ROOT = pathlib.Path(__file__).resolve().parents[2]

def test_repo_manifest_parses():
    man = m.load(str(ROOT / "ota" / "release.json"))
    assert man.schema == 1 and man.protocol["update"] == k.UPDATE_PROTOCOL
    assert "container" in man.heavy and set(m.COMPONENT_ORDER) == set(man.components)

def test_changed_components_by_prefix():
    man = m.load(str(ROOT / "ota" / "release.json"))
    assert m.changed_components(man, ["satellite/program/maestro.py"]) == ["satellite"]
    assert m.changed_components(man, ["agents/common/friday_agent_core/server.py"]) == ["satellite", "server-agent"]
    assert m.changed_components(man, ["satellite/programX.py", "README.md"]) == []

@pytest.mark.parametrize("bad,key", [
    ({"schema": 2}, "schema"),
    ({"components": {}}, "components"),
    ({"components": {"x": ["../etc"]}}, "x"),
    ({"heavy": ["nope"]}, "nope"),
    ({"protocol": {"satellite": 0}}, "satellite"),
])
def test_invalid_manifest_names_the_key(bad, key):
    base = json.loads((ROOT / "ota" / "release.json").read_text())
    base.update(bad)
    with pytest.raises(m.ManifestError, match=key):
        m.parse(base)

def test_constants_match_spec():
    assert k.RETRY_DELAYS_S == (900, 3600, 21600, 86400) and k.SOAK_S == 172800
    assert k.PUSH_WINDOW == ("07:30", "22:00") and k.HEALTH_TIMEOUT_S == 180 and k.HEAVY_IDLE_S == 1800
