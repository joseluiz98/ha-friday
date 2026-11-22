# Satellite LAN First Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The satellite declares its cable and Wi-Fi addresses; the integration connects on the cable, falls back to the Wi-Fi at once, returns to the cable after 60 s of answers, and an address change never reloads the entry.

**Architecture:** the agent adds `wired`/`wifi` to its zeroconf properties; the zeroconf step stores them in the subentry (`host` = preferred, plus `host_wired`/`host_wifi`) and hands them to the live `FridayConnection` instead of reloading; `FridayConnection` keeps an ordered host list, falls back on a failed connect and probes the cable while on the Wi-Fi.

**Tech Stack:** Python (aiohttp, Home Assistant config flows, python-zeroconf), pytest via `scripts/test.sh`.

**Spec:** `docs/superpowers/specs/2026-11-21-satellite-lan-first-design.md`

## Global Constraints
- Property names (exact): `wired`, `wifi`; each omitted when that interface has no IPv4.
- Subentry keys (new, in `const.py`): `CONF_HOST_WIRED = "host_wired"`, `CONF_HOST_WIFI = "host_wifi"`. `CONF_HOST` stays and always holds the preferred address (wired if present, else wifi) so older code paths keep working.
- Return to the cable: `CABLE_STABLE_S = 60` (60 s of consecutive successful probes); probe every `CABLE_PROBE_S = 10`.
- Zeroconf abort reason stays `"already_configured"`; the `state == "pairable"` path is untouched.
- An announcement **without** `wired`/`wifi` (old agent) keeps v0.4.2 behaviour except that it no longer reloads (Task 2 handles it via the connection, like the new path).
- Root cause of the double reload (found 21/11): `async_update_subentry` fires `_async_update_listener` (`__init__.py:464`), which reloads because the subentry fingerprint changed, **and** the flow calls `async_schedule_reload`. Both go away: host keys leave the fingerprint, the explicit reload is removed.
- Every `scripts/test.sh` / `test-all.sh` run uses `FRIDAY_TEST_DIR=ha-friday-lanfirst`. Temp files never under `/tmp` directly.
- Git messages in English; never `--no-verify`. Comments in English in `agents/` and `custom_components/`.
- Release notes: controller writes v0.4.3 and a Known limits line in v0.4.2 after execution.

## Review Focus
- MK1 (Wi-Fi only, `wired` absent): host never changes, no reload, no "moved" log — Task 2 `test_zeroconf_wifi_only_stable`.
- The same announcement answered from two IPs (avahi): `discovery_info.ip_address` is ignored when properties are present — Task 2 `test_zeroconf_ignores_answer_ip_with_props`.
- Two announcements 10 s apart with new addresses: zero entry reloads, the connection gets the newest hosts — Task 2 `test_zeroconf_move_never_reloads`.
- Cable flapping (answers 30 s, fails, answers): stays on the Wi-Fi until 60 s continuous — Task 3 `test_returns_only_after_60s_stable`.
- A connection with one host only (MK1): no probing task runs — Task 3 `test_single_host_no_probe`.

---

### Task 1: agent declares its addresses

**Files:**
- Modify: `agents/satellite/friday_satellite_agent/announce.py` (`addresses() -> dict[str, str]`, `announce(..., addrs)`, `Keeper`)
- Modify: `agents/satellite/friday_satellite_agent/__main__.py:475` (Keeper wiring if its signature changes)
- Test: `tests/agents/test_announce.py`

**Interfaces:** Produces `addresses(run=hardware.run) -> dict[str, str]` with keys among `"wired"`, `"wifi"` (connected ethernet / connected wifi IPv4, via `hardware.parse_devices` and `nmcli -g IP4.ADDRESS device show <dev>`). Announcement properties gain those keys; `ServiceInfo.addresses` = preferred first (wired, then wifi), falling back to `lan_ip()` when the dict is empty.

- [ ] **Step 1: Failing tests:** `test_addresses_both` (fake nmcli: eth0 ethernet connected 10.0.0.5, wlan0 wifi connected 10.0.0.6 → `{"wired": "10.0.0.5", "wifi": "10.0.0.6"}`); `test_addresses_wifi_only`; `test_announce_props_carry_addresses` (fake `ServiceInfo` captures properties and addresses order); `test_keeper_reannounces_when_wifi_changes` (wired same, wifi changes → second announce); `test_keeper_same_addresses_no_reannounce`.
- [ ] **Step 2: Run** `FRIDAY_TEST_DIR=ha-friday-lanfirst bash scripts/test.sh tests/agents/test_announce.py` — Expected: new tests FAIL.
- [ ] **Step 3: Implement.** Keeper compares the whole address dict (not one IP); keeps the "no route → keep what is announced" rule; still skips `127.*`.
- [ ] **Step 4: Run** — Expected: PASS. Commit `feat(satellite-agent): announce cable and Wi-Fi addresses`.

### Task 2: zeroconf updates hosts without reloading

**Files:**
- Modify: `custom_components/friday/const.py` (two keys above)
- Modify: `custom_components/friday/config_flow.py` (`async_step_zeroconf`, existing non-pairable branch)
- Modify: `custom_components/friday/__init__.py` (`_reload_fingerprint` drops `CONF_HOST`, `CONF_HOST_WIRED`, `CONF_HOST_WIFI` from subentry data; connection creation passes the host list)
- Test: `tests/integration/test_config_flow.py`, `tests/integration/test_setup.py`

**Interfaces:** Consumes Task 3's `FridayConnection.set_hosts(hosts: list[str], port: int) -> None` and constructor `FridayConnection(hass, hosts: list[str] | str, port, token, subentry_id)` (a `str` = one host). Produces helper `hosts_from(data: Mapping) -> list[str]` in `__init__.py`: `[host_wired, host_wifi]` without empties/duplicates, else `[data[CONF_HOST]]`.

- [ ] **Step 1: Failing tests:** `test_zeroconf_ignores_answer_ip_with_props` (props wired=10.0.0.5 wifi=10.0.0.6, answer IP 10.0.0.6, stored wired 10.0.0.5 / wifi 10.0.0.6 → no update, no reload); `test_zeroconf_new_wired_updates_hosts` (stored wired .5, props wired .7 → subentry `host`=`host_wired`=.7, `set_hosts([".7", ".6"], 8701)` called, `async_schedule_reload` and entry reload NOT called); `test_zeroconf_wifi_only_stable` (props wifi only, matches stored → nothing); `test_zeroconf_move_never_reloads` (two moves in a row → reload count 0, last `set_hosts` wins); `test_zeroconf_old_agent_moves_without_reload` (no props, new answer IP → host updated, `set_hosts` called, no reload); `test_update_listener_ignores_host_change` (`async_update_subentry` changing only hosts → no reload; changing token → reload). Existing v0.4.2 tests that assert `async_schedule_reload` are rewritten to the new expectation.
- [ ] **Step 2: Run** the two test files — Expected: new tests FAIL.
- [ ] **Step 3: Implement.** Props present → wanted = `{wired, wifi}` from props; else wanted = `{host from answer}` as `CONF_HOST`. Update only when stored differs; log info `"Satellite %s addresses: wired=%s wifi=%s"`; call `set_hosts` on the live connection (`entry.runtime_data` connections by subentry id; skip if not loaded). Remove `async_schedule_reload`.
- [ ] **Step 4: Run** — Expected: PASS. Commit `fix(integration): follow satellite addresses without reloading`.

### Task 3: connection prefers the cable and falls back

**Files:**
- Modify: `custom_components/friday/connection.py`
- Test: `tests/integration/test_connection.py`

**Interfaces:** Produces `FridayConnection(hass, hosts: list[str] | str, port: int, token: str, subentry_id: str)`, `set_hosts(hosts, port)`, read-only `host` property (address in use). Constants `CABLE_PROBE_S = 10`, `CABLE_STABLE_S = 60`.

- [ ] **Step 1: Failing tests** (fake `_session`/probe, patched sleep/clock): `test_connects_on_first_host`; `test_falls_back_to_second_host_without_backoff` (first raises `ClientError` → next attempt is hosts[1] immediately, backoff starts only after all hosts failed); `test_returns_only_after_60s_stable` (on Wi-Fi; probe of hosts[0] ok for 30 s, fails, ok for 60 s → switch happens only at the end: closes the ws, reconnects on hosts[0]); `test_single_host_no_probe`; `test_set_hosts_reconnects_when_host_in_use_removed` (in use .6, new list without .6 → reconnect); `test_set_hosts_same_in_use_keeps_session`.
- [ ] **Step 2: Run** `FRIDAY_TEST_DIR=ha-friday-lanfirst bash scripts/test.sh tests/integration/test_connection.py` — Expected: new tests FAIL.
- [ ] **Step 3: Implement.** Probe = TCP connect to `hosts[0]:port` with 3 s timeout (`asyncio.open_connection`, then close); the probe task runs only while `host != hosts[0]` and is cancelled on unload. Switching back closes the ws; `_run` reconnects on `hosts[0]`. Logs at info on each switch: `"Agent %s now on %s"`.
- [ ] **Step 4: Run** — Expected: PASS. Commit `feat(integration): satellite connection prefers the cable`.

Task order: 1, 3, 2 (Task 2 consumes Task 3).

### Task 4: full check
- [ ] **Step 1: Run** `FRIDAY_TEST_DIR=ha-friday-lanfirst bash scripts/test-all.sh` — Expected: every step passes (lone `test_provisioning` timeout = known flake, rerun that step).
- [ ] **Step 2:** `git push -u origin fix/satellite-lan-first`.
