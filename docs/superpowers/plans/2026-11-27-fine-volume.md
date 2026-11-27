# Fine Satellite Volume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Music Assistant (MA) volume works again on MK1 and MK2, and every percent 1-100 has its own loudness.

**Architecture:** In the house, one `input_number` mirror per satellite, bound both ways to the integration's volume entity (Task 1, no OTA). In the satellite, the hardware step sits at or above the asked loudness and a new ALSA softvol `Fine` attenuates the rest (Tasks 2-7, OTA).

**Tech Stack:** Home Assistant YAML package, Python 3 stdlib (satellite program and agent), ALSA `asound.conf`, bash migration.

**Spec:** `docs/superpowers/specs/2026-11-27-fine-volume-design.md` (read it first)

## Global Constraints

- Repo `/config/dev/ha-friday`, branch `feat/fine-volume` (from `origin/master`). Task 1 edits `/config` (the house), not the repo.
- Git in English. Commit per task. No push, PR, merge, tag or deploy without the owner's OK.
- Never read or print `satellites.json`, tokens, `local.env`, passwords or `/etc/friday-satelite/config.json` as a whole (a hook refuses; do not work around it). `sh /share/friday/sat_info.sh` is the safe registry reader.
- Satellites: MK1 (Jabra, card `USB`) and MK2 (XVF3800, card `Array`, Living Room). `<MK1_HOST>`, `<MK2_HOST>`, `<MK1_SHORT>`, `<MK2_SHORT>` and the MA player ids are in `/config/docs/claude/fine-volume-house-values.md` (outside the repo). In `/config` files write the real values, never the markers; never commit them to the repo. Never run heavy or parallel jobs on a Pi 3.
- Tests: satellite program `cd satellite/program && python3 -m unittest -q`; agent and repo tests `bash scripts/test.sh <path>` (runs on the VM); everything `bash scripts/test-all.sh`. Tests that create temp folders must remove them.
- Volume is never a mute. Sendspin's own volume stays at 100. `Music` stays the ducking control.
- Satellites receive changes only by OTA (alpha first). Never `deploy.sh satellite` on an OTA satellite.
- Changing a volume in the house is audible: change only when nothing is playing on that player (or ask the owner), and restore the previous value.

## Review Focus

1. MA slider dragged with music playing: it never jumps back and loudness never overshoots (Task 1 Step 6, Task 3 write-order tests, Task 7).
2. Program restart or satellite offline with music: mirror keeps its value, loudness does not change by itself (Task 1 Step 6, Task 7).
3. Nightly HA restart: the restored mirror never overrides a volume changed on the satellite (Task 1 test `test_restore_is_not_sent`).
4. Rollback after the new config was written, then a volume change on the old program: the newer change wins on the next update (Task 4 test).
5. XVF3800 or Jabra unplugged and replugged: `Fine` comes back with the right trim, never stuck loud (Task 4 `iniciar` test, Task 7).

---

### Task 1: House mirrors for MK1 and MK2 (fixes the 401, no OTA)

**Files:**
- Modify: `/config/packages/friday_satellite_volume.yaml` (rewrite)
- Modify: `/config/tools/friday_music/test_satellite_volume_package.py` (rewrite)
- Modify: `/config/secrets.yaml` (remove `friday_satellite_volume_webhook`, `friday_satellite_office_port_bearer`)

**Interfaces:**
- Produces: `input_number.friday_satellite_volume` (MK1, name `Volume do satélite do escritório`), `input_number.friday_satellite_living_room_volume` (MK2, name `Volume do satélite da sala`).

- [ ] **Step 1: Back up** both files to `<file>.bak.20261127_fine_volume`. For `secrets.yaml` use `cp -p` and never print it.

- [ ] **Step 2: Rewrite the test.** Keep the existing YAML loader. `PAIRS = {"office": ("input_number.friday_satellite_volume", "number.friday_sat_<MK1_SHORT>_volume"), "living_room": ("input_number.friday_satellite_living_room_volume", "number.friday_sat_<MK2_SHORT>_volume")}`. Automation ids are `friday_volume_<room>_to_mirror` and `friday_volume_<room>_to_satellite`. Tests:
  - `test_mirrors_are_percent_0_100`: both helpers have `(min, max, step) == (0, 100, 1)`.
  - `test_no_secret_ip_webhook_or_rest_command`: the file text has none of `!secret`, `192.168.`, `webhook`, `rest_command`.
  - `test_four_automations_bind_both_ways`: for each pair, `to_mirror` triggers on the number and writes the mirror with `input_number.set_value`; `to_satellite` triggers on the mirror and writes the number with `number.set_value`.
  - `test_to_mirror_settles_1s`: trigger id `settled` on the number has `for: {seconds: 1}`; trigger id `reconcile` on the mirror has `for: {seconds: 3}`.
  - `test_stale_value_cannot_pull_a_drag_back`: the `to_mirror` condition requires, unless `trigger.id == 'reconcile'`, that the mirror's `last_changed` is at least 1 s old.
  - `test_both_write_only_when_values_differ`: each condition template contains `| float` on both entities and `!=`.
  - `test_restore_is_not_sent`: the `to_satellite` condition checks `trigger.from_state is not none` and `from_state.state not in ['unknown', 'unavailable']`.
  - `test_unavailable_number_is_ignored`: both conditions require the number's state not in `['unknown', 'unavailable']`.
  - `test_latest_wins`: all four have `mode: restart`.
  - `test_no_cross_wiring`: no automation of one room mentions the other room's entities (compare whole entity ids).

- [ ] **Step 3: Run** `cd /config/tools/friday_music && python3 -m unittest -q test_satellite_volume_package`. Expected: FAIL.

- [ ] **Step 4: Rewrite the package.** Header comment in Portuguese (like the old one) pointing to the spec. The MK1 pair, exactly; repeat for `living_room` with its entities (aliases `Satélite escritório – …` / `Satélite sala – …`, helper icon `mdi:speaker`, unit `%`, `mode: slider`):

```yaml
automation:
  - id: friday_volume_office_to_mirror
    alias: Satélite escritório – volume para o espelho
    mode: restart
    triggers:
      - trigger: state
        id: settled
        entity_id: number.friday_sat_<MK1_SHORT>_volume
        to: null
        for:
          seconds: 1
      # o número pode ter ficado parado enquanto o espelho mudava (arraste no MA): confere de novo quando o espelho para
      - trigger: state
        id: reconcile
        entity_id: input_number.friday_satellite_volume
        to: null
        for:
          seconds: 3
    conditions:
      - condition: template
        value_template: >-
          {% set n = states('number.friday_sat_<MK1_SHORT>_volume') %}
          {% set m = states('input_number.friday_satellite_volume') %}
          {{ n not in ['unknown', 'unavailable'] and (n | float(-1)) != (m | float(-1))
             and (trigger.id == 'reconcile'
                  or as_timestamp(now()) - as_timestamp(states.input_number.friday_satellite_volume.last_changed) >= 1) }}
    actions:
      - action: input_number.set_value
        target:
          entity_id: input_number.friday_satellite_volume
        data:
          value: "{{ states('number.friday_sat_<MK1_SHORT>_volume') | float }}"

  - id: friday_volume_office_to_satellite
    alias: Satélite escritório – volume para o satélite
    mode: restart
    triggers:
      - trigger: state
        entity_id: input_number.friday_satellite_volume
        to: null
    conditions:
      - condition: template
        value_template: >-
          {% set n = states('number.friday_sat_<MK1_SHORT>_volume') %}
          {% set m = states('input_number.friday_satellite_volume') %}
          {{ trigger.from_state is not none
             and trigger.from_state.state not in ['unknown', 'unavailable']
             and m not in ['unknown', 'unavailable']
             and n not in ['unknown', 'unavailable']
             and (n | float(-1)) != (m | float(-1)) }}
    actions:
      - delay:
          milliseconds: 150
      - action: number.set_value
        target:
          entity_id: number.friday_sat_<MK1_SHORT>_volume
        data:
          value: "{{ states('input_number.friday_satellite_volume') | float | round(0) | int }}"
```

Remove the two secrets from `secrets.yaml` (delete their lines only, e.g. `sed -i '/^friday_satellite_volume_webhook:/d;/^friday_satellite_office_port_bearer:/d'`). Run the test again: PASS.

- [ ] **Step 5: Load.** `POST /core/api/config/core/check_config` must return `valid`. Then call `input_number.reload` and `automation.reload` (`http://supervisor/core/api/services/<domain>/reload` with `$SUPERVISOR_TOKEN`). `rest_command.reload` drops the old command. Check that both helpers exist, the four automations are `on`, and the two old automations are gone or `unavailable`. Old registry entries may be removed with the owner's OK only. Expose neither helper to Assist or Alexa.

- [ ] **Step 6: Live proof** (idle player, or the owner's OK), for each satellite. Record the current value, then:
  1. Set the mirror (as MA would, REST `input_number/set_value`) to a value in another step, e.g. 55. Expect within 3 s: number changes, then mirror equals number (before the OTA: the 12-step value), and `amixer` on the Pi shows the matching position (MK1 `amixer -c USB sget PCM`, MK2 `amixer -c Array sget PCM`).
  2. Set the integration number: the mirror follows within 3 s.
  Before the OTA, a move inside one 12-step bucket snaps the mirror back after 3 s (the satellite's truth); expected until Task 7.
  3. Send 10 mirror values 0.2 s apart: after 5 s mirror == number and neither changes in the next 10 s (history shows no ping-pong).
  4. On MK1 press the Jabra button: the mirror follows.
  5. `sudo systemctl restart friday-satelite` on the Pi (idle, no timers): while down the mirror keeps its value; after return, mirror == number and the PCM position is unchanged.
  6. Restore the first value. `ha core logs` has no error from these automations.

- [ ] **Step 7: MA settings (owner, in the MA UI).** MK1 already uses its mirror: nothing to change. Before changing MK2, read `sudo cat /var/lib/sendspin-cli/state` on MK2 (27/11: `volume = 100`; if lower, tell the owner music will get louder) and have the owner note the player's current "Volume control". Then the owner selects `Volume do satélite da sala` for the MK2 player (if the option is missing, the helper must first be added to the Home Assistant plugin's player volume controls). Owner drags both sliders; confirm sound and ring follow and Sendspin state stays `volume = 100`.

- [ ] **Step 8: House docs.** Update the bridge text in `/config/docs/claude/friday-producao.md`, `/config/tools/satelite/new-satellite-runbook.md` §6 (new satellite = add helper + the two automations from this package + test pair + MA Volume control) and the memory note `project_satellite_media.md`. Leave the old specs, plans and `.bak` files alone.

---

### Task 2: Volume scale (pure module)

**Files:**
- Create: `satellite/program/escala.py`, `satellite/program/test_escala.py`
- Modify: `satellite/program/placa.py` (nothing renamed; `VOLUME_XVF[0]` becomes 26 with a comment)

**Interfaces:**
- Produces:
  - `escala.TABELAS: dict[str, tuple[float, ...]]` with keys `"jabra"`, `"xvf3800"`: 12 dB values. Jabra `-36 + 4*k`; XVF `placa.VOLUME_XVF[k] - 60` (build it in `escala` without a circular import).
  - `grade(k: int) -> int` = `round(k * 100 / 11)`
  - `posicao_de_pct(pct: int) -> int`: lowest k with `grade(k) >= pct`
  - `alvo_db(pct: int, tabela) -> float`: linear between `(grade(k), tabela[k])`
  - `trim_db(pct: int, tabela) -> float` = `alvo_db - tabela[posicao_de_pct(pct)]` (<= 0)
  - `pct_de(posicao: int, trim: float, tabela) -> int`: the percent whose `alvo_db` is nearest to `tabela[posicao] + trim`, clamped to 0..100
  - All reject non-int / out of range percent with `ValueError` (bool is not an int here).

- [ ] **Step 1: Tests**, for both tables:

```python
def test_grid_percents_keep_todays_loudness(self):
    self.assertEqual([e.grade(k) for k in range(12)], [0, 9, 18, 27, 36, 45, 55, 64, 73, 82, 91, 100])
    for k in range(12):
        self.assertEqual((e.posicao_de_pct(e.grade(k)), e.trim_db(e.grade(k), t)), (k, 0))
def test_loudness_strictly_rises(self):
    vals = [e.alvo_db(p, t) for p in range(101)]
    self.assertTrue(all(b - a >= 0.1 for a, b in zip(vals, vals[1:])))
def test_trim_is_within_the_softvol_range(self):
    self.assertTrue(all(-6.0 < e.trim_db(p, t) <= 0 for p in range(101)))
def test_percent_survives_position_and_trim(self):
    for p in range(101):
        self.assertEqual(e.pct_de(e.posicao_de_pct(p), e.trim_db(p, t), t), p)
def test_floor(self):
    self.assertEqual((e.TABELAS["jabra"][0], e.TABELAS["xvf3800"][0]), (-36, -34))
```

- [ ] **Step 2: Run** `cd satellite/program && python3 -m unittest -q test_escala`. Expected: FAIL (no module).
- [ ] **Step 3: Implement.** Also run `test_placa` and update any expectation of position 0 = raw 0.
- [ ] **Step 4: Run** the whole program suite. Expected: OK.
- [ ] **Step 5: Commit** `feat(satellite): fine volume scale`.

---

### Task 3: Fine mixer, volume control, config and program wiring

**Files:**
- Create: `satellite/program/mixer_fino.py`, `satellite/program/test_mixer_fino.py`
- Modify: `satellite/program/volume.py`, `comandos.py`, `configuracao.py`, `placa.py`, `hardware.py`, `friday_satelite.py`
- Test: `test_volume.py`, `test_comandos.py`, `test_configuracao.py`, `test_volume_inicial.py`, `test_placa.py`

**Interfaces:**
- Consumes: Task 2.
- Produces:
  - `hardware.FineMixer(card="USB", control="Fine")`: `write(trim_db: float)` runs `amixer -q -c <card> sset Fine <round((max(trim_db, -6.0) + 6.0) * 20)>` (0..120, so 120 = 0 dB; never below 1, 0 mutes); `prime()` plays 0.1 s of silence on PCM `voice`. XVF card `Array`.
  - `mixer_fino.MixerFino(hw, fino, tabela, agendar, atraso_s=0.4)`; `hw` has `ler()/escrever(pos)`, `agendar(delay_s, fn) -> handle` with `.cancel()`. Methods: `escrever(pct)`, `sincronizar(posicao)` (writes the board position, same trim, cancels a pending write), `reaplicar(pct)` (alias of `escrever` that writes the board even if unchanged), `posicao -> int` (last asked), `trim -> float`, `ler() -> int` (board position).
  - `placa.montar(...)` returns also `"fine_mixer"` and `"tabela"` (`escala.TABELAS[...]`).
  - `volume.ControleVolume(mixer: MixerFino-like, gravar(pct), pct_inicial, alertas_numero=6)`; `.normal`, `.atual` are percents; `.posicao` property; `definir(pct)`, `passo(delta)`, `apertou(delta, agora)`, `tique`, `entrar_alerta(rampa_s=0, agora=None)`, `sair_alerta`, `iniciar`. The `avisar` parameter and the neighbour-write trick stay only where needed: `iniciar` still writes a neighbouring board position first (driver cache).
  - `volume.posicao_de_percentual` is removed; `posicao_de_numero` stays. `volume.descrever(pct) -> {"posicao": k, "volume": k if k <= 9 else (10 if k == 11 else None), "percentual": pct}`.
  - `configuracao`: new key `volume_pct` (default None, limits int 0..100); `pct_efetivo(cfg) -> int | None`: None if both are None; `grade(volume)` if `volume_pct` is None or `posicao_de_pct(volume_pct) != volume` (when `volume` is not None); else `volume_pct`. `volume_webhook_url` leaves `PADROES`.

- [ ] **Step 1: `test_mixer_fino.py`** with fakes that append `("hw", pos)` / `("fine", trim)` to one list and a manual scheduler:

```python
def test_same_step_writes_only_the_trim()          # 73 -> 70 (both step 8): log == [("fine", trim70)]
def test_step_up_lowers_trim_now_and_board_later()  # 73 -> 75: [("fine", t75)], then scheduler fires -> ("hw", 9)
def test_step_down_moves_board_first()              # 75 -> 70: [("hw", 8), ("fine", t70)], nothing scheduled
def test_latest_wins_while_pending()                # 73->75->85->70 before firing: no ("hw", 9|10) ever; pending cancelled
def test_never_louder_than_old_or_new():            # for every (a, b) in 0..100 step 7: after each logged write,
    # tabela[hw] + trim <= max(alvo(a), alvo(b)) + 1e-9, treating a "fine" write that RAISES the trim as effective
    # only when the board write already happened (that is the safe order)
def test_new_trim_decrease_restarts_the_delay()     # board waits atraso_s after the LAST trim decrease
def test_sincronizar_keeps_trim_and_cancels_pending()
```

- [ ] **Step 2: Update `test_volume.py`.** Replace positions by percents; the fake mixer exposes the methods above. Keep every existing behaviour test (alert, ramp over grid percents `grade(1..alvo)`, button during alert, debounce) and add:

```python
def test_set_percent_is_kept_exactly():      c.definir(70) -> normal 70, descrever -> {"posicao": 8, "volume": 8, "percentual": 70}
def test_step_keeps_the_trim():              from 70, passo(+1) -> position 9, pct_de(9, trim70, jabra)
def test_step_never_raises_on_less():        from 5, passo(-1) -> stays 5
def test_button_recomputes_percent():        from 70, apertou(-1) -> position 7, same trim, sincronizar(7) called
def test_button_at_the_ends_keeps_percent(): 100 + (+1) -> 100; 0 + (-1) -> 0
def test_restore_after_alert_returns_exact_percent():  70 -> alert -> 70
def test_dragging_does_not_save_every_value():  definir(50, agora=0.0) .. definir(60, agora=0.5): nothing saved until ESPERA_GRAVACAO_S after the last
```
`definir` gains `agora=None`; `None` = save on the next tick (voice). The local-port path passes `time.monotonic()`. Delete the `TestAvisoDeMudanca` class.

- [ ] **Step 3: Config tests:** `volume_pct` limits; `pct_efetivo` for: both None; only `volume: 9` -> 82; `volume_pct: 70, volume: 8` -> 70; rollback case `volume_pct: 70, volume: 3` -> 27; a leftover `volume_webhook_url` in the file still loads. Command tests: `percentual: 70` answers `percentual == 70`; `numero: 7` -> 64; `numero: 10` -> 100.

- [ ] **Step 4: Run** the program suite. Expected: FAIL on the new tests.
- [ ] **Step 5: Implement.** In `friday_satelite.py`:
  - build `MixerFino(camada["mixer"], camada["fine_mixer"], camada["tabela"], agendar=loop.call_later)` (the object is created before the loop runs: use a small lambda that resolves the loop at call time, thread-safe like the old `_avisar_volume` did with `call_soon_threadsafe`); `fine_mixer.prime()` before the first write, failure logged, never fatal (same pattern as `music_mixer.prime`).
  - Factory (`volume` and `volume_pct` both None): after `volume_inicial.aplicar()`, initial = `grade(self._ler_mixer())` (Jabra 65% -> 64; XVF raw 75% -> 45, today's effective loudness).
  - `_gravar_volume(pct)` writes `volume_pct` and `volume = posicao_de_pct(pct)`.
  - `ao_mudar_config`: compare `volume_pct`; a PUT carrying only `volume` means `grade(volume)`; call `definir(pct, agora=time.monotonic())`; do not write the config file a second time with a stale value.
  - Emitted events and `/status` carry `volume` (position) and `volume_pct`.
  - Jabra countdown (`ring.make_ring(posicao_fn=...)`) keeps writing the board only (`camada["mixer"].escrever`); the restore at line ~980 calls `self.volume` mixer `reaplicar(self.volume.atual)`.
  - Delete `_avisar_volume`, `_enviar_volume`, `_volume_aviso`.
  - The porta-local PUT must save the config as before, but the volume keys it saves must be the consistent pair (check how `porta_local.py` writes the file; if it saves before `ao_mudar_config`, normalize the pair there).
- [ ] **Step 6: Run** the program suite. Expected: OK.
- [ ] **Step 7: Commit** `feat(satellite): fine volume on top of the board step`.

---

### Task 4: Agent reports and sets the exact percent

**Files:**
- Modify: `agents/satellite/friday_satellite_agent/capabilities.py`, `__main__.py`
- Test: `tests/agents/test_satellite_capabilities.py`

**Interfaces:**
- Consumes: config keys `volume_pct`, `volume`; event `mudancas` and `/status` carry them.
- Produces: entity state `volume` = `volume_pct`, else `percent_from_position(volume)`; same fallback on `/status`. `to_program(cfg, "volume", v)` -> `{"config": {"volume_pct": int(round(v))}}`. `position_from_percent` is removed.

- [ ] **Step 1: Tests:** state with `volume_pct: 70, volume: 8` reports 70; with `volume: 8` only reports 73; status fallback; `to_program(CFG, "volume", 70.0) == {"config": {"volume_pct": 70}}`; `Tracker` maps a config event with `volume_pct` to origin `ha` after `mark_ha({"volume_pct": 70})` and to `voice` for a voice change.
- [ ] **Step 2: Run** `bash scripts/test.sh tests/agents/test_satellite_capabilities.py`. Expected: FAIL.
- [ ] **Step 3: Implement.** Check the `Tracker` handles `volume` + `volume_pct` arriving together as one HA echo.
- [ ] **Step 4: Stale-read guard.** In `__main__.py`, one `asyncio.Lock` held by `refresh` from the program read to the end of `push_state`, so a poll that started before a `set` cannot publish after it. `do_set` must not hold it while calling `refresh` (no deadlock). Add a test with a fake program whose first `get_config` is slow and a second one fast: the last pushed `volume` is the new one. If `refresh` is not testable as a closure, move the lock + body into a small class in a new module `refresher.py`.
- [ ] **Step 5: Run** `bash scripts/test.sh tests/agents`. Expected: PASS. Also confirm by reading `Tracker.on_event` that voice and button `config` events call `refresh` at once (no 20 s wait); add the missing path only if absent.
- [ ] **Step 6: Commit** `feat(satellite-agent): exact volume percent, no stale refresh`.

---

### Task 5: ALSA `Fine` control, migration 0009 and image check

**Files:**
- Modify: `satellite/audio/jabra.conf`, `satellite/audio/xvf3800.conf`, `satellite/image/verify_image.sh`, `satellite/image/README.md` (only if it lists migrations)
- Create: `satellite/migrations/0009_fine_volume.sh`, `tests/agents/test_migration_0009.py`
- Test: `tests/agents/test_audio_confs.py`

**Interfaces:**
- Consumes: control name `Fine`, 0..120 = -6..0 dB (Task 3).
- Produces: both confs define `pcm.fine_vol`; `ota/release.json` needs no change (both confs are already in `package.satellite.system`; verify).

- [ ] **Step 1: Tests.** `COMMON` gains `pcm.fine_vol` and `name "Fine"`. For both confs the `pcm.fine_vol` block has `type softvol`, `slave.pcm "mixer_out"`, `min_dB -6.0`, `max_dB 0.0`, `resolution 121`, and the card of the board. Jabra: `pcm.voice` and `pcm.music_vol` have slave `fine_vol`. XVF: `pcm.mono_out` has slave `fine_vol` (voice and music already go through `mono_out`). Exactly one PCM has `slave.pcm "mixer_out"`: `fine_vol`. `JABRA_SHA256` pinned the pre-XVF file: replace that hash test (if present further down) by these structural assertions.
- [ ] **Step 2: Migration test**, modelled on `test_migration_0008.py`: installs both confs into `etc/friday-audio/` with mode 0644 from `$FRIDAY_RELEASE_DIR/system`; fails (non-zero, nothing touched) if one source is missing or the variable is unset; idempotent; with `FRIDAY_OTA_ROOT` set it never calls `systemctl`.
- [ ] **Step 3: Run** `bash scripts/test.sh tests/agents/test_audio_confs.py tests/agents/test_migration_0009.py`. Expected: FAIL.
- [ ] **Step 4: Implement.** Confs: add the block with a short comment; keep `Music`. Migration: same structure as 0008 (check all sources first, atomic `inst`). On a real device then `systemctl try-restart sendspin-cli friday-silence >/dev/null 2>&1 || true`. It must install the files itself because activation runs migrations before system files; say so in the header. `verify_image.sh`: `bad` unless each `/etc/friday-audio/*.conf` contains `pcm.fine_vol`. The image's migration list is recorded from the release folder automatically (`customize-chroot.sh`), nothing to add there.
- [ ] **Step 5: Run** the tests plus `cd satellite/image && python3 -m unittest -q tests.test_payload_check tests.test_make_manifest tests.test_vault_payload tests.test_build_download` and `bash ota/test_self_update.sh`. Expected: PASS.
- [ ] **Step 6: Commit** `feat(satellite): Fine ALSA control, migration 0009, image check`.

---

### Task 6: Bench proof of the ALSA chain (owner's OK first; both Pis are in use)

Needs the owner's OK: it stops the voice program for a few minutes. Do it before the alpha, one Pi at a time, with copies in `/tmp` only; nothing installed in `/etc`.

- [ ] **Step 1:** copy the new conf of the board to the Pi and test it with `ALSA_CONFIG_PATH=/usr/share/alsa/alsa.conf:/tmp/<conf>` style override (or the owner's OK to swap `/etc/asound.conf` temporarily, restored at the end). With `friday-satelite`, `sendspin-cli` and `friday-silence` stopped:
  `aplay -D voice x.wav` and `aplay -D music_vol` (48 kHz S16 stereo) both play; `amixer sget Fine` exists, starts at 120 (if it does not, `prime` must set it, fix Task 3); 120 vs 40 changes loudness on both paths by about 4 dB (measure with the board's microphone, `arecord`, RMS).
- [ ] **Step 2:** measure the delay between an `amixer` write of `Fine` and the audible change while a tone plays; compare with `atraso_s = 0.4`. Cross a step boundary both ways while recording: no 100 ms window louder than the louder end by more than 1 dB. Adjust `atraso_s` if needed (commit).
- [ ] **Step 3:** restore services; `systemctl is-active` all, original `asound.conf` link back. Record the numbers in the plan's PR description.

If the stacked softvol does not work (`music_vol` refuses the format or `Fine` has no effect), stop and report; do not improvise another chain.

---

### Task 7: Docs, full suite, alpha and proof

**Files:** `docs/satellite-settings.md` (volume row), `docs/release-notes/vX.Y.Z.md` (skill `writing-friday-release-notes`; number per `docs/releasing.md`, the owner decides), `custom_components/friday/manifest.json` (version).

- [ ] **Step 1:** `grep -rn 'volume_webhook_url\|position_from_percent\|posicao_de_percentual' --include='*' -I . | grep -v docs/superpowers` returns nothing unexpected (check `volume_inicial.py` and `voice_gateway/`; `volume_inicial` keeps its own conversion if it needs one, inline).
- [ ] **Step 2:** `bash scripts/test-all.sh`. Expected: `ALL GREEN`. Check every CI job, not the tail.
- [ ] **Step 3:** final code review of the branch; then ask the owner before push, PR (draft, with a "Merge gate" section = Step 4) and the alpha release. Pull `origin/master` before the PR.
- [ ] **Step 4: Alpha proof (merge gate = promotion to stable), both satellites:** updated, migration 0009 recorded, `Fine` exists, loudness at 73 sounds as before; MA slider changes sound at 70 → 72 → 75 and stays put; drag with music: no jump back, no blip; Jabra buttons and "volume 7" / "mais baixo" work and the MA slider follows; alarm rings and restores the exact percent; program restart and unplug/replug of the board with music: no loudness change; Sendspin `volume = 100`; 1 h of music with no new dropouts (compare `journalctl -u sendspin-cli`); MK2 wake word with music no worse than before. XVF raw values and ring unchanged at grid percents.
- [ ] **Step 5:** after promotion, remove `volume_webhook_url` from the Pi configs only if the owner wants (harmless leftover), and update memory and `friday-producao.md`.
