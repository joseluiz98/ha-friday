# ha-friday

Home Assistant custom integration for **F.R.I.D.A.Y.**: the server (VM) and
each voice satellite show up as devices, with entities generated from the
capabilities the agents announce over the websocket protocol described in
[docs/protocol.md](docs/protocol.md).

Status: scaffolding (v0.1.0); only the pure protocol module exists so far.

## Install

Add this repository to HACS as a custom repository (type: Integration), then
restart Home Assistant. Requires Home Assistant 2026.10.0 or newer.

## Development

Tests run on the VM `friday` (the HAOS box is too weak) inside a
`python:3.14-slim` container:

```
scripts/test.sh                                   # whole suite
scripts/test.sh tests/integration/test_protocol.py
```

The script rsyncs the repo to `~/ha-friday` on the VM and runs pytest there
(pip cache kept in the `friday-pip-cache` Docker volume; Docker needs `sudo`).
Override the target with `FRIDAY_TEST_VM=user@host`.

Test dependencies are pinned in `requirements_test.txt`:
`pytest-homeassistant-custom-component==0.13.375`, the release that pins
`homeassistant==2026.10.4` exactly. It requires Python >= 3.14 (not 3.13), so
the test container uses `python:3.14-slim`.

Run `bash secret-sweep.sh` before every commit or push.

## Restart and reboot actions

- **Restart F.R.I.D.A.Y.** (Server button): refuses while a conversation is open (read from the container
  log, same rule as `POST /v1/restart`), then runs `docker compose restart friday` on the VM. After the Server
  accepts, the integration presses the hidden `restart_program` on every connected satellite.
- **Reboot VM** (integration button, Server device): `POST` to the Proxmox API
  (`/nodes/<node>/qemu/<vmid>/status/reboot`), so it works even with the VM hung. It is created only when the
  Server subentry has all Proxmox fields (URL, node, VM id, token id, token secret); fill them with
  *Reconfigure* on the Server. The token needs only `VM.PowerMgmt` on `/vms/<vmid>`. The conversation check
  applies only while the Server agent is connected.
- Every press (ok or refused) is logged to the logbook on the device.

### Process restarts (investigation, 2026-10-29)

Inside the container `/run.sh` (PID 7) starts three children: the OpenClaw gateway (kept by `manter_gateway`,
a backoff loop), the Pipecat bot (`bot.py`) and a `socat` TLS terminator. `wait -n` on the bot or `socat`
ends the container, so the Voice Gateway (bot) is **not** restartable on its own: killing it restarts the
whole container, the same as Restart F.R.I.D.A.Y. The gateway (brain) has a loop, but a kill costs the
15 s+ backoff and counts as a failure for the escalating wait. There are therefore no *Restart Voice Gateway*
or *Restart Brain* buttons (the spec allows omitting unsupported actions).

## Satellite agent

`agents/satellite` runs on the Pi next to the voice program (`sudo bash satellite/install.sh` from `agents/`; it never
restarts `friday-satelite`). It announces `_friday._tcp` over zeroconf; Home Assistant offers the satellite on the
integrations page, asks for the local-port password and the area, and adds it as a `satellite` subentry
(`async_add_subentry`, available in HA 2026.10). `single_config_entry` is deliberately not set in the manifest, since it
would abort discovery once the main entry exists; the user flow still allows a single entry.
