"""Satellite OTA client (plan 2026-11-10 Task 14)."""

import hashlib
import io
import json
import os
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "satellite"))
from friday_satellite_agent import ota  # noqa: E402
from friday_agent_core.ota.constants import HEAVY_IDLE_S, RETRY_DELAYS_S  # noqa: E402

SAT = "sat-abc123"
PKG = b"package bytes"
PKG_SHA = hashlib.sha256(PKG).hexdigest()
IDLE = {"estado": "parada", "timers": [], "alarme": None, "musica": False, "pendentes": 0, "fila_entregas": 0,
        "ultima_abertura": 100, "update_pendente": False}


def offer(tag="v0.3.0", **kw):
    base = {"tag": tag, "sha": "a" * 40, "sha256": PKG_SHA, "size": len(PKG),
            "url": f"/v1/ota/package/{tag}/satellite.tar.gz?sat_id={SAT}", "wheels_url": None, "heavy": False,
            "install_requested": False, "auto_install": True, "reason": ""}
    return {**base, **kw}


class Http:
    def __init__(self, offer_=None, body=PKG):
        self.offer, self.body, self.calls, self.reports = offer_ or offer(), body, [], []
        self.down = False

    async def get_json(self, url, params, token):
        self.calls.append(("offer", url, dict(params), token))
        if self.down:
            raise OSError("down")
        return self.offer

    async def post_json(self, url, body, token):
        if self.down:
            raise OSError("down")
        self.reports.append(body)
        return {"ok": True}

    async def download(self, url, token, dest):
        self.calls.append(("download", url))
        Path(dest).write_bytes(self.body)
        return hashlib.sha256(self.body).hexdigest()


class Program:
    def __init__(self, status=None):
        self.st, self.commands, self.music_asked = dict(status or IDLE), [], []

    async def status(self, music=False):
        self.music_asked.append(music)
        return dict(self.st)

    async def command(self, body):
        self.commands.append(body)
        if body.get("acao") == "update_pending":
            self.st["update_pendente"] = body["ligado"]
        return {"ok": True}


class Helper:
    def __init__(self, rc=None):
        self.calls, self.rc = [], rc or {}

    async def __call__(self, args):
        self.calls.append(list(args))
        rc, out = self.rc.get(args[0], (0, ""))
        if args[0] == "stage":
            assert os.path.isfile(args[1])  # the package is there while the helper stages it
        return rc, out


class World:
    def __init__(self, tmp: Path, http=None, program=None, helper=None, creds=True, current="v0.2.1"):
        self.tmp, self.now = tmp, 10_000.0
        self.http, self.program, self.helper = http or Http(), program or Program(), helper or Helper()
        etc = tmp / "etc"
        etc.mkdir()
        if creds:
            (etc / "ota_token").write_text("tok\n")
            (etc / "ota.json").write_text(json.dumps({"channel": "alpha", "url": "http://vm:8801/"}))
        rel = tmp / "opt" / "releases"
        if current:
            (rel / current).mkdir(parents=True)
            (rel / current / "manifest.json").write_text(json.dumps({"lock_sha256": "b" * 64}))
            os.symlink(rel / current, tmp / "opt" / "current")
        (tmp / "helper").write_text("#")
        self.sudo = []
        self.c = ota.OtaClient(SAT, self.program, self.http, self.helper, clock=lambda: self.now,
                               state_path=str(tmp / "state.json"), result_path=str(tmp / "result.json"),
                               downloads=str(tmp / "downloads"), channel_file=str(etc / "ota.json"),
                               token_file=str(etc / "ota_token"), current=str(tmp / "opt" / "current"),
                               helper_path=str(tmp / "helper"), sudo=self.fake_sudo)

    async def fake_sudo(self, argv):
        self.sudo.append(argv)
        return 0

    def helper_result(self, **kw):
        (self.tmp / "result.json").write_text(json.dumps(kw))

    def verbs(self):
        return [c[0] for c in self.helper.calls]


async def test_new_offer_downloads_stages_and_installs_when_idle(tmp_path):
    w = World(tmp_path)
    await w.c.step()
    assert w.http.calls[1] == ("download", f"http://vm:8801/v1/ota/package/v0.3.0/satellite.tar.gz?sat_id={SAT}")
    stage = w.helper.calls[0]
    assert stage[0] == "stage" and stage[2] == "v0.3.0" and "--wheels" not in stage
    assert w.helper.calls[1] == ["activate", "v0.3.0"]
    assert {"acao": "ring", "state": "updating"} in w.program.commands
    assert not list((tmp_path / "downloads").iterdir())  # the helper copied it: nothing big stays
    assert json.loads((tmp_path / "state.json").read_text())["activating"] == "v0.3.0"


async def test_channel_installed_and_lock_go_in_the_offer(tmp_path):
    w = World(tmp_path)
    w.http.offer = {"tag": None, "reason": "up_to_date"}
    await w.c.step()
    _, url, params, token = w.http.calls[0]
    assert url == "http://vm:8801/v1/ota/offer" and token == "tok"
    assert params == {"sat_id": SAT, "channel": "alpha", "installed": "v0.2.1", "protocol": "1",
                      "lock_sha256": "b" * 64}


async def test_old_layout_reports_no_version_and_no_lock(tmp_path):
    w = World(tmp_path, current=None)
    w.http.offer = {"tag": None, "reason": "up_to_date"}
    await w.c.step()
    assert w.http.calls[0][2]["installed"] == "" and w.http.calls[0][2]["lock_sha256"] == ""


async def test_wheels_are_downloaded_and_staged_when_offered(tmp_path):
    w = World(tmp_path, Http(offer(wheels_url="/v1/ota/package/v0.3.0/wheels.tar.gz?sat_id=" + SAT)))
    await w.c.step()
    stage = w.helper.calls[0]
    assert stage[3] == "--wheels" and stage[4].endswith("v0.3.0-wheels.tar.gz")


async def test_auto_install_off_only_downloads_and_lights_pending(tmp_path):
    w = World(tmp_path, Http(offer(auto_install=False)))
    await w.c.step()
    assert w.verbs() == ["stage"]
    assert {"acao": "update_pending", "ligado": True} in w.program.commands
    assert w.c.entity_state()["ota"]["latest"] == "v0.3.0"
    await w.c.step()  # already staged: no second download, pending not re-sent while the program shows it
    assert w.verbs() == ["stage"] and [c[0] for c in w.http.calls].count("download") == 1
    assert w.program.commands.count({"acao": "update_pending", "ligado": True}) == 1
    w.program.st["update_pendente"] = False  # the program restarted and forgot it
    await w.c.step()
    assert w.program.commands.count({"acao": "update_pending", "ligado": True}) == 2


@pytest.mark.parametrize("busy", [{"estado": "conversa"}, {"timers": [{"nome": "x"}]}, {"alarme": "x"},
                                  {"musica": True}, {"pendentes": 1}])
async def test_a_busy_satellite_defers_the_automatic_install(tmp_path, busy):
    w = World(tmp_path, program=Program({**IDLE, **busy}))
    await w.c.step()
    assert w.verbs() == ["stage"] and {"acao": "update_pending", "ligado": True} in w.program.commands
    assert w.program.music_asked[-1] is True


async def test_heavy_needs_thirty_minutes_of_continuous_idle(tmp_path):
    w = World(tmp_path, Http(offer(heavy=True)))
    await w.c.step()
    assert "activate" not in w.verbs()
    w.now += HEAVY_IDLE_S - 1
    await w.c.step()
    assert "activate" not in w.verbs()
    w.program.st["estado"] = "conversa"  # someone talked: the count starts over
    w.now += 10
    await w.c.step()
    w.program.st["estado"] = "parada"
    w.now += 10
    await w.c.step()
    w.now += HEAVY_IDLE_S - 1
    await w.c.step()
    assert "activate" not in w.verbs()
    w.now += 1
    await w.c.step()
    assert w.verbs()[-1] == "activate"


async def test_heavy_counts_a_conversation_that_happened_between_two_polls(tmp_path):
    w = World(tmp_path, Http(offer(heavy=True)))
    await w.c.step()
    w.now += HEAVY_IDLE_S
    w.program.st["ultima_abertura"] = 999  # a conversation opened and closed while nobody polled
    await w.c.step()
    assert "activate" not in w.verbs()
    w.now += HEAVY_IDLE_S
    await w.c.step()
    assert w.verbs()[-1] == "activate"


async def test_manual_ignores_timers_and_music_but_not_a_conversation(tmp_path):
    w = World(tmp_path, Http(offer(auto_install=False)), Program({**IDLE, "timers": [{"nome": "x"}], "musica": True}))
    assert w.c.request_install() == {"ok": True}
    w.program.st["estado"] = "conversa"
    await w.c.step()
    assert "activate" not in w.verbs()
    w.program.st["estado"] = "parada"
    await w.c.step()
    assert w.verbs()[-1] == "activate"


async def test_install_requested_by_the_vm_is_manual(tmp_path):
    w = World(tmp_path, Http(offer(auto_install=False, install_requested=True, heavy=True)),
              Program({**IDLE, "musica": True}))
    await w.c.step()
    assert w.verbs()[-1] == "activate"


async def test_reports_result_left_by_helper(tmp_path):
    w = World(tmp_path)
    await w.c.step()
    assert w.verbs()[-1] == "activate"
    w.helper_result(tag="v0.3.0", previous="v0.2.1", state="rolled_back", stage="health", error="program")
    w2 = World.__new__(World)  # the agent that comes back: same files, new process
    w2.__dict__.update(w.__dict__)
    w2.helper = Helper()
    w2.c = ota.OtaClient(SAT, w.program, w.http, w2.helper, clock=lambda: w.now, state_path=w.c.state_path,
                         result_path=w.c.result_path, downloads=w.c.downloads, channel_file=w.c.channel_file,
                         token_file=w.c.token_file, current=w.c.current, helper_path=w.c.helper_path,
                         sudo=w.fake_sudo)
    await w2.c.startup()
    assert w2.helper.calls[0] == ["recover"]
    assert w.http.reports == [{"sat_id": SAT, "tag": "v0.3.0", "result": "rolled_back", "stage": "health",
                               "error": "program", "protocol": 1}]
    assert {"acao": "ring", "state": "off"} in w.program.commands
    state = json.loads((tmp_path / "state.json").read_text())
    assert "activating" not in state and state["attempts"]["failures"] == 1
    await w2.c.startup()
    assert len(w.http.reports) == 1  # reported once


async def test_installed_result_clears_pending_and_prunes(tmp_path):
    w = World(tmp_path)
    await w.c.step()
    w.helper_result(tag="v0.3.0", previous="v0.2.1", state="installed")
    await w.c.report_activation()
    assert w.http.reports[-1]["result"] == "installed"
    assert {"acao": "update_pending", "ligado": False} in w.program.commands and w.verbs()[-1] == "prune"


async def test_still_activating_reports_nothing(tmp_path):
    w = World(tmp_path)
    await w.c.step()
    w.helper_result(tag="v0.3.0", state="activating")
    await w.c.report_activation()
    assert w.http.reports == []
    n = len(w.http.calls)
    await w.c.step()
    assert len(w.http.calls) == n  # no new offer while the helper still works


async def test_wrong_sha_never_stages_and_reports_failed(tmp_path):
    w = World(tmp_path, Http(body=b"tampered"))
    await w.c.step()
    assert w.verbs() == []
    assert w.http.reports[-1]["result"] == "failed" and w.http.reports[-1]["stage"] == "download"
    assert not list((tmp_path / "downloads").iterdir())


async def test_helper_refusal_reports_its_stage(tmp_path):
    w = World(tmp_path, helper=Helper({"stage": (2, "x\nfriday-ota-apply: refused at pip: no space left\n")}))
    await w.c.step()
    assert (w.http.reports[-1]["stage"], w.http.reports[-1]["error"]) == ("pip", "no space left")
    assert "activate" not in w.verbs()


async def test_local_retry_respects_next_at(tmp_path):
    w = World(tmp_path, Http(body=b"tampered"))
    await w.c.step()
    n = [c[0] for c in w.http.calls].count("download")
    w.now += RETRY_DELAYS_S[0] - 1
    await w.c.step()
    assert [c[0] for c in w.http.calls].count("download") == n
    w.now += 1
    await w.c.step()
    assert [c[0] for c in w.http.calls].count("download") == n + 1


async def test_a_report_the_vm_missed_is_sent_again(tmp_path):
    w = World(tmp_path, Http(body=b"tampered"))
    w.http.down = True
    await w.c.step()  # offer fails: nothing happens
    w.http.down = False
    await w.c.step()
    assert len(w.http.reports) == 1
    w.c.data["unsent"] = w.http.reports.pop()
    await w.c.step()
    assert w.http.reports and "unsent" not in json.loads((tmp_path / "state.json").read_text())


async def test_without_credentials_the_vm_is_never_called(tmp_path):
    w = World(tmp_path, creds=False)
    await w.c.step()
    assert w.http.calls == [] and w.helper.calls == []
    assert w.c.entity_state()["ota_credentials"] is False
    assert w.c.request_install() == {"ok": False, "error": "no_credentials"}


async def test_set_credentials_and_channel(tmp_path):
    w = World(tmp_path, creds=False)
    assert w.c.set_credentials("", "http://vm:8801")["ok"] is False
    assert w.c.set_credentials("t" * 20, "ftp://x")["ok"] is False
    assert w.c.set_credentials("t" * 20, "http://vm:8801/") == {"ok": True}
    assert (tmp_path / "etc" / "ota_token").stat().st_mode & 0o777 == 0o600
    assert w.c.set_channel("beta")["ok"] is False and w.c.set_channel("alpha") == {"ok": True}
    assert w.c.settings() == {"channel": "alpha", "url": "http://vm:8801"}
    assert w.c.entity_state()["ota_channel"] == "alpha" and w.c.entity_state()["ota_credentials"] is True


async def test_old_image_gets_the_helper_from_the_package(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        info = tarfile.TarInfo("system/friday-ota-apply")
        info.size = 2
        t.addfile(info, io.BytesIO(b"#!"))
    w = World(tmp_path, Http(body=buf.getvalue()), current=None)
    w.http.offer = offer(sha256=hashlib.sha256(buf.getvalue()).hexdigest())
    os.remove(tmp_path / "helper")
    await w.c.step()
    assert w.sudo[0][:1] == ["install"] and w.sudo[0][-1] == str(tmp_path / "helper")
    assert w.verbs()[0] == "stage"


async def test_activation_refused_before_detaching_is_a_failure(tmp_path):
    w = World(tmp_path, helper=Helper({"activate": (2, "friday-ota-apply: refused at args: bad tag")}))
    await w.c.step()
    assert w.http.reports[-1]["stage"] == "args"
    assert "activating" not in json.loads((tmp_path / "state.json").read_text())
    assert w.program.commands[-1] == {"acao": "ring", "state": "off"} or {"acao": "ring", "state": "off"} in \
        w.program.commands


def test_package_manifest_contract():
    good = {"schema": 1, "tag": "v0.3.0", "sha": "a" * 40, "protocol": {"satellite": 1}, "heavy": False,
            "lock_sha256": "b" * 64, "migrations": ["0001_versioned_layout.sh"]}
    assert ota.parse_package_manifest(good)["protocol"] == {"satellite": 1}
    for key, bad in (("tag", "x"), ("sha", "z"), ("schema", 2), ("protocol", {"s": True}), ("migrations", "x")):
        with pytest.raises(ValueError, match=key):
            ota.parse_package_manifest({**good, key: bad})
