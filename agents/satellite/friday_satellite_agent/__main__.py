"""F.R.I.D.A.Y. satellite agent: mirrors the voice program's settings to Home Assistant."""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import secrets

from aiohttp import web

from friday_agent_core.server import AgentCore

from . import capabilities as cap
from . import diagnostics as diagmod
from . import eq as eqmod
from . import hardware as hw
from . import local, pairing, provision, recordings
from . import room_sensors
from . import wifi
from .announce import announce, lan_ip as _lan_ip
from . import power as power_mod
from .power import Power
from .program import ProgramClient, ProgramError

LOG = logging.getLogger("friday_satellite_agent")
CONFIG = os.environ.get("FRIDAY_SAT_CONFIG", "/etc/friday-satelite/config.json")
TIMERS = os.environ.get("FRIDAY_SAT_TIMERS", "/home/jose/satelite/estado/timers.json")


def read_timers() -> list | None:
    try:
        with open(TIMERS, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None
ETC_DIR = os.path.dirname(CONFIG)  # /etc/friday-satelite: pairable, ha_token, mic
_exit = os._exit  # replaced in tests
PROGRAM_DIR = os.environ.get("FRIDAY_SAT_PROGRAM_DIR", "/home/jose/satelite")
BUNDLES = os.path.expanduser("~/.friday-agent/bundles")
APPLY_AGENT = ["sudo", "-n", "/usr/local/sbin/friday-agent-apply"]
ID_FILE = os.environ.get("FRIDAY_SAT_ID_FILE", "/etc/friday-satelite/id")
RECORDINGS = os.environ.get("FRIDAY_SAT_RECORDINGS", "/home/jose/gravacoes/ativacoes")
PORT = int(os.environ.get("FRIDAY_AGENT_PORT", "8701"))
COUNTERS = os.path.expanduser("~/.friday-agent/counters.json")  # today's counts survive an agent restart
POLL_S = 20


def _read_settings() -> tuple[str, int]:
    with open(CONFIG, encoding="utf-8") as f:  # only the keys we need; never logged
        cfg = json.load(f)
    return cfg["senha_porta_local"], int(cfg.get("porta_local", 8700))


def _sat_id() -> str:
    with open(ID_FILE, encoding="utf-8") as f:
        return f.read().strip()


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    token, program_port = _read_settings()
    if not isinstance(token, str) or not token.strip():
        LOG.error("senha_porta_local in %s is empty", CONFIG)
        raise SystemExit("missing local port password")
    sat_id = _sat_id()
    program = ProgramClient(token, f"http://127.0.0.1:{program_port}")
    # `token` above is the program's local password (program <-> agent, loopback). The token HA must present is a
    # different thing: the one HA issued at adoption, or - legacy satellites paired by hand - that same password.
    pairable = pairing.pairing_state(ETC_DIR) == "pairable"
    # while pairable nobody can know the agent token: a random one that is never disclosed
    ha_token = secrets.token_urlsafe(32) if pairable else pairing.agent_token(ETC_DIR, {"senha_porta_local": token})
    session = pairing.PairingSession() if pairable else None
    zc_box: list = []
    mapper, tracker = cap.Mapper(), cap.Tracker(path=COUNTERS)
    memo: dict = {"cfg": None, "ww": [], "state": {"program_state": "off", **tracker.counters(),
                                                   "sw_version": cap.VERSION, "wifi_change": "idle"}}
    device = {"id": sat_id, "kind": "satellite", "name": f"Satellite {sat_id}",
              "model": "Raspberry Pi 3 + Jabra", "sw_version": cap.VERSION}

    async def power_log(message: str) -> None:
        await core.push_log(message, {})

    power = Power(program_state=lambda: memo["state"].get("program_state"), log=power_log)
    memo["state"]["voice_enabled"] = power.enabled
    off_task: list[asyncio.Task] = []

    async def voice_off() -> None:
        try:
            await power.set_voice(False)
        except Exception as err:  # noqa: BLE001
            LOG.warning("voice off failed: %s", type(err).__name__)

    async def set_voice(enabled: bool) -> dict:
        for t in off_task:
            t.cancel()  # a newer command replaces a pending courtesy wait
        off_task.clear()
        memo["state"]["voice_enabled"] = enabled
        await core.push_state({"voice_enabled": enabled}, "ha")
        if enabled:
            await power.set_voice(True)
        else:
            power.persist(False)  # durable before the (up to 120 s) wait
            off_task.append(asyncio.create_task(voice_off()))
        return {"ok": True}

    async def apply_agent(stage: str) -> int:
        return await power_mod.run_command(APPLY_AGENT + [stage])

    prov = provision.Provisioner(
        etc_dir=ETC_DIR, config_path=CONFIG, ssh_dir=os.path.expanduser("~/.ssh"), program_dir=PROGRAM_DIR,
        bundles_dir=BUNDLES, voices_dir=os.path.expanduser("~/vozes"), models_dir=os.path.expanduser("~/modelos"),
        program=program, restart_program=power.restart_program, apply_agent=apply_agent,
        exit_fn=lambda code: _exit(code), ip_fn=lambda: _lan_ip())

    def _args(cmd: dict) -> dict:
        return cmd["args"] if isinstance(cmd.get("args"), dict) else cmd

    async def do_provision_info(cmd: dict) -> dict:
        return await prov.info()

    async def do_apply_bundle(cmd: dict) -> dict:
        a = _args(cmd)
        return await prov.apply_bundle(a.get("kind"), a.get("url"), a.get("sha256"))

    async def do_start_onboarding(cmd: dict) -> dict:
        return await prov.start_onboarding(_args(cmd).get("facts"))

    async def do_factory_reset(cmd: dict) -> dict:
        return await prov.factory_reset(_args(cmd).get("level"))

    async def do_press(cmd: dict) -> dict:
        key = cmd.get("key")
        if key == "reset_network":
            return await prov.factory_reset(1)
        if key == "reset_home":
            return await prov.factory_reset(2)
        if key == "reboot":
            return await power.reboot()
        if key == "restart_program":
            return await power.restart_program()
        return {"ok": False, "error": "unknown_key"}

    room = room_sensors.Monitor(room_sensors.RoomSensors(), lambda v: room_state(v), lambda: room_caps())
    memo["sent_caps"] = None
    equalizer = eqmod.Equalizer()

    def capabilities() -> list[dict]:
        return cap.controls_from_config(memo["cfg"] or {}, memo["ww"]) + cap.room_descriptors(room.keys) + equalizer.descriptors()

    async def room_state(values: dict) -> None:
        memo["state"].update(values)
        await core.push_state(values, "agent")

    async def room_caps() -> None:
        memo["sent_caps"] = capabilities()  # the poll must not announce the same change again
        await core.push_capabilities()

    async def refresh(origin: str) -> None:
        status = await program.status()
        if status is None:
            memo["cfg"] = None
            changes = {"program_state": "off"}
        else:
            try:
                memo["cfg"] = await program.get_config()
                memo["ww"] = await program.wakewords()
            except (ProgramError, OSError):
                return
            changes = mapper.state(memo["cfg"], memo["ww"], status)
        timers = await asyncio.to_thread(read_timers)
        if timers is not None:
            changes.update(cap.timers_state(timers))
        changes.update(tracker.counters())
        memo["state"].update(changes)
        await core.push_state(changes, origin)

    async def do_set(cmd: dict) -> dict:
        if cmd.get("key") == "voice_enabled":
            return await set_voice(bool(cmd.get("value")))
        if cmd.get("key") in eqmod.BANDS:
            res = await equalizer.apply({cmd["key"]: cmd.get("value")})
            memo["state"].update(equalizer.state())
            await core.push_state(equalizer.state(), "ha")
            return res
        cfg = memo["cfg"]
        if cfg is None:
            return {"ok": False, "error": "program_down"}
        plan = mapper.to_program(cfg, cmd.get("key"), cmd.get("value"))
        if plan is None:
            return {"ok": False, "error": "unknown_key"}
        try:
            if "config" in plan:
                tracker.mark_ha(plan["config"])
                await program.put_config(plan["config"])
            else:
                name, mode = plan["wakeword"]
                tracker.mark_ha(["modo_friday", "palavras_extras"])
                await program.set_wakeword(name, modo=mode)
        except ProgramError as err:
            return {"ok": False, "error": "rejected" if err.status in (400, 409) else "program_error"}
        except (OSError, TimeoutError):
            return {"ok": False, "error": "program_down"}
        await refresh("ha")
        return {"ok": True}

    changer = wifi.Changer()
    wifi_tasks: set = set()

    async def wifi_report(status: str, message: str | None = None) -> None:
        memo["state"]["wifi_change"] = status
        await core.push_state({"wifi_change": status}, "agent")
        if message:
            await core.push_log(message, {})

    async def wifi_job(ssid: str, password: str, server_host) -> None:
        try:
            await wifi_report("running")
            result = await changer.set_wifi(ssid, password, server_host)
            if result.get("ok"):
                await wifi_report("ok", "Wi-Fi changed")
            else:
                await wifi_report("rolled_back", "Wi-Fi change failed; previous network restored")
        except Exception as err:  # noqa: BLE001 - never log args (password)
            LOG.error("wifi change crashed: %s", type(err).__name__)
            await changer.recover()
            await wifi_report("rolled_back", "Wi-Fi change failed; previous network restored")

    async def do_wifi(cmd: dict) -> dict:
        args = cmd.get("args")
        if not isinstance(args, dict) or not wifi.nmcli_available():
            return {"ok": False, "error": "unsupported"}
        ssid, password = args.get("ssid"), args.get("password")
        if not wifi.valid_credentials(ssid, password):
            return {"ok": False, "error": "invalid"}
        if memo["state"].get("wifi_change") == "running":
            return {"ok": False, "error": "busy"}
        host = args.get("server_host") or core.last_peer
        # a background task owned by the agent: a dropped websocket (the IP may change) cannot cancel it
        task = asyncio.create_task(wifi_job(ssid, password, host))
        wifi_tasks.add(task)
        task.add_done_callback(wifi_tasks.discard)
        memo["state"]["wifi_change"] = "running"
        return {"ok": True, "data": {"status": "started"}}

    async def on_event(tipo: str, dados: dict) -> None:
        out = tracker.on_event(tipo, dados)
        if tipo == "onboarding":
            asyncio.create_task(prov.on_onboarding(dados.get("resultado")))
        if not out:
            return
        if "config" in out:
            await refresh(out["origin"])
            return
        if out.get("state"):
            memo["state"].update(out["state"])
            await core.push_state(out["state"], "voice")
        if out.get("event"):
            await core.push_event(*out["event"])

    core = AgentCore(ha_token, device, capabilities, lambda: dict(memo["state"]), {"set": do_set, "press": do_press, "wifi_set": do_wifi,
                      "provision_info": do_provision_info, "apply_bundle": do_apply_bundle,
                      "start_onboarding": do_start_onboarding, "factory_reset": do_factory_reset,
                      "diagnostics": diagmod.make_handler(CONFIG, lambda: dict(memo["state"]))})

    async def timers_watch() -> None:
        """Timers file changed (created/cancelled/finished): refresh at once instead of waiting the poll."""
        last = None
        while True:
            try:
                mtime = os.stat(TIMERS).st_mtime
            except OSError:
                mtime = None
            if last is not None and mtime != last:
                try:
                    await refresh("voice")
                except Exception as err:  # noqa: BLE001
                    LOG.warning("timers refresh failed: %s", type(err).__name__)
            last = mtime
            await asyncio.sleep(2)

    async def poll() -> None:
        while True:
            try:
                await refresh("agent")
                caps = capabilities()
                if memo["sent_caps"] is not None and caps != memo["sent_caps"]:
                    await core.push_capabilities()
                memo["sent_caps"] = caps
            except Exception as err:  # noqa: BLE001
                LOG.warning("poll failed: %s", type(err).__name__)
            await asyncio.sleep(POLL_S)

    async def hardware_loop() -> None:
        while True:
            try:
                changes = await hw.sample_async(with_network=wifi.nmcli_available())
                memo["state"].update(changes)
                await core.push_state(changes, "agent")
            except Exception as err:  # noqa: BLE001
                LOG.warning("hardware sample failed: %s", type(err).__name__)
            await asyncio.sleep(hw.SAMPLE_S)

    async def room_loop() -> None:
        while True:
            try:
                await room.step()
            except Exception as err:  # noqa: BLE001
                LOG.warning("room sensors failed: %s", type(err).__name__)
            await asyncio.sleep(room_sensors.READ_S)

    async def midnight() -> None:
        while True:
            now = datetime.datetime.now()
            nxt = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=1, microsecond=0)
            await asyncio.sleep((nxt - now).total_seconds())
            changes = tracker.reset_day()
            memo["state"].update(changes)
            await core.push_state(changes, "system")

    await asyncio.to_thread(room.prime)
    await power.startup()
    if await asyncio.to_thread(equalizer.detect):
        await equalizer.startup()
        memo["state"].update(equalizer.state())
    if await changer.recover():  # an attempt interrupted by an agent restart is rolled back
        memo["state"]["wifi_change"] = "rolled_back"
    async def speak_code() -> None:
        """Pairing mode: ring in setup colors and the code, spoken now and every 60 s until adopted."""
        while True:
            delay = 60
            try:
                await program.command({"acao": "ring", "state": "setup"})
                keys = ["setup_mode", "pairing_code"] + [f"digit_{d}" for d in session.code]
                await program.command({"acao": "play_phrases", "keys": keys})
            except Exception as err:  # noqa: BLE001 - the program may still be starting
                LOG.warning("pairing prompt failed: %s", type(err).__name__)
                delay = 5
            await asyncio.sleep(delay)

    async def finish_pairing() -> None:
        await asyncio.sleep(1.0)  # let the HTTP reply leave first
        try:
            await program.command({"acao": "ring", "state": "off"})
        except Exception:  # noqa: BLE001
            pass
        for zc_ in zc_box:
            zc_.close()  # goodbye packet; the restarted agent announces state=paired
        _exit(3)  # the unit has Restart=always: comes back with the new token

    async def on_paired(new_token: str) -> None:
        await asyncio.to_thread(pairing.persist_pairing, ETC_DIR, new_token)
        LOG.info("paired with Home Assistant; restarting to load the new token")
        asyncio.create_task(finish_pairing())

    app = local.make_app(token, on_event, core.app())
    if pairable:
        pairing.add_routes(app, session, sat_id, on_paired)
    recordings.add_routes(app, token, RECORDINGS)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    zc = await asyncio.to_thread(announce, sat_id, PORT, "pairable" if pairable else "paired")
    if zc:
        zc_box.append(zc)
    try:
        tasks = [poll(), midnight(), hardware_loop(), room_loop(), timers_watch()]
        if pairable:
            tasks.append(speak_code())
        await asyncio.gather(*tasks)
    finally:
        if zc:
            zc.close()


if __name__ == "__main__":
    asyncio.run(main())
