# Timer delivery, Echo style: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A timer only rings (ramp, beeps, one spoken notice after 3 loops) with no Gemini session at expiry; only the wake word or the button acts; "Friday, parar" stops ringing timers on every satellite.

**Architecture:** The maestro (satellite state machine) stops opening a conversation at expiry and schedules the spoken notice by time. The bot synthesizes the notice when a named timer is created and POSTs the WAV to the satellite, with a generic clip shipped in the program as the fallback. A consecutive-TTS-failure counter written by the bot is read by the server agent and shown as a HA problem sensor. A new bot tool stops ringing timers by fanning a command out to every satellite.

**Tech Stack:** Python (satellite program, aiohttp, unittest), voice gateway (Gemini TTS via `anuncios.falar`), server agent + HA integration (pytest via `scripts/test.sh`).

**Spec:** `docs/superpowers/specs/2026-11-24-timer-delivery-design.md`

## Global Constraints
- Worktree `/config/dev/ha-friday-wt-timer`, branch `feat/timer-delivery` (from master). Git messages in English, commits end with the Co-Authored-By and Claude-Session lines; never `--no-verify`; the sweep must pass. Comments in English in new code under `agents/`, `custom_components/`.
- Tests: satellite `cd satellite/program && python3 -m unittest -q <module>`; agents/integration `FRIDAY_TEST_DIR=ha-friday-timer bash scripts/test.sh <path>`; voice gateway like CI (job `voice-gateway`, no pipecat): on the VM `docker run --rm -e FRIDAY_LOCAL_CONFIG=/w/voice_gateway/local.example.env -v friday-pip-cache:/root/.cache/pip -v $HOME/<dir>:/w -w /w/voice_gateway python:3.14-slim sh -c 'pip install -q numpy loguru aiohttp fastapi; python -m unittest -q <module>'` (locally `test_anuncios` already shows 13 failures/6 errors on master from missing deps: compare against that baseline, never against zero). A new voice-gateway test that needs pipecat uses `skipUnless(find_spec("pipecat"))`.
- Alarm ring: color `0x8000FF` (purple) at factor `0.4`, pulse `SPEED_FAST = 3`; do-not-disturb: same color and factor, `SPEED_SLOW = 1`. Orange (`COR_SISTEMA`) stays system-only. Jabra unchanged.
- Spoken notice plays once, after `alarm_ramp_s` + **3 loops** of the alarm beep; cap `alarme_max_s = 300` and the Telegram "not answered" notice unchanged.
- TTS failure alert: **3 consecutive** failures (no success between, any time span); cleared by the next success; expires after **7 days** with no new TTS attempt. Timers and announcements share the streak. The push automation is house-side: never created here without the owner's OK.
- Paid model calls (generating the generic clip) only with the owner's OK. Never print secrets. Nothing from the house in the repo.
- Merge, tag and publish only after the owner approves. Do not touch PR #54's branch.

## Review Focus
- TV or any speech during delivery must not end or mute a timer → Task 2 `test_speech_during_alarm_does_not_stop_it`.
- Wake word during delivery with the conversation closed without a stop: the alarm must come back → Task 3 `test_alarm_resumes_when_the_conversation_ends_without_stopping`.
- Several timers expiring together: each plays its own notice once, in its turn; "parar" stops the current and the queued ones → Task 2 `test_queued_timers_each_get_one_notice`, Task 3 `test_stop_all_clears_queue`.
- Notice file missing, unreadable or never uploaded: the generic clip plays; with both missing, the beeps go on silently → Task 5.
- Satellite offline or slow when the bot fans out "parar": the others still stop and the answer lists who did not answer → Task 8.

---

### Task 1: purple ring for alarm and do-not-disturb

**Files:**
- Modify: `satellite/program/ring.py` (the `_XVF` table, near `COR_SISTEMA`)
- Test: `satellite/program/test_ring.py`

**Interfaces:**
- Produces: `COR_ROXO = 0x8000FF`, `COR_ROXO_FATOR = 0.4`, `COR_ALARME_DND = escalar(COR_ROXO, COR_ROXO_FATOR)`; `_XVF["alarm"] = (1, COR_ALARME_DND, SPEED_FAST)`, `_XVF["dnd"] = (1, COR_ALARME_DND, SPEED_SLOW)`.

- [ ] **Step 1: Failing test** `test_alarm_and_dnd_share_purple_at_40_percent`: both entries use `ring.COR_ALARME_DND`; `_XVF["alarm"][2] > _XVF["dnd"][2]`; neither equals `ring.COR_SISTEMA`. Update the existing assertions that expect orange `0xFF8000` for `alarm` and full `0x8000FF` for `dnd`.
- [ ] **Step 2: Run** `cd satellite/program && python3 -m unittest -q test_ring` — Expected: new test FAILS.
- [ ] **Step 3: Implement** the constants and table change; `COR_CONTAGEM` (factory-reset countdown) stays orange.
- [ ] **Step 4: Run** `python3 -m unittest -q test_ring test_maestro` — Expected: OK.
- [ ] **Step 5: Commit** `feat(satellite): purple alarm ring, shared 40% brightness with do-not-disturb`.

### Task 2: delivery without a conversation

**Files:**
- Modify: `satellite/program/maestro.py` (`Ajustes`, `_iniciar_alarme`, `_tique`, `_abrir_timer`, `_apos_conversa`, the `atendido` branch near line 537, `music_wait`)
- Modify: `satellite/program/friday_satelite.py` (set `Ajustes.volta_alarme_s` and `rampa_s` from the alarm WAV length and `cfg["alarm_ramp_s"]` at start and on config change)
- Test: `satellite/program/test_maestro.py`, `satellite/program/test_maestro_alertas.py`

**Interfaces:**
- Produces: `Ajustes.rampa_s: float = 0.0`, `Ajustes.voltas_antes_do_nome: int = 3`; `Ajustes.volta_alarme_s` now means the length of one alarm loop (default `1.65`). New maestro action `("aviso_timer", timer: dict)` (the timer dict has `id`, `nome`). New deadline kind `"nome_do_timer"`.
- Behavior: `_iniciar_alarme` no longer schedules `alarme_fala` or opens a conversation; it schedules `("nome_do_timer", agora + rampa_s + voltas_antes_do_nome * volta_alarme_s)`. When it fires in state `ALARME` it returns `[("aviso_timer", timer)]` once per alarm. Speech events (`fala_comecou`) in `ALARME` do nothing. `_abrir_timer` and the `atendido` flag are removed. A music alarm (`music`) keeps `request_music`; if the music does not start in `music_wait_s` it falls through to the same `nome_do_timer` deadline.

- [ ] **Step 1: Failing tests** in `test_maestro.py` (use the file's existing fake-clock helpers): `test_expiry_opens_no_conversation` (after `timer_venceu` actions contain `("alarme","tocar")` and `("luz","alarme")`, never `("conectar", …)`, estado is `alarme`); `test_notice_comes_after_ramp_plus_three_loops` (`rampa_s=3`, `volta_alarme_s=1.65`: nothing at 7.9 s, one `("aviso_timer", t)` at 8.0 s, none again later); `test_speech_during_alarm_does_not_stop_it` (`fala_comecou` events for 60 s: still `alarme`, no `registrar_timer`); `test_queued_timers_each_get_one_notice` (two timers expire together: the second rings only after the first ends, each emits one notice); `test_not_answered_after_300s_still_notifies` (existing behavior kept: `como_parou == "nao_atendido"` and `aviso_nao_atendido`). Delete or rewrite the old tests that expect `_abrir_timer`/`atendido`.
- [ ] **Step 2: Run** `python3 -m unittest -q test_maestro test_maestro_alertas` — Expected: new tests FAIL.
- [ ] **Step 3: Implement** the behavior above in `maestro.py`; wire `rampa_s` and `volta_alarme_s` in `friday_satelite.py` (read `wave` length of the alarm sound, as `_repetir_alarme` already does).
- [ ] **Step 4: Run** the same command — Expected: OK. Also `python3 -m unittest -q test_maestro_anuncio test_maestro_wake_word`.
- [ ] **Step 5: Commit** `feat(satellite): timers ring without opening a conversation`.

### Task 3: wake word during delivery and the stop command

**Files:**
- Modify: `satellite/program/maestro.py` (`ALARME` branch of the wake-word/button handling near line 530, `_apos_conversa`, `_abrir`, new `parar_alarmes`)
- Modify: `satellite/program/comandos.py` (new action `timers_parar`)
- Modify: `satellite/program/friday_satelite.py` (execute the command effect)
- Test: `satellite/program/test_maestro.py`, `satellite/program/test_comandos.py`

**Interfaces:**
- Consumes: Task 2 state (`_alarme`, `_fila_alarmes`).
- Produces: `Maestro.parar_alarmes(agora, nome=None) -> tuple[list[dict], list[tuple]]` (stopped timers `[{"id","nome"}]` and actions; `nome=None` stops the current alarm and every queued one; a name stops only matching timers). Command `{"acao": "timers_parar", "nome": str|None}` → response `{"parados": [{"id","nome"}], "tocando": bool}`. `_abrir(agora, como, whisper=False, wake_word=None, extras=None)` forwards `extras` into the `("conectar", extras)` action; for a wake word heard in `ALARME` the extras are `{"timers_tocando": [{"id","nome"}, …]}`. `publico()` gains `"alarmes": [{"id","nome"}, …]` (current + queued) next to the existing `"alarme"`.
- Behavior: wake word in `ALARME` → `[("alarme","pausar")]` + `_abrir(..., extras=…)`; the alarm record stays. Button in `ALARME` ends the alarm as today (`como_parou: "botao"`). When that conversation closes and the alarm record still exists, `_apos_conversa` returns `[("alarme","tocar"), ("luz","alarme")]` (and ends it as `nao_atendido` past 300 s). Stopping through the command ends it with `como_parou: "palavra"`.

- [ ] **Step 1: Failing tests:** `test_wake_word_pauses_alarm_and_opens_conversation_with_timers_in_extras`; `test_alarm_resumes_when_the_conversation_ends_without_stopping`; `test_stop_by_name_only_stops_that_timer`; `test_stop_all_clears_queue` (current + queued ended, `registrar_timer` for each, no alarm sound afterwards); `test_stop_with_nothing_ringing_returns_empty`; in `test_comandos.py` `test_timers_parar_returns_stopped_timers` and `test_timers_parar_rejects_non_string_name`.
- [ ] **Step 2: Run** `python3 -m unittest -q test_maestro test_comandos` — Expected: new tests FAIL.
- [ ] **Step 3: Implement** as specified; the command handler delegates to `Maestro.parar_alarmes` through the same effect mechanism other commands use.
- [ ] **Step 4: Run** the same command plus `test_satelite_ligacao` — Expected: OK.
- [ ] **Step 5: Commit** `feat(satellite): wake word and stop command act on a ringing timer`.

### Task 4: per-timer notice storage on the satellite

**Files:**
- Modify: `satellite/program/porta_local.py` (new route `POST /timer_voice`), `satellite/program/friday_satelite.py` (wiring, cleanup), `satellite/program/timers.py` (hook when a timer is cancelled or ends)
- Test: `satellite/program/test_porta_local.py` (create if the file name differs: next to the existing `/announce` route tests), `satellite/program/test_timers.py`

**Interfaces:**
- Produces: `POST /timer_voice` on port 8700, bearer auth like `/announce`, headers `X-Timer-Id` (must match `^t[0-9a-f]{6}$`), body a WAV (`RIFF`), max 2 MB; stored atomically as `<state dir>/timer_voz/<id>.wav`; replies `{"ok": true}`; 400 for bad id or non-WAV, 404 when the id is not an active timer, 413 over the limit. Helper `timer_voz_path(timer_id) -> str`. Cleanup: the file is deleted when its timer is cancelled or finishes ringing, and at boot every file whose id is not an active timer is deleted.

- [ ] **Step 1: Failing tests:** `test_timer_voice_stores_wav_for_active_timer`, `test_timer_voice_unknown_timer_404`, `test_timer_voice_rejects_non_wav_and_bad_id`, `test_timer_voice_needs_bearer`, `test_cancel_deletes_the_file`, `test_boot_removes_orphan_files`.
- [ ] **Step 2: Run** the test modules — Expected: FAIL.
- [ ] **Step 3: Implement** following the `/announce` handler (atomic `.tmp` + `os.replace`, size guard, auth).
- [ ] **Step 4: Run** — Expected: OK.
- [ ] **Step 5: Commit** `feat(satellite): accept a per-timer spoken notice`.

### Task 5: notice playback and the generic clip

**Files:**
- Create: `satellite/program/sons/timer_generico.wav` (committed binary, Friday voice, 48 kHz stereo S16, about 2 s), `satellite/voice_lines/gerar_timer_generico.py` (generator, same style as `gerar_frases_onboarding.py`)
- Modify: `satellite/program/friday_satelite.py` (handle `("aviso_timer", timer)`)
- Test: `satellite/program/test_satelite_ligacao.py` (or the module that already tests maestro-action execution), `satellite/voice_lines/test_gerar_falas_voz.py`

**Interfaces:**
- Consumes: Task 2 action `("aviso_timer", timer)`, Task 4 `timer_voz_path`.
- Produces: `_aviso_do_timer(timer) -> str | None`: the per-timer file if it exists and is a readable WAV, else the generic clip, else `None`. The generic clip is resolved from the release program dir (`os.path.join(os.path.dirname(__file__), "sons", "timer_generico.wav")`), so the image (`cp -a $R/program/sons/.`) and OTA releases both carry it with no migration. The notice is queued with `self.alto.tocar_wav(path)`; `None` plays nothing and logs `{"timer_aviso": "sem_arquivo", "id": …}`.

- [ ] **Step 1: Failing tests:** `test_notice_plays_the_timer_file_when_present`, `test_notice_falls_back_to_generic_when_file_missing`, `test_notice_falls_back_to_generic_when_file_is_not_a_wav`, `test_notice_silent_when_both_missing`, `test_unnamed_timer_uses_generic_clip`; in the generator tests, one asserting the script refuses to run without an explicit `--confirm-paid` flag.
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement** `_aviso_do_timer` and the action; write the generator (Gemini TTS through the same `voice_settings.tts` configuration, text "Seu timer terminou.").
- [ ] **Step 4: Ask the owner for the OK to spend one TTS call, then run the generator once** and commit the WAV. Check `file` reports 48000 Hz stereo 16-bit and duration under 4 s. Run the satellite tests — Expected: OK.
- [ ] **Step 5: Commit** `feat(satellite): spoken timer notice with a generic fallback clip`.

### Task 6: TTS health counter (voice gateway)

**Files:**
- Create: `voice_gateway/tts_saude.py`, `voice_gateway/test_tts_saude.py`
- Modify: `voice_gateway/anuncios.py` (`falar` callers record success or failure), `voice_gateway/bot.py` (state file path from env)

**Interfaces:**
- Produces: `registrar(ok: bool, origem: str, erro: str | None = None, agora: float | None = None) -> None` and `estado(agora: float | None = None) -> dict` returning `{"falhando": bool, "seguidas": int, "desde": float | None, "ultimo_erro": str | None, "origem": str | None}`. State file `FRIDAY_TTS_HEALTH` (default `/srv/friday/state/tts-health.json`, atomic write). Constants `LIMITE_SEGUIDAS = 3`, `EXPIRA_S = 7 * 86400`. `origem` is `"timer"` or `"anuncio"`.

- [ ] **Step 1: Failing tests:** `test_three_consecutive_failures_turn_on` (spaced hours apart); `test_success_clears_the_streak`; `test_two_failures_then_success_then_two_failures_is_off`; `test_expires_after_seven_days_without_attempts`; `test_announce_and_timer_failures_share_the_streak`; `test_corrupt_state_file_reads_as_healthy`.
- [ ] **Step 2: Run** `test_tts_saude` with the voice-gateway recipe — Expected: FAIL.
- [ ] **Step 3: Implement** the module; call `registrar` around the `an.falar` calls in the announcement path (success and exception) without changing their behavior.
- [ ] **Step 4: Run** `test_tts_saude test_anuncios` — Expected: new tests OK; `test_anuncios` no worse than the baseline.
- [ ] **Step 5: Commit** `feat(voice-gateway): count consecutive TTS failures`.

### Task 7: synthesize the notice when a named timer is created

**Files:**
- Modify: `voice_gateway/satelite_modo.py` (timer tool result path), `voice_gateway/bot.py` (background task + sender), `voice_gateway/anuncios.py` only to reuse `falar`, `wav_48k_estereo`, `Cache`, `chave`
- Test: `voice_gateway/test_satelite_modo.py`

**Interfaces:**
- Consumes: Task 6 `registrar`.
- Produces: `TEXTO_AVISO_TIMER = "O timer {nome} terminou."` and `async def avisar_voz_do_timer(deps, sid, timer_id, nome) -> str` returning `"ok"`, `"sem_nome"`, `"tts_falhou"` or `"envio_falhou"`; `deps.enviar_voz_timer(ip, senha, timer_id, wav)` posting to `http://<ip>:8700/timer_voice` with `Authorization: Bearer` and `X-Timer-Id`. It runs as a background task after a successful `timer_criar` that returned a timer with a `nome`; it never delays or changes the tool answer.

- [ ] **Step 1: Failing tests (fake deps):** `test_named_timer_sends_the_wav_to_the_satellite`; `test_unnamed_timer_sends_nothing`; `test_tts_failure_is_logged_counted_and_does_not_break_creation`; `test_cached_text_does_not_call_tts_again`; `test_send_failure_is_returned_not_raised`.
- [ ] **Step 2: Run** with the voice-gateway recipe — Expected: FAIL.
- [ ] **Step 3: Implement**; log one line per attempt (`timer_voz: id, resultado, ms, cache`).
- [ ] **Step 4: Run** `test_satelite_modo test_anuncios test_tts_saude` — Expected: no worse than baseline, new tests OK.
- [ ] **Step 5: Commit** `feat(voice-gateway): synthesize a named timer's notice at creation`.

### Task 8: stop tool and wake-word context (voice gateway)

**Files:**
- Modify: `voice_gateway/satelite_modo.py` (tool definition, args, opening message), `voice_gateway/bot.py` (registration, fan-out, session start)
- Test: `voice_gateway/test_satelite_modo.py`

**Interfaces:**
- Consumes: Task 3 command `timers_parar`, `publico()["alarmes"]`, Task 3 connection extra `timers_tocando`.
- Produces: tool `parar_timers` (args `nome` optional string, `comodo` optional string; validated by `args_parar_timers(a) -> dict`); `async def parar_em_todos(deps, nome=None, comodo=None) -> dict` sending `{"acao":"timers_parar","nome":nome}` to every satellite in the registry (filtered by room when `comodo` is given), in parallel with a 3 s timeout each, returning `{"parados": [{"timer","comodo"}], "sem_resposta": [comodo, …]}`. Opening text for a session opened by the wake word during an alarm states facts only: which timers are ringing (name, room) and that nothing has been stopped yet. The tool description states that no name and no room means all ringing timers on all satellites.

- [ ] **Step 1: Failing tests:** `test_stop_without_args_sends_to_every_satellite`; `test_stop_by_room_only_hits_that_room`; `test_stop_by_name_passes_the_name`; `test_one_satellite_timing_out_is_listed_not_fatal`; `test_opening_lists_ringing_timers_and_says_none_stopped_yet`; `test_tool_args_reject_non_string_values`. Grep the whole prompt/descriptions for sentences that contradict "stop" (the old "cancelar_timer" text stays for timers that are not ringing yet).
- [ ] **Step 2: Run** with the voice-gateway recipe — Expected: FAIL.
- [ ] **Step 3: Implement**, reusing the announcement fan-out helpers for the satellite list and credentials; the answer is returned to the model so it can say which timer ended and a closing phrase in the same turn.
- [ ] **Step 4: Run** `test_satelite_modo test_canal_casa` — Expected: no worse than baseline, new tests OK.
- [ ] **Step 5: Commit** `feat(voice-gateway): stop ringing timers on every satellite`.

### Task 9: TTS problem sensor in HA

**Files:**
- Modify: `agents/server/friday_server_agent/collectors.py` (read the state file, add to `status_metrics` and `capabilities`), `agents/server/friday_server_agent/__main__.py` (env path, same pattern as `ACTIVITY_FILE`)
- Test: `tests/agents/test_server_collectors.py`

**Interfaces:**
- Consumes: Task 6 state file format.
- Produces: capability `tts_failing` (binary_sensor, `device_class: problem`, name "TTS failing", diagnostic) with attributes `consecutive`, `since`, `last_error`, `origin`; value `None` when the file is unreadable, `False` when healthy or expired (7 days), `True` at 3 consecutive failures. The integration needs no code: descriptors come from `capabilities()`.

- [ ] **Step 1: Failing tests:** `test_tts_failing_on_at_three_in_a_row`, `test_tts_failing_off_when_expired`, `test_tts_failing_unknown_when_file_unreadable`, and the capabilities list contains `tts_failing`.
- [ ] **Step 2: Run** `FRIDAY_TEST_DIR=ha-friday-timer bash scripts/test.sh tests/agents/test_server_collectors.py` — Expected: FAIL.
- [ ] **Step 3: Implement**, mirroring `brain_memory_watcher_degraded`.
- [ ] **Step 4: Run** the same command plus `tests/agents/test_satellite_capabilities.py` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(server-agent): TTS failing problem sensor`.

### Task 10: full check, owner's checklist and the ideas list

**Files:**
- Modify: `docs/superpowers/specs/2026-11-24-timer-delivery-design.md` (only if a call changed during the build)

- [ ] **Step 1: Run** `FRIDAY_TEST_DIR=ha-friday-timer bash scripts/test-all.sh` and the satellite and voice-gateway commands from Global Constraints — Expected: every step passes (a lone `test_provisioning` timeout is the known flake: rerun that step).
- [ ] **Step 2: Rebase** on a freshly fetched `origin/master`, push the branch, open the PR as **ready** if CI is green (check every job through JSON: `gh pr checks N --json name,bucket,link -q '.[]|select(.bucket!="pass" and .bucket!="skipping")'` must print nothing, and `mergeStateStatus` must not be BLOCKED). The PR body lists the alpha checks from the spec's "To confirm on the alpha".
- [ ] **Step 3: Stop and wait** for the owner: house-side steps (the HA push automation for `binary_sensor.friday_server_tts_failing`) and the live test on MK2 with the TV on are his; merge, tag and release only on his OK.
- [ ] **Step 4: After the owner confirms the alpha works**, mark the implemented ideas as done so they are not built again, in the HA to-do list `todo.ideias_de_automacao`, only these two items: "feedback luminoso para timers" and "feedback por voz ao encerrar o timer":
  ```bash
  for item in "feedback luminoso para timers" "feedback por voz ao encerrar o timer"; do
    curl -s -X POST -H "Authorization: Bearer $SUPERVISOR_TOKEN" -H "Content-Type: application/json" \
      -d "{\"entity_id\":\"todo.ideias_de_automacao\",\"item\":\"$item\",\"status\":\"completed\"}" \
      http://supervisor/core/api/services/todo/update_item
  done
  ```
  Then read the list back (`todo/get_items`) — Expected: those two are `completed`, the other eight stay `needs_action`.
- [ ] **Step 5: Add the release notes entry** for this work through the `writing-friday-release-notes` skill when the version is cut (not before).
