# F.R.I.D.A.Y. for Home Assistant

F.R.I.D.A.Y. is a self-hosted voice assistant: a server running the brain and the voice, plus Raspberry Pi voice
satellites with wake words. This custom integration brings both into Home Assistant as devices, so you can monitor
health and costs, control wake words, volume and voice, see each activation's details and listen to its recording.
The agents speak the websocket protocol in [docs/protocol.md](docs/protocol.md).

## What you get

- **Devices and entities** for the server and every satellite, generated from the capabilities the agents announce
  (health, costs, wake word modes, volume, voice, restart and reboot actions).
- **Activations card** (`custom:friday-activations-card`) listing each activation with its cause and a play button.
- **Media panel source** with the activation recordings, grouped by satellite and day.
- **Diagnostics** with secrets redacted, plus logbook entries for restart and reboot actions.

## Install

1. In HACS open the three-dot menu, **Custom repositories**, add
   `https://github.com/joseluiz98/ha-friday` with category **Integration**, then download it.
2. Restart Home Assistant (2026.10.0 or newer) and add **F.R.I.D.A.Y.** under Settings, Devices & services.
3. Install the agents (see below) so the server and each satellite announce themselves.

### Agents

Each agent is a small program that speaks the websocket protocol in [docs/protocol.md](docs/protocol.md).

- **Server agent** (the VM running F.R.I.D.A.Y.): `sudo bash agents/server/install.sh`. It creates a venv, generates
  the agent token under `/srv/friday/secrets/agent_token` and installs `friday-server-agent.service`.
- **Satellite agent** (each Raspberry Pi satellite): from `agents/`, `sudo bash satellite/install.sh`. It announces
  `_friday._tcp` over zeroconf; Home Assistant offers the satellite on the integrations page.

### Proxmox token (optional, for the *Reboot VM* button)

Fill the Proxmox fields (URL, node, VM id, token id, token secret) with *Reconfigure* on the Server. Create an API
token in Proxmox with privilege separation and give it only `VM.PowerMgmt` on `/vms/<vmid>`. Keep the secret in the
config entry only; never commit it.

### Activations card

The integration registers a Lovelace card for satellite activations:

```yaml
type: custom:friday-activations-card
entity: event.friday_sat_activation   # one entity or a list
hours: 24                             # 24, 72 or 168
types: [conversation, rejected, shadow]
```

### Media panel

Activation recordings appear in the Media panel under the **F.R.I.D.A.Y.** source, grouped by satellite and day.

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

### Privacy and recorder notes

- `wifi_ssid` (the network name) is a diagnostic sensor that is **disabled by default** (`enabled_default: false` in
  the descriptor): Home Assistant's recorder stores the state of every enabled entity, so the SSID is only kept if you
  enable the entity yourself. `wifi_signal` and the other network sensors stay enabled.
- `activations_today` and `rejected_today` reset every local midnight, so they carry no `state_class`. The agent keeps
  today's counts in `~/.friday-agent/counters.json` so a restart of the agent does not zero them.
