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
