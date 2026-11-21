# Recordings and Host Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recordings play on code-adopted satellites, the card says why one does not, extra words record their no-voice rejections, and the integration follows a satellite that announces a new address.

**Architecture:** four independent fixes: the satellite agent's recordings routes accept a set of tokens; the integration's proxy view maps outcomes to 404/403/502 and the card names them; the program's extra-word rejection branch also records `sem_voz`; the zeroconf step updates a known satellite's host/port.

**Tech Stack:** Python (aiohttp, Home Assistant config flows, pytest via `scripts/test.sh`; `unittest` in `satellite/program`), plain JS (`node --test`).

**Spec:** `docs/superpowers/specs/2026-11-21-recordings-and-host-design.md`

## Global Constraints
- Card notes (exact): 404 → `"recording not ready"`, 403 → `"satellite refused"`, 502 → `"satellite offline"`, any other non-OK → `` `error ${status}` `` (as today).
- File prefix of an extra word's no-voice clip: `f"{word}_sem_voz_"`; registry line `tipo="sem_voz"`, `palavra=<word>`.
- Zeroconf abort reason stays `"already_configured"`; a `state == "pairable"` announcement keeps its current path untouched.
- Every `scripts/test.sh` / `scripts/test-all.sh` run uses `FRIDAY_TEST_DIR=ha-friday-recfix` (another worktree runs on the VM in parallel under the default folder). `scripts/test.sh <pytest args>` runs pytest on the VM, e.g. `FRIDAY_TEST_DIR=ha-friday-recfix bash scripts/test.sh tests/agents/test_satellite_recordings.py`.
- Git messages in English; never `--no-verify`; temp files never under `/tmp` directly.
- Comments follow the file's language (English in `agents/` and `custom_components/`, pt-BR in `satellite/program/`).

## Review Focus
- Legacy satellite (local password == HA token): both still work — Task 1 `test_same_token_twice_is_fine`.
- Caller with no/other token never gets a file — Task 1 `test_wrong_token_401`.
- Device in the registry but its connection has no hello yet (satellite offline at HA start): 502, not 404 — Task 2 `test_view_502_when_known_but_not_connected`.
- Zeroconf from an unknown id or with `state=pairable` never rewrites a configured host — Task 4 `test_zeroconf_pairable_does_not_move_host`.
- A recording already in progress when an extra word is rejected without voice: no second clip, the event carries `recording=None` — Task 3 source check `test_sem_voz_da_extra_respeita_gravando`.

---

### Task 1: agent recordings routes accept the HA token

**Files:**
- Modify: `agents/satellite/friday_satellite_agent/recordings.py` (`add_routes(app, tokens, folder)`; `_authorized(request, tokens)`)
- Modify: `agents/satellite/friday_satellite_agent/__main__.py:469` → `recordings.add_routes(app, (ha_token, token), RECORDINGS)`
- Test: `tests/agents/test_satellite_recordings.py`, `tests/agents/test_satellite_main.py` (only if it asserts the call shape)

**Interfaces:** Produces `add_routes(app: web.Application, tokens: tuple[str, ...] | list[str] | str, folder: str) -> None`; a plain `str` is accepted as one token (keeps old callers/tests).

- [ ] **Step 1: Failing tests** in `test_satellite_recordings.py` (new fixture variant with `add_routes(app, ("ha", "local"), folder)`):
  `test_ha_token_serves` (`Bearer ha` → 200 on `/v1/recordings/20261103_100215.wav`), `test_local_token_serves` (`Bearer local` → 200),
  `test_wrong_token_401` (`Bearer other`, no header, `Bearer ` empty → 401 on all three routes),
  `test_same_token_twice_is_fine` (`("tok","tok")` → `Bearer tok` 200), `test_empty_token_in_set_never_matches` (`("", "local")` + `Bearer ` → 401).
  Existing tests using `add_routes(app, "tok", …)` stay green.
- [ ] **Step 2: Run** `FRIDAY_TEST_DIR=ha-friday-recfix bash scripts/test.sh tests/agents/test_satellite_recordings.py` — Expected: new tests FAIL.
- [ ] **Step 3: Implement.** Normalize `tokens` to a tuple of non-empty strings; `_authorized` uses `hmac.compare_digest` against each. Update `__main__.py` (comment: 21/11, code-adopted satellites: HA presents `ha_token`; legacy ones present the local password).
- [ ] **Step 4: Run** the same command — Expected: PASS. Commit `fix(satellite-agent): recordings accept the HA token`.

### Task 2: proxy view and card name what happened

**Files:**
- Modify: `custom_components/friday/recordings.py` (`RecordingView.get`; helper `device_known(hass, device_id) -> bool` or a split of `find_connection`)
- Modify: `custom_components/friday/frontend/activations-format.js` (new export `playNote(status: number) -> string`)
- Modify: `custom_components/friday/frontend/friday-activations-card.js` (`_play` uses `playNote`)
- Test: `tests/integration/test_recordings.py`, `tests/frontend/activations-format.test.mjs`

- [ ] **Step 1: Failing tests.** `test_recordings.py`: `test_view_403_when_agent_refuses` (fake agent answers 401 → view 403; also 403 → 403); `test_view_502_when_known_but_not_connected` (device exists in the registry with the domain identifier but no loaded connection has its hello → 502); `test_view_502_on_agent_5xx` (agent 500 → 502); existing `test_view_404s` (unknown device, bad name, agent 404) still 404. Frontend: `playNote(404) === "recording not ready"`, `playNote(403) === "satellite refused"`, `playNote(502) === "satellite offline"`, `playNote(500) === "error 500"`, `playNote(200) === ""`.
- [ ] **Step 2: Run** `bash scripts/test.sh` subset for `tests/integration/test_recordings.py` and `node --test tests/frontend/activations-format.test.mjs` — Expected: new ones FAIL.
- [ ] **Step 3: Implement.** In `get`: bad name → 404; device not in registry or without a `DOMAIN` identifier → 404; no connection with that id's hello → 502; client error/timeout → 502; agent 200 → stream; 401/403 → 403; 404 → 404; anything else → 502. Card `_play`: `if (!res.ok) this._notes[key] = playNote(res.status); else …` (the `catch` stays "satellite offline").
- [ ] **Step 4: Run** both — Expected: PASS. Commit `fix(integration): recordings proxy says refused or offline instead of not ready`.

### Task 3: extra words record their no-voice rejections

**Files:**
- Modify: `satellite/program/registro_palavra.py` (`prefixo_sem_voz_extra(nome) -> str` returning `f"{nome}_sem_voz_"`)
- Modify: `satellite/program/friday_satelite.py` (extra-word branch now at the `r_ and r_["causa"] == "paciencia"` block, ~line 1200)
- Modify: `custom_components/friday/recordings.py` (`title_for` prefix fallback)
- Test: `satellite/program/test_registro_palavra.py`, `satellite/program/test_satelite_ligacao.py` (source checks, class of `test_tique_polls_the_state_file_at_most_every_5_s`), `tests/integration/test_recordings.py`

- [ ] **Step 1: Failing tests.** `test_registro_palavra.py`: `rp.prefixo_sem_voz_extra("sexta_feira") == "sexta_feira_sem_voz_"`. Source checks in `test_satelite_ligacao.py`: in the capture-loop source, the extra-word rejection condition covers `("paciencia", "sem_voz")`, uses `registro_palavra.prefixo_sem_voz_extra(nome_e)` for `sem_voz`, writes `registro_palavra.linha("sem_voz", …, palavra=nome_e)`, and keeps `gravando is None` and `self.cfg.get("gravar_ativacoes")` in that condition (`test_sem_voz_da_extra_grava_como_a_paciencia`, `test_sem_voz_da_extra_respeita_gravando`). `test_recordings.py` `test_title_from_prefix` gains: `title_for("sexta_feira_sem_voz_20261121_185817.wav", None)` ends `"rejected (no voice)"`, `title_for("sexta_feira_paciencia_20261121_185336.wav", None)` ends `"rejected (patience)"`.
- [ ] **Step 2: Run** `cd satellite/program && python3 -m unittest -q test_registro_palavra test_satelite_ligacao` and the integration test — Expected: new ones FAIL.
- [ ] **Step 3: Implement.** Generalize the block: when `r_` has `causa in ("paciencia", "sem_voz")`, mode `ativa`, `r_["gravacao"] is None`, recordings on and `gravando is None`: name with `prefixo_paciencia_extra` or `prefixo_sem_voz_extra`, clip `trecho_do_episodio(self.contexto, r_["quadros"])` as today, `r_["gravacao"] = nome`, registry line of type `r_["causa"]` with `palavra=nome_e`. Comment: spec 2026-11-21 (TV negatives on the MK2). Confirm `r_["quadros"]` is filled for `sem_voz` episodes in `detectores.py` (it is set per episode; if not, add it there with a `test_detectores.py` test and a Ruling). `title_for`: `prefix == "sem_voz" or prefix.endswith("_sem_voz")` → `"rejected (no voice)"`; `prefix.endswith("_paciencia")` → `"rejected (patience)"`, both before the `_quase` branch.
- [ ] **Step 4: Check the wake-word tools** still pass with the new prefix: `bash scripts/test.sh` subset `tests/tools` (or the `tools/wakeword` unittest suites) — Expected: PASS; if a tool parses recording names by a closed regex that rejects `<word>_sem_voz_…`, extend it with a test and a Ruling.
- [ ] **Step 5: Run** the Task 3 tests — Expected: PASS. Commit `feat(satellite): extra words record their no-voice rejections`.

### Task 4: integration follows a satellite that announces a new address

**Files:**
- Modify: `custom_components/friday/config_flow.py` (`async_step_zeroconf`, the `existing is not None` branch)
- Test: `tests/integration/test_config_flow.py`

- [ ] **Step 1: Failing tests** after `test_zeroconf_discovery_offers_satellite`'s setup: `test_zeroconf_new_address_updates_host` (same id `sat-1`, `ip_address` `10.0.0.10`, port `8701` → abort `already_configured`, subentry `host == "10.0.0.10"`, entry reloaded once: patch `hass.config_entries.async_schedule_reload` and assert the call); `test_zeroconf_same_address_does_nothing` (no update, no reload); `test_zeroconf_new_port_updates` (port `8702`); `test_zeroconf_pairable_does_not_move_host` (`state="pairable"`, other IP → the pair path as today, subentry host unchanged).
- [ ] **Step 2: Run** the config-flow tests — Expected: new ones FAIL.
- [ ] **Step 3: Implement.** In the `existing is not None` branch (non-pairable): if `(existing.data[CONF_HOST], existing.data[CONF_PORT]) != (host, port)`: `async_update_subentry(entries[0], existing, data={**existing.data, CONF_HOST: host, CONF_PORT: port})`, log at info `"Satellite %s moved to %s:%s"`, `self.hass.config_entries.async_schedule_reload(entries[0].entry_id)`; then abort `already_configured`.
- [ ] **Step 4: Run** — Expected: PASS. Commit `feat(integration): follow a satellite that announces a new address`.

### Task 5: full check
- [ ] **Step 1: Run** `scripts/test-all.sh` — Expected: every step passes (a lone `test_provisioning` timeout is a known flake: rerun that step alone).
- [ ] **Step 2:** `git push`.

Release notes are updated by the controller before execution (v0.4.2, same alpha as #45 and #46).
