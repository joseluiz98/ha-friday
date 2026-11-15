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
FP = "ab" * 32
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

    async def get_json(self, url, params, token, fingerprint=None):
        assert fingerprint == FP
        self.calls.append(("offer", url, dict(params), token))
        if self.down:
            raise OSError("down")
        return self.offer

    async def post_json(self, url, body, token, fingerprint=None):
        assert fingerprint == FP
        if self.down:
            raise OSError("down")
        self.reports.append(body)
        return {"ok": True}

    async def download(self, url, token, dest, fingerprint=None):
        assert fingerprint == FP
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
            (etc / "ota.json").write_text(json.dumps({"channel": "alpha", "url": "https://vm:8802/", "fingerprint": FP}))
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
    assert w.http.calls[1] == ("download", f"https://vm:8802/v1/ota/package/v0.3.0/satellite.tar.gz?sat_id={SAT}")
    stage = w.helper.calls[0]
    assert stage[0] == "stage" and stage[2] == "v0.3.0" and "--wheels" not in stage
    assert w.helper.calls[1] == ["activate", "v0.3.0"]
    assert {"acao": "ring", "state": "updating"} in w.program.commands
    assert not list((tmp_path / "downloads").iterdir())  # the helper copied it: nothing big stays
    assert json.loads((tmp_path / "state.json").read_text())["activating"]["tag"] == "v0.3.0"


async def test_channel_installed_and_lock_go_in_the_offer(tmp_path):
    w = World(tmp_path)
    w.http.offer = {"tag": None, "reason": "up_to_date"}
    await w.c.step()
    _, url, params, token = w.http.calls[0]
    assert url == "https://vm:8802/v1/ota/offer" and token == "tok"
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
                                  {"musica": True}, {"pendentes": 1}, {"fila_entregas": 1}])
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
    w.helper_result(tag="v0.3.0", previous="v0.2.1", state="rolled_back", stage="health", error="program", at=w.now)
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
    w.helper_result(tag="v0.3.0", previous="v0.2.1", state="installed", at=w.now)
    await w.c.report_activation()
    assert w.http.reports[-1]["result"] == "installed"
    assert {"acao": "update_pending", "ligado": False} in w.program.commands and w.verbs()[-1] == "prune"


async def test_still_activating_reports_nothing(tmp_path):
    w = World(tmp_path)
    await w.c.step()
    w.helper_result(tag="v0.3.0", state="activating", at=w.now)
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
    class Flaky(Http):
        fail_posts = 1

        async def post_json(self, url, body, token, fingerprint=None):
            if self.fail_posts:
                self.fail_posts -= 1
                raise OSError("down")
            return await super().post_json(url, body, token, fingerprint)
    w = World(tmp_path, Flaky(body=b"tampered"))
    await w.c.step()
    assert w.http.reports == []
    assert json.loads((tmp_path / "state.json").read_text())["unsent"][0]["result"] == "failed"
    await w.c.step()
    assert [r["result"] for r in w.http.reports] == ["failed"]
    assert "unsent" not in json.loads((tmp_path / "state.json").read_text())


async def test_without_credentials_the_vm_is_never_called(tmp_path):
    w = World(tmp_path, creds=False)
    await w.c.step()
    assert w.http.calls == [] and w.helper.calls == []
    assert w.c.entity_state()["ota_credentials"] is False
    assert w.c.request_install() == {"ok": False, "error": "no_credentials"}


async def test_set_credentials_and_channel(tmp_path):
    w = World(tmp_path, creds=False)
    assert w.c.set_credentials("", "https://vm:8802", FP)["ok"] is False
    assert w.c.set_credentials("t" * 20, "http://vm:8801", FP)["ok"] is False  # never plain HTTP
    assert w.c.set_credentials("t" * 20, "https://vm:8802", None)["ok"] is False  # never without the pin
    assert w.c.set_credentials("t" * 20, "https://vm:8802/", FP) == {"ok": True}
    assert (tmp_path / "etc" / "ota_token").stat().st_mode & 0o777 == 0o600
    assert w.c.set_channel("beta")["ok"] is False and w.c.set_channel("alpha") == {"ok": True}
    assert w.c.settings() == {"channel": "alpha", "url": "https://vm:8802", "fingerprint": FP}
    assert w.c.entity_state()["ota_channel"] == "alpha" and w.c.entity_state()["ota_credentials"] is True


def test_an_old_http_credential_is_not_used(tmp_path):
    w = World(tmp_path)
    (tmp_path / "etc" / "ota.json").write_text(json.dumps({"channel": "stable", "url": "http://vm:8801"}))
    assert w.c.has_credentials() is False


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
    state = json.loads((tmp_path / "state.json").read_text())
    assert "activating" not in state and state["attempts"]["failures"] == 1 and "staged" not in state
    rings = [c["state"] for c in w.program.commands if c.get("acao") == "ring"]
    assert rings == ["updating", "off"] and w.c.in_progress is False


def _restart(w):
    """The agent that comes back: same files, new process."""
    return ota.OtaClient(SAT, w.program, w.http, w.helper, clock=lambda: w.now, state_path=w.c.state_path,
                         result_path=w.c.result_path, downloads=w.c.downloads, channel_file=w.c.channel_file,
                         token_file=w.c.token_file, current=w.c.current, helper_path=w.c.helper_path,
                         sudo=w.fake_sudo)


async def test_a_stale_result_of_the_same_tag_is_not_reported(tmp_path):
    w = World(tmp_path)
    w.helper_result(tag="v0.3.0", state="rolled_back", at=w.now - 100)  # an earlier attempt of the same tag
    await w.c.step()
    assert w.verbs()[-1] == "activate"
    w.c.kick()
    await w.c.step()
    assert w.http.reports == [] and w.c.in_progress


async def test_a_result_that_never_comes_unblocks_after_the_deadline(tmp_path):
    w = World(tmp_path)
    await w.c.step()  # activate; the helper never writes anything
    w.now += ota.ACTIVATE_DEADLINE_S + 1
    await w.c.step()
    assert "recover" in w.verbs()
    assert w.http.reports[0]["stage"] == "activate" and w.http.reports[0]["error"] == "lost"


async def test_helper_raising_on_activate_does_not_wedge(tmp_path):
    class Boom(Helper):
        async def __call__(self, args):
            if args[0] == "activate":
                raise OSError("exec")
            return await super().__call__(args)
    w = World(tmp_path, helper=Boom())
    await w.c.step()
    assert w.http.reports[-1]["result"] == "failed" and "activating" not in w.c.data


async def test_an_unexpected_stage_error_is_reported_once_and_backs_off(tmp_path):
    class Boom(Helper):
        async def __call__(self, args):
            if args[0] == "stage":
                raise OSError("exec")
            return await super().__call__(args)
    w = World(tmp_path, helper=Boom())
    await w.c.step()
    assert [r["stage"] for r in w.http.reports] == ["stage"]
    w.now += RETRY_DELAYS_S[0] - 1
    await w.c.step()
    assert [c[0] for c in w.http.calls].count("download") == 1


async def test_pending_ring_comes_back_after_a_rollback_while_the_retry_waits(tmp_path):
    w = World(tmp_path)
    await w.c.step()
    w.helper_result(tag="v0.3.0", state="rolled_back", stage="health", at=w.now + 1)
    w.c = _restart(w)
    await w.c.startup()
    w.program.st["update_pendente"] = False  # the rollback restarted the program
    w.http.offer = {"tag": None, "reason": "retry_later", "auto_install": True, "install_requested": False}
    await w.c.step()
    assert w.program.commands[-1] == {"acao": "update_pending", "ligado": True}


async def test_a_manual_install_with_nothing_to_install_does_not_arm_a_later_release(tmp_path):
    w = World(tmp_path, program=Program({**IDLE, "timers": [{"nome": "x"}]}))
    w.http.offer = {"tag": None, "reason": "up_to_date"}
    assert w.c.request_install() == {"ok": True}
    await w.c.step()
    assert w.http.calls[-1][2]["install"] == "1"
    w.http.offer = offer("v0.4.0")  # days later, a new release while a timer runs
    await w.c.step()
    offers = [c for c in w.http.calls if c[0] == "offer"]
    assert "install" not in offers[-1][2] and "activate" not in w.verbs()


async def test_back_mid_activation_shows_the_install(tmp_path):
    w = World(tmp_path)
    await w.c.step()
    w.helper_result(tag="v0.3.0", state="activating", at=w.now + 1)
    w.program.commands.clear()
    w.c = _restart(w)
    await w.c.startup()
    assert w.c.in_progress and w.program.commands == [{"acao": "ring", "state": "updating"}]


async def test_an_old_program_is_asked_update_pending_once(tmp_path):
    class Old(Program):
        async def command(self, body):
            self.commands.append(body)
            raise RuntimeError("400")
    st = {k: v for k, v in IDLE.items() if k != "update_pendente"}
    w = World(tmp_path, Http(offer(auto_install=False)), Old(st))
    await w.c.step()
    await w.c.step()
    assert w.program.commands.count({"acao": "update_pending", "ligado": True}) == 1


def test_package_manifest_contract():
    good = {"schema": 1, "tag": "v0.3.0", "sha": "a" * 40, "protocol": {"satellite": 1}, "heavy": False,
            "lock_sha256": "b" * 64, "migrations": ["0001_versioned_layout.sh"]}
    assert ota.parse_package_manifest(good)["protocol"] == {"satellite": 1}
    for key, bad in (("tag", "x"), ("sha", "z"), ("schema", 2), ("protocol", {"s": True}), ("migrations", "x")):
        with pytest.raises(ValueError, match=key):
            ota.parse_package_manifest({**good, key: bad})


async def test_install_pressed_while_the_offer_is_in_flight_is_kept(tmp_path):
    w = World(tmp_path)

    async def offer_with_press(url, params, token, fingerprint=None):
        w.c.request_install()  # the button, during the request
        return {"tag": None, "reason": "up_to_date"}
    w.http.get_json = offer_with_press
    await w.c.step()
    assert w.c.manual_asked is True


def test_the_report_queue_is_capped(tmp_path):
    w = World(tmp_path)
    for i in range(30):
        w.c._queue("v0.3.0", "failed", "s", str(i))
    q = w.c.data["unsent"]
    assert len(q) == ota.MAX_UNSENT and q[-1]["error"] == "29"


async def test_the_real_client_pins_the_vm_certificate(tmp_path):
    """End to end over TLS: the right fingerprint talks, any other certificate is refused."""
    import shutil
    if not shutil.which("openssl"):
        pytest.skip("no openssl here")
    from aiohttp import web
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "server"))
    from friday_server_agent.ota import tls
    c, k = str(tmp_path / "c.crt"), str(tmp_path / "k.key")
    tls.ensure(c, k)
    app = web.Application()
    app.router.add_get("/v1/ota/offer", lambda r: web.json_response({"tag": None, "reason": "up_to_date"}))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=tls.context(c, k))
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        h = ota.HttpClient(timeout_s=5)
        url = f"https://127.0.0.1:{port}/v1/ota/offer"
        assert (await h.get_json(url, {}, "t", tls.fingerprint(c)))["reason"] == "up_to_date"
        with pytest.raises(Exception):
            await h.get_json(url, {}, "t", "00" * 32)
    finally:
        await runner.cleanup()


def test_update_channel_is_not_on_the_device_page():
    """14/11: the channel select is hidden (set from the satellite's Advanced step); the update entity stays."""
    from friday_satellite_agent.ota import OtaClient
    by_key = {d["key"]: d for d in OtaClient.descriptors()}
    assert by_key["ota_channel"]["hidden"] is True and by_key["ota_channel"]["options"] == ["stable", "alpha"]
    assert not by_key["ota"].get("hidden") and by_key["ota"]["name"] == "Software"


async def test_old_layout_shows_the_version_line_so_ha_offers_install(tmp_path):
    """FINDING 3 (14/11): on the old layout `installed` was None, the update entity unknown and HA had no Install."""
    w = World(tmp_path, Http(offer()), current=None)
    w.c.legacy_version = "v0.2.1-69-g75a1756"
    await w.c.step()
    st = w.c.entity_state()["ota"]
    assert st["installed"] == "v0.2.1-69-g75a1756" and st["latest"] == "v0.3.0"
    assert ota.installed(w.c.current) == (None, "")  # the offer still says "old layout" to the VM


def test_versioned_layout_ignores_the_version_line(tmp_path):
    w = World(tmp_path)
    w.c.legacy_version = "v0.2.1-69-g75a1756"
    assert w.c.entity_state()["ota"]["installed"] == "v0.2.1"
