# F.R.I.D.A.Y. agent protocol v1

The integration talks to **agents** (the F.R.I.D.A.Y. server and each voice
satellite) over a single websocket per agent. The agent describes itself
(capabilities); Home Assistant builds entities from that description. The wire
format is JSON text frames. The reference implementation of the parsing is
`custom_components/friday/protocol.py` (pure Python, no Home Assistant imports).

## Transport

- URL: `ws://<host>:<port>/v1/ws`
- Header: `Authorization: Bearer <token>`; the agent answers `401` without it.
- Satellite: port `8701`; the token is the satellite's local-port password
  from `/etc/friday-satelite/config.json`.
- Server: port `8801`; the token is in `/srv/friday/secrets/agent_token`
  (new file, mode 600).
- Never put tokens in the repository, logs or terminal output.

## Agent to Home Assistant

### hello

Sent right after connecting.

```json
{"type":"hello","protocol":1,
 "device":{"id":"...","kind":"server|satellite","name":"...","model":"...","sw_version":"...","hw_version":"..."},
 "capabilities":[<descriptor>, ...],
 "state":{"<key>":<value>}}
```

### Descriptor

```json
{"key":"...","platform":"sensor|binary_sensor|number|switch|select|button|event",
 "name":"...","category":null,"icon":"...","unit":"...","device_class":"...",
 "state_class":"...","min":0,"max":10,"step":1,"mode":"...",
 "options":["..."],"event_types":["..."],"available":true,"hidden":false,"enabled_default":true}
```

- `category`: `null`, `"config"` or `"diagnostic"`.
- Absent fields mean the Home Assistant default.
- A descriptor whose `platform` is not one of the seven above is skipped (its
  key is reported in `Hello.skipped`); the rest of the hello is still used.
- `enabled_default: false` creates the entity disabled in the entity registry (the user can enable it); used for
  `wifi_ssid`, so the network name is not recorded unless asked for.
- `hidden: true` marks a protocol-only command: it is never turned into an
  entity, but Home Assistant may still send it as a command.

### state

```json
{"type":"state","changes":{"<key>":<value>},"origin":"voice|button|ha|agent|system"}
```

### event

```json
{"type":"event","key":"<event descriptor key>","event_type":"...","data":{}}
```

### log

```json
{"type":"log","message":"...","data":{}}
```

Becomes a logbook entry.

### capabilities

```json
{"type":"capabilities","capabilities":[<descriptor>, ...]}
```

Replaces the whole list; the integration reloads the config subentry.

## Home Assistant to agent

```json
{"type":"command","id":1,"name":"set","key":"...","value":...}
{"type":"command","id":2,"name":"press","key":"..."}
{"type":"command","id":3,"name":"<action>","args":{}}
```

The agent replies with the same `id`:

```json
{"type":"result","id":1,"ok":true,"data":{}}
{"type":"result","id":1,"ok":false,"error":"conversation_open"}
```

`error` is a short machine-readable code (for example `conversation_open`).

## Origin `ha`

When a `set` came from Home Assistant, the agent confirms it with a `state`
message whose `origin` is `ha`. The integration re-emits nothing in that case
(no duplicate event or logbook entry).

## Command `diagnostics`

`{"type":"command","id":4,"name":"diagnostics"}` returns, in `data`, the agent's
redacted state and the tail of its logs (`logs`: unit/container to text, capped at
200 KB each; satellite also returns the redacted `config`). Secrets are masked by key
and by regex (Bearer, `PVEAPIToken=`, psk/password) and never leave the agent.
