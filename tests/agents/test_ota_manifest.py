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

def _installed_server_dirs():
    """The /opt/friday/<d> dirs server/install.sh copies (its `for d in ...; do` loop)."""
    import re
    text = (ROOT / "server" / "install.sh").read_text()
    return re.search(r"^for d in ([a-z ]+); do$", text, re.M).group(1).split()

def test_every_installed_server_dir_is_in_the_server_component():
    """Final review 16/11: server/browser was installed but missing from components.server, so a release that only
    changed it would not be installed by the OTA (plan() -> touches())."""
    man = m.load(str(ROOT / "ota" / "release.json"))
    dirs = _installed_server_dirs()
    assert "browser" in dirs
    for d in dirs:
        assert f"server/{d}" in man.components["server"], d
    assert m.changed_components(man, ["server/browser/browser_idle.py"]) == ["server"]

def test_drift_checks_every_installed_server_dir():
    import importlib.util
    spec = importlib.util.spec_from_file_location("drift", ROOT / "scripts" / "drift.py")
    drift = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(drift)
    prefixes = {p[0] for p in drift.PLANS["vm"]}
    for d in _installed_server_dirs():
        assert f"server/{d}/" in prefixes, d

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
