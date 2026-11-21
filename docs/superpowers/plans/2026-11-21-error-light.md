# Satellite Error Light Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The satellite ring shows red after 2 min without network (any fault), and flashes red for 2 s with every error beep.

**Architecture:** netwatch (systemd timer, now every 1 min) records `failing_since`/`last_check` in its state file and
paces the ladder by time; the program's 5 s poll swaps `read_exhausted()` for `read_net_down(now)`. The ring gets a
`flash_error(hold_s)` (XVF only; no-op elsewhere) that the program calls wherever it plays the `erro` sound.

**Tech Stack:** Python 3 stdlib (`unittest`), systemd units, bash migration (`satellite/migrations/`), pytest for
migration tests (`tests/agents/`).

**Spec:** `docs/superpowers/specs/2026-11-21-error-light-design.md`

## Global Constraints
- Constants (exact): `NET_DOWN_LIGHT_S = 120`, `NET_STATE_STALE_S = 180`, `ROUND_S = 300` in `net_recovery.py`;
  `FLASH_ERROR_S = 2.0` in `ring.py`. `ERROR_HOLD_S = 10.0` is unchanged.
- Timer: `OnUnitActiveSec=1min`, `OnBootSec=2min` unchanged.
- The maestro event name `rede_esgotada` and the light name `rede_esgotada` → `net_error` stay (no rename); only the
  value source changes.
- Git messages in English; never `--no-verify`; tests' temp dirs never under `/tmp` directly (test-all sets TMPDIR).
- No change to ladder steps, `MAX_CHAIN_REBOOTS = 3`, `REBOOT_WINDOW_S`, `BOOT_GRACE_S`, the Pi-at-fault test, or the
  "sem Wi-Fi" speech rule.
- Comments/docstrings follow the file's language (pt-BR in `friday_satelite.py`/`maestro.py`/`ring.py`, English in
  `net_recovery.py`/`netwatch.py`/migrations).

## Review Focus
- Network back while red: `read_net_down` must turn False on the first healthy check (`failing_since` cleared) — Task 1 `test_healthy_clears_failing_since`.
- netwatch stops running (unit failed) with `failing_since` set: light must go off after 180 s — Task 1 `test_read_net_down_stale_file_is_false`.
- Reboot mid-episode: new boot clears `failing_since` so an old episode never lights red at once — Task 1 `test_new_boot_clears_failing_since`.
- Ladder pace with the 1-min timer: runs within 300 s of the last counted round take no action — Task 1 `test_rounds_count_at_most_every_round_s`.
- Error beep while the ring is mid-conversation (listen/speak): flash then return to that light, never stuck red — Task 2 `test_flash_error_returns_to_maestro_light`.

---

### Task 1: netwatch records the episode, paces by time, and the 1-min timer ships

**Files:**
- Modify: `satellite/program/net_recovery.py` (constants, `fresh_state`, `_reset`, `decide`, new `read_net_down`; module docstring)
- Modify: `satellite/program/netwatch.py` (docstring "every 1 min"; result dict gains `"failing_since"`)
- Modify: `satellite/program/friday-netwatch.timer` (`OnUnitActiveSec=1min`, Description "every minute")
- Create: `satellite/migrations/0008_netwatch_every_minute.sh`
- Test: `satellite/program/test_net_recovery.py`, `satellite/program/test_netwatch.py`, create `tests/agents/test_migration_0008.py`

**Interfaces:**
- Produces: `net_recovery.read_net_down(now: float, path: str = STATE_PATH) -> bool`; state keys `failing_since`
  (float|None), `last_check` (float|None), `last_round` (float|None); constants above.

- [ ] **Step 1: Adapt the existing tests to time pacing.** In `test_net_recovery.py`, `run_rounds` advances
  `now + i * nr.ROUND_S` (was `now + i`); `test_full_ladder_then_reboot_recorded` expects `{"ts": 5000.0 + 3 * nr.ROUND_S, ...}`.
  In `test_netwatch.py`, each `self.step(...)` that represents a new round advances `self.t[0] += nr.ROUND_S` first
  (a helper in the test class is fine). No assertion about actions changes.
- [ ] **Step 2: Write the new failing tests** in `test_net_recovery.py`:
  - `test_failing_since_set_on_first_failure_even_in_grace_or_conversation`: `decide(fresh, obs(uptime_s=10), 1000.0).state["failing_since"] == 1000.0`; same with `conversation_open=True`; a second failed call at 1060 keeps 1000.0.
  - `test_healthy_clears_failing_since`: state with `failing_since=1000.0` + `obs(healthy=True)` → `failing_since is None`, `last_round is None`.
  - `test_new_boot_clears_failing_since`: state `boot_id="b1", failing_since=1.0` + `obs(boot_id="b2")` at 1000 → `failing_since == 1000.0` (cleared, then set by this failed check).
  - `test_last_check_written_every_run`: any decide at `now=1234.0` → `state["last_check"] == 1234.0` (healthy, failing, no profile).
  - `test_rounds_count_at_most_every_round_s`: from fresh, failed calls at t=0,60,120,…,600 (step 60, uptime large) → actions are `NONE` except `BOUNCE` at t=300 and `RESTART_NM` at t=600; `failed_rounds` after t=600 is 3.
  - `test_read_net_down`: write state `{"failing_since": 1000.0, "last_check": 1110.0}` → `read_net_down(1119.0, p) is False`, `read_net_down(1120.0, p) is True`.
  - `test_read_net_down_stale_file_is_false`: `{"failing_since": 1000.0, "last_check": 1000.0}` → `read_net_down(1181.0, p) is False`, `read_net_down(1180.0, p) is True`.
  - `test_read_net_down_missing_corrupt_or_none`: missing file, `"x"` text, `{"failing_since": None, "last_check": 1.0}`, `{"failing_since": "a", "last_check": 1.0}` → all False.
- [ ] **Step 3: Run** `cd satellite/program && python3 -m unittest -q test_net_recovery test_netwatch` — Expected: the new tests FAIL (KeyError/AttributeError), adapted old tests may fail too.
- [ ] **Step 4: Implement in `net_recovery.py`.** `fresh_state()` adds the three keys as `None`; `load_state` keeps them
  (it already copies known keys). `_reset` also sets `failing_since = last_round = None`. In `decide`, in this order:
  set `s["last_check"] = now` first; on a new boot also clear `failing_since` and `last_round`; after the healthy /
  no-profile returns, set `failing_since = now` if it is not a number; keep rule 5 (grace/conversation → NONE);
  then a new rule before rule 6: if `last_round` is a number and `now - last_round < ROUND_S` → `Decision(NONE, s, False)`;
  else `s["last_round"] = now` and continue with rule 6 unchanged (`failed_rounds += 1`, `FAILED_ROUNDS_BEFORE_ACTION`
  stays 2). `read_net_down(now, path)` returns True only when both values are int/float, `now - last_check <= NET_STATE_STALE_S`
  and `now - failing_since >= NET_DOWN_LIGHT_S`; never raises (OSError/ValueError → False). `read_exhausted` stays.
- [ ] **Step 5: netwatch + timer.** `netwatch.py` docstring says it runs every minute and the ladder counts a round at
  most every 5 min; `result` gains `"failing_since": st["failing_since"]` (add the key to the existing
  `test_healthy_round_runs_nothing_and_is_silent` key list). Timer: `OnUnitActiveSec=1min`, `Description=Check the satellite Wi-Fi every minute`.
- [ ] **Step 6: Migration test first** — `tests/agents/test_migration_0008.py`, same `run(root)` helper as `test_migration_0007.py`:
  - `test_0008_writes_dropin`: with `FRIDAY_OTA_ROOT=tmp`, exit 0, file `etc/systemd/system/friday-netwatch.timer.d/interval.conf` == `"[Timer]\nOnUnitActiveSec=\nOnUnitActiveSec=1min\n"`.
  - `test_0008_idempotent`: run twice → exit 0, same content, one file in the dir.
  Run `python3 -m pytest -q tests/agents/test_migration_0008.py` — Expected: FAIL (script missing).
- [ ] **Step 7: Write `0008_netwatch_every_minute.sh`** in the style of 0002 (header comment with date and why, `set -uo pipefail`,
  `R=${FRIDAY_OTA_ROOT:-}`): `mkdir -p`, write the drop-in atomically (tmp + `mv`), and only when `R` is empty run
  `systemctl daemon-reload` and `systemctl restart friday-netwatch.timer` (both `>/dev/null 2>&1 || true`); echo `0008: netwatch every minute`. `chmod +x`.
- [ ] **Step 8: Run** `cd satellite/program && python3 -m unittest -q test_net_recovery test_netwatch` and `python3 -m pytest -q tests/agents/test_migration_0008.py` — Expected: all pass.
- [ ] **Step 9: Commit** `feat(satellite): netwatch records the outage and runs every minute`.

### Task 2: ring flashes the momentary error for a given time

**Files:**
- Modify: `satellite/program/ring.py` (`FLASH_ERROR_S`, `_Composto.flash_error`, `Xvf3800Ring._comecar_erro(hold)`)
- Test: `satellite/program/test_ring.py`

**Interfaces:**
- Produces: `ring.FLASH_ERROR_S = 2.0`; `ring.<any driver>.flash_error(hold_s: float = FLASH_ERROR_S) -> None`.
  On `Xvf3800Ring` it behaves like `show("error")` with `hold_s` instead of `ERROR_HOLD_S`; on `_Composto` (Jabra, Null) it does nothing.

- [ ] **Step 1: Failing tests** in `TestXvf` (use the existing fake timer/transport `self.timers`, `self.t`, `W`):
  - `test_flash_error_lasts_flash_s_and_returns`: `r.show("speak")`; `r.flash_error()`; last writes are the `error` frame (`W("LED_COLOR", 0xFF0000)` … `W("LED_EFFECT", 1)`); `self.timers[-1].atraso == ring.FLASH_ERROR_S == 2.0`; firing it restores `W("LED_EFFECT", 3)`.
  - `test_flash_error_returns_to_maestro_light`: `r.show_maestro("listen")`; `r.flash_error()`; fire timer → the last frame is the listen frame (compare to the writes `show_maestro("listen")` produced on a fresh ring).
  - `test_new_external_state_during_flash_cancels_return`: as `test_estado_novo_durante_o_erro_cancela_a_volta`, with `flash_error()`.
  - `test_show_error_still_holds_10s`: `r.show("error")` → `self.timers[-1].atraso == ring.ERROR_HOLD_S`.
  In `TestJabra`: `test_flash_error_is_noop_on_jabra`: `ring.JabraRing(j).flash_error()` → the fake Jabra recorded no write.
- [ ] **Step 2: Run** `python3 -m unittest -q test_ring` — Expected: FAIL (`flash_error` missing).
- [ ] **Step 3: Implement.** `_Composto.flash_error(self, hold_s=FLASH_ERROR_S)`: no-op unless `self.ERRO_MOMENTANEO`;
  otherwise the same body as `show("error")` but passing `hold_s` to `_comecar_erro`. Change `Xvf3800Ring._comecar_erro(self, hold=ERROR_HOLD_S)`
  and `_Composto._comecar_erro(self, hold=ERROR_HOLD_S)`; `show` keeps calling it with the default. Update the module docstring line on the momentary error.
- [ ] **Step 4: Run** `python3 -m unittest -q test_ring` — Expected: OK.
- [ ] **Step 5: Commit** `feat(satellite): ring can flash the momentary error for a set time`.

### Task 3: program lights red from the outage and flashes with the error beep

**Files:**
- Modify: `satellite/program/friday_satelite.py` (`tique`, `_agir`)
- Modify: `satellite/program/maestro.py` (comments only: `rede_esgotada` now means "sem rede há ≥ 2 min")
- Test: `satellite/program/test_satelite_ligacao.py`

**Interfaces:**
- Consumes: `net_recovery.read_net_down(now)` (Task 1), `anel.flash_error()` (Task 2).

- [ ] **Step 1: Failing tests** in `test_satelite_ligacao.py`:
  - Update `test_tique_polls_the_state_file_at_most_every_5_s`: asserts `"net_recovery.read_net_down(time.time())"` and `'"net_down"'` in the `tique` body (instead of `read_exhausted(` / `"net_exhausted"`); the other asserts stay.
  - `test_som_de_erro_pisca_o_anel` (async, beside `test_sons_turno_falso_pula_inicio_e_fim_mas_nao_o_erro`): replace `self.s.anel` with a `types.SimpleNamespace(flash_error=lambda *a: vistos.append("flash"), show_maestro=lambda *a, **k: None)`; `_agir("som", "erro")` → `vistos == ["flash"]` and `F.sons == ["erro_b.wav"]`; `_agir("som", "fim")` adds nothing to `vistos`.
  - `test_som_de_erro_pisca_mesmo_com_sons_de_turno_desligados`: `cfg["sons_turno"] = False`; `_agir("som", "erro")` → `vistos == ["flash"]`.
- [ ] **Step 2: Run** `python3 -m unittest -q test_satelite_ligacao` — Expected: the three FAIL.
- [ ] **Step 3: Implement.** In `tique`, `v = net_recovery.read_net_down(time.time())` and the log line `{"net_down": v}`
  (comment: spec 2026-11-21, red after 2 min without network, any fault). In `_agir`, branch `"som"`: when `arg == "erro"`
  call `self.anel.flash_error()` (after the sound starts or before; one line, comment citing spec 2026-11-21), whether or
  not another rule skips turn sounds (the error is always allowed). Update the `maestro.py` comments around
  `rede_esgotada` and `_repouso` (no logic change).
- [ ] **Step 4: Run the whole satellite suite** `cd satellite/program && python3 -m unittest -q` — Expected: `OK`.
- [ ] **Step 5: Commit** `feat(satellite): red after 2 min without network and a red flash with every error beep`.

### Task 4: docs and full check

**Files:**
- Modify: `docs/` page that documents the satellite lights/network recovery, if one names "exhausted" as the red-light trigger (`grep -rn "net_error\|esgotad\|exhausted" docs --include=*.md` outside `superpowers/`); update the sentence only.
- [ ] **Step 1:** Make that doc edit (skip with a ledger note if no such page).
- [ ] **Step 2: Run** `scripts/test-all.sh` — Expected: every step passes (a lone `test_provisioning` timeout is a known flake: rerun that step alone).
- [ ] **Step 3: Commit** `docs(satellite): red light after 2 min without network` (only if Step 1 changed a file).

Release notes are not part of this plan: the version depends on the merge order with PR #45 and are written with the
release (skill `writing-friday-release-notes`), with the spec's "To confirm on the alpha" checks as promotion gates.
