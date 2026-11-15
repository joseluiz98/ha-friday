import json, pytest
from friday_agent_core.ota import migrations as mg

def mk(folder, name, body="exit 0"):
    (folder / name).write_text("#!/bin/bash\n" + body + "\n")

def test_discover_orders_and_filters(tmp_path):
    for n in ("0002_b.sh", "0001_a.sh", "README.md", "0003_c.py", "4_bad.sh"):
        (tmp_path / n).write_text("")
    assert mg.discover(str(tmp_path)) == ["0001_a.sh", "0002_b.sh", "0003_c.py"]
    assert mg.discover(str(tmp_path / "none")) == []

def test_duplicate_number_refused(tmp_path):
    mk(tmp_path, "0001_a.sh"); mk(tmp_path, "0001_b.sh")
    with pytest.raises(mg.MigrationError):
        mg.discover(str(tmp_path))

def test_skipping_versions_runs_every_pending_in_order(tmp_path):
    out = tmp_path / "out.txt"
    for n in ("0002_x.sh", "0003_y.sh", "0004_z.sh", "0005_w.sh"):
        mk(tmp_path, n, f'echo {n} >> "{out}"')
    rec = tmp_path / "rec.json"
    rec.write_text(json.dumps({"0001_base.sh": {"at": 1, "tag": "v0.2.0"}}))
    ran = mg.run_pending(str(tmp_path), str(rec), {"FRIDAY_TAG": "v0.6.0"})
    assert ran == ["0002_x.sh", "0003_y.sh", "0004_z.sh", "0005_w.sh"]
    assert out.read_text().split() == ran
    assert mg.run_pending(str(tmp_path), str(rec), {"FRIDAY_TAG": "v0.6.0"}) == []
    assert mg.load_done(str(rec))["0005_w.sh"]["tag"] == "v0.6.0"

def test_failure_stops_and_is_not_recorded(tmp_path):
    mk(tmp_path, "0001_ok.sh"); mk(tmp_path, "0002_bad.sh", "exit 3"); mk(tmp_path, "0003_never.sh")
    rec = tmp_path / "rec.json"
    with pytest.raises(mg.MigrationError) as e:
        mg.run_pending(str(tmp_path), str(rec), {"FRIDAY_TAG": "v1.0.0"})
    assert e.value.name == "0002_bad.sh"
    assert set(mg.load_done(str(rec))) == {"0001_ok.sh"}

def test_env_reaches_migration(tmp_path):
    out = tmp_path / "env.txt"
    mk(tmp_path, "0001_env.sh", f'echo "$FRIDAY_TAG $FRIDAY_RELEASE_DIR" > "{out}"')
    mg.run_pending(str(tmp_path), str(tmp_path / "r.json"), {"FRIDAY_TAG": "v1.2.3", "FRIDAY_RELEASE_DIR": "/x"})
    assert out.read_text().strip() == "v1.2.3 /x"
