# Only the Word Interrupts + Beam Lock Reaches Gemini Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task
> (owner, 21/11: one Sonnet executor, no per-task review; one final review at the end). Steps use checkbox
> (`- [ ]`) syntax for tracking.

**Goal:** On satellites, while she answers no audio goes to Gemini (only "Friday" cuts her), the mic reopens as soon
as the answer is complete, and the beam lock covers the first exchange on both XVF channels.

**Architecture:** The satellite's `Maestro` (pure state machine) already closes the mic for "recados"; a normal answer
becomes a recado when the new setting is on. The bot forwards Gemini's `turn_complete` to the satellite as a
server message, so the maestro can reopen at her stop instead of waiting 1.5 s. The maestro emits an explicit "answer
ended" action that the program uses to release the beam lock (`doa.Regra`), and the lock also routes channel 0.

**Tech Stack:** Python 3.13, unittest (satellite program, voice gateway), pytest (integration, agents).

**Spec:** `docs/superpowers/specs/2026-11-21-only-the-word-interrupts-design.md`

## Global Constraints
- Setting key everywhere: `only_wake_word_interrupts` (bool). Program and integration default **True**. Label
  (integration forms, descriptor name, en and pt-BR translation files): `Only the wake word interrupts` (the pt-BR
  file keeps English labels for these keys, as `no_follow_up_after_home_commands` does).
- `Ajustes.only_wake_word_interrupts` dataclass default is **False** (existing maestro tests keep today's behavior);
  the program passes the config value (default True).
- Bot → satellite message: RTVI server message `{"evento": "resposta_completa"}`; satellite event name
  `resposta_completa`.
- New maestro actions: `("guardar_pausa", True)`, `("guardar_pausa", False)`, `("fim_da_resposta", None)`.
- Beam lock registers: lock = `AEC_FIXEDBEAMSAZIMUTH_VALUES [θ, θ]` → `AEC_FIXEDBEAMSONOFF [1]` →
  `AUDIO_MGR_OP_R [7, 0]` → `AUDIO_MGR_OP_L [6, 0]` (then read back `AUDIO_MGR_OP_L`); release =
  `AEC_FIXEDBEAMSONOFF [0]` → `AUDIO_MGR_OP_R [7, 3]` → `AUDIO_MGR_OP_L [8, 0]`. Never `SAVE_CONFIGURATION`.
- `RECADO_PAUSA_S` stays 1.5. The pause buffer holds at most `RECADO_PAUSA_S` of audio.
- Web page client (`/client/`, not a satellite) unchanged. No change to the voice's texts (`SYSTEM_INSTRUCTION`).
- Version stays 0.4.2 (not tagged yet). Code comments and git messages in English; never `--no-verify`.
- Tests: set `TMPDIR=/root/.claude/jobs/47cf0b42/tmp/wordonly-tmp` (create it) for every local test run; pytest
  runs through `scripts/test.sh` (VM). Never write test files under `/tmp`.
- Work only in the worktree `/config/dev/ha-friday-wt-wordonly` (branch `feat/only-word-interrupts`). Do not push.

## Review Focus
1. Gemini's `turn_complete` arriving **before** `ela_comecou` (short answer generated faster than playback starts):
   the flag must survive `ela_comecou`, so the mic still reopens at her stop. (Task 1 test.)
2. A pause inside a long answer **without** completion (bot-stopped then bot-started within 1.5 s): mic stays closed,
   the pause buffer is discarded, no audio of the pause reaches Gemini. (Task 1 and Task 3 tests.)
3. "Friday" said during the 1.5 s pause after her answer: the word cuts as in a recado, the lock releases
   (`fim_da_resposta`) and the buffered audio (which holds the word and the request start) is sent. (Task 1 test.)
4. Setting off: everything as today, including the mic open while she speaks and no `guardar_pausa` actions.
   (Task 1 test.)
5. A brain answer (recado from `aguardando`) with `resposta_completa`: the mic reopens at her stop too, light goes
   back to `ouvindo`. (Task 1 test.)

---

### Task 1: Maestro — answers as recados, completion shortcut, answer-ended action

**Files:**
- Modify: `satellite/program/maestro.py` (`Ajustes`, `_zerar`, events `ela_comecou`, `ela_parou`, `palavra`, new
  `resposta_completa`, the `_recado_fim` reopen in `evento`/`_tique` path, the PARADA ignore list)
- Test: `satellite/program/test_maestro.py` (new class `TestSoAPalavraInterrompe`)

**Interfaces:**
- Produces: `Ajustes.only_wake_word_interrupts: bool = False`; event `"resposta_completa"`; actions
  `("guardar_pausa", True|False)` and `("fim_da_resposta", None)` (consumed by Task 3).

Behavior (with the setting on; recado = `self._recado`):
- `ela_comecou` in `ouvindo`, not already a recado → becomes a recado: `("microfone", False)`, `("luz",
  "respondendo")` (not `falando`: the 18/11 Echo-like light stays for normal answers; brain/delivery/presentation
  recados keep `falando`).
- `resposta_completa` in `conectando`/`ouvindo` → remember it (`self._completa = True`); if the 1.5 s pause is already
  running (`_recado_fim` set), reopen now. Ignored in `parada` (add to the existing ignore list).
- `ela_parou` in a recado: if completion is known → reopen now; else start the pause (`_recado_fim = agora +
  RECADO_PAUSA_S`) and return `[("guardar_pausa", True)]`. This applies to every recado (spec goal 2).
- Reopen (now, or when `_recado_fim` expires) = today's recado end (`("microfone", True)`, `("luz", "ouvindo")` +
  `_trata("ela_parou", parou, None)`) plus `("fim_da_resposta", None)`; clears `_completa`.
- `ela_comecou` while the pause runs → recado continues and `("guardar_pausa", False)` is returned.
- `palavra` during a recado (existing cut path) → also `("fim_da_resposta", None)` and clears `_completa`.
- Setting off or not a recado: `ela_parou` in `ouvindo` (today's continuation path) also returns
  `("fim_da_resposta", None)`. `_completa` is never set to affect anything when the setting is off and no recado.
- `_completa` survives `ela_comecou` (Review Focus 1). It is cleared on reopen (both paths, including today's
  continuation path), on the word cut, in `_zerar`, and on `fala_comecou` outside a recado (the person starts a new
  utterance, so a `turn_complete` left over from a turn she never spoke, e.g. a `<no speech>` drop, cannot reopen
  the mic in the middle of the next answer).

- [ ] **Step 1: Write the failing tests** in a new `class TestSoAPalavraInterrompe(unittest.TestCase)` with
  `self.m = Maestro(Ajustes(only_wake_word_interrupts=True))` and a helper that opens and connects
  (`palavra` at 0, `conectou` at 1, `fala_comecou` 2, `fala_parou` 3). Use `tipos()` from the file, and add
  `"fim_da_resposta"` to the names `tipos()` filters out (next to `registrar`), so the existing tests that pin exact
  action lists stay valid; the new tests check it on the raw list (`("fim_da_resposta", None) in acoes`). Below,
  "contains `"fim_da_resposta"`" means that raw check. Assertions:
  - `test_resposta_fecha_o_microfone`: `ela_comecou` at 4 → `tipos` contains `"microfone:False"` and
    `"luz:respondendo"`, not `"luz:falando"`.
  - `test_fala_da_sala_durante_a_resposta_nao_conta`: after `ela_comecou`, `fala_comecou` at 5 → `[]`.
  - `test_completa_reabre_na_hora`: `ela_comecou` 4, `resposta_completa` 5, `ela_parou` 6 → `tipos` contains
    `"microfone:True"`, `"luz:ouvindo"`, `"fim_da_resposta"`; `self.m.estado == "ouvindo"`.
  - `test_completa_antes_de_ela_comecar` (Review Focus 1): `resposta_completa` 4, `ela_comecou` 4.1, `ela_parou` 6
    → contains `"microfone:True"` and `"fim_da_resposta"`.
  - `test_sem_completa_espera_a_pausa`: `ela_comecou` 4, `ela_parou` 6 → exactly `["guardar_pausa:True"]`;
    `tique` at 7.0 → no `"microfone:True"`; `tique` at 7.6 → contains `"microfone:True"`, `"fim_da_resposta"`.
  - `test_pausa_dentro_da_resposta` (Review Focus 2): `ela_comecou` 4, `ela_parou` 6, `ela_comecou` 6.5 →
    contains `"guardar_pausa:False"` and not `"microfone:True"`; `tique` at 8.5 → no `"microfone:True"`.
  - `test_completa_durante_a_pausa_reabre`: `ela_comecou` 4, `ela_parou` 6, `resposta_completa` 6.3 → contains
    `"microfone:True"`, `"fim_da_resposta"`.
  - `test_palavra_corta_a_resposta` (Review Focus 3): `ela_comecou` 4, `palavra` 5 → contains `"parar_fala"`,
    `"microfone:True"`, `"fim_da_resposta"`; same with `palavra` at 6.5 after `ela_parou` 6.
  - `test_desligado_como_hoje` (Review Focus 4): `Maestro(Ajustes())`, same opening, `ela_comecou` 4 →
    `tipos == ["luz:respondendo"]`; `ela_parou` 6 → contains `"luz:ouvindo"` and `"fim_da_resposta"`, no
    `"guardar_pausa:True"`.
  - `test_resposta_do_cerebro_com_completa` (Review Focus 5): reuse the steps of `TestCerebro.aguardando()` (in
    this class, with the setting on), then `cerebro_entregando` 40.0, `ela_comecou` 40.5, `resposta_completa` 44.0,
    `ela_parou` 45.0 → contains `"microfone:True"`, `"luz:ouvindo"`, `"fim_da_resposta"`.
  - `test_completa_parada_ignorado`: fresh maestro, `resposta_completa` at 0 → `[]`.
  - `test_completa_velha_nao_reabre_no_meio`: `resposta_completa` 3.5 (no answer follows), `fala_comecou` 8,
    `fala_parou` 9, `ela_comecou` 10, `ela_parou` 11 → exactly `["guardar_pausa:True"]` (the old flag was cleared).
- [ ] **Step 2:** `cd satellite/program && python3 -m unittest -q test_maestro.TestSoAPalavraInterrompe`.
  Expected: FAIL/ERROR (unknown field `only_wake_word_interrupts`).
- [ ] **Step 3:** Implement the behavior above in `maestro.py`.
- [ ] **Step 4:** `cd satellite/program && python3 -m unittest -q test_maestro test_maestro_alertas
  test_maestro_cadastro test_maestro_rede test_maestro_sussurro test_maestro_wake_word`. Expected: OK (existing
  tests run with the dataclass default False). The only allowed edit to an existing test: one that compares a raw
  action list (not `tipos()`) and now also gets `("fim_da_resposta", None)` gets it added to its expected list;
  list each in the report.
- [ ] **Step 5:** Commit `feat(satellite): only the wake word interrupts her answers`.

### Task 2: Beam lock — channel 0 on the locked beam, release when the first answer ends

**Files:**
- Modify: `satellite/program/doa.py` (`TravaFeixe.travar`, `TravaFeixe.soltar`, `Regra`, module docstring)
- Test: `satellite/program/test_doa.py`

**Interfaces:**
- Produces: `Regra.resposta_acabou() -> None` (releases if locked); `Regra.fala_comecou`/`fala_parou` are removed.
  `Regra.palavra`, `Regra.conversa_fechou`, `TravaFeixe.tique` unchanged. Consumed by Task 3.

- [ ] **Step 1: Tests** (update the existing ones and add):
  - `test_lock_sequence`: writes are `["AEC_FIXEDBEAMSAZIMUTH_VALUES", "AEC_FIXEDBEAMSONOFF", "AUDIO_MGR_OP_R",
    "AUDIO_MGR_OP_L"]`, the last with `[6, 0]`; the fake `Link` reads `AUDIO_MGR_OP_L` back as `[6, 0]` (extend the
    test's `Link` with a `read(name)` returning a configurable value).
  - `test_channel0_not_read_back_falls_back`: `read` returns `[8, 0]` (or `None`) → `travar` still returns True,
    `travado` True, a final write `("AUDIO_MGR_OP_L", [8, 0])` and one log line containing `"canal 0"`.
  - `test_unlock_sequence`: `[("AEC_FIXEDBEAMSONOFF", [0]), ("AUDIO_MGR_OP_R", [7, 3]), ("AUDIO_MGR_OP_L", [8, 0])]`.
  - `test_failed_lock_unlocks`: last three writes are the full release sequence above.
  - Replace `test_releases_when_first_request_ends` with `test_releases_when_first_answer_ends`: after `palavra`,
    still locked; `resposta_acabou()` → unlocked. And `test_answer_end_without_lock_writes_nothing`.
- [ ] **Step 2:** `cd satellite/program && python3 -m unittest -q test_doa`. Expected: FAIL.
- [ ] **Step 3:** Implement. A failed write of `OP_L 6 0` or a read-back other than `[6, 0]` writes `OP_L [8, 0]`,
  logs `"trava de feixe: canal 0 ficou no 8 0"` and keeps the lock (channel 1 is locked). The docstring states the
  new rule (lock through the first exchange; both channels).
- [ ] **Step 4:** Same command. Expected: OK.
- [ ] **Step 5:** Commit `feat(satellite): the beam lock covers the first exchange on both channels`.

### Task 3: Program wiring — event, actions, pause buffer, lock release

**Files:**
- Modify: `satellite/program/conexao.py` (`traduzir_rtvi`; `ConexaoFriday.segurar_audio`, `soltar_audio`, `enviar`)
- Modify: `satellite/program/friday_satelite.py` (`_ajustes`, `_agir`, `_despachar`/`_doa_evento`, capture loop)
- Test: `satellite/program/test_conexao.py`

**Interfaces:**
- Consumes: Task 1 actions/event, Task 2 `Regra.resposta_acabou()`.
- Produces: `traduzir_rtvi({"type": "server-message", "data": {"evento": "resposta_completa"}}) ==
  ("resposta_completa", None)`; `ConexaoFriday.segurar_audio() -> None`, `ConexaoFriday.soltar_audio(enviar: bool)
  -> None`.

Wiring:
- `_ajustes`: `only_wake_word_interrupts=self.cfg.get("only_wake_word_interrupts", True) is not False`.
- New attribute `self.guardando_pausa = False` (init next to `microfone_aberto`). **Do not reuse
  `microfone_aberto` for the pause:** with it True the capture loop skips the wake word inference (06/10, CPU), and
  "Friday" must be heard during the pause (Review Focus 3). The capture loop sends to the connection when
  `self.microfone_aberto or self.guardando_pausa`; the wake word skip stays on `microfone_aberto` only.
- `_agir("guardar_pausa", True)`: `conexao.segurar_audio()`, `guardando_pausa = True`. `("guardar_pausa", False)`:
  `conexao.soltar_audio(False)`, `guardando_pausa = False`. `("microfone", True)`: `conexao.soltar_audio(True)`
  before opening (flushes the buffer, oldest first) and `guardando_pausa = False`. Every place that sets
  `microfone_aberto = False` for a closing conversation also sets `guardando_pausa = False`. Guard every connection
  call with `if self.conexao`.
- `ConexaoFriday`: while held, `enviar(pcm)` appends to a deque instead of the mic queue; the deque keeps at most
  `RECADO_PAUSA_S` of audio (16 kHz S16: 32 000 bytes/s), dropping the oldest. `soltar_audio(True)` sends the deque
  through the normal path, `soltar_audio(False)` drops it; both end holding. Import `RECADO_PAUSA_S` from `maestro`.
- `_agir("fim_da_resposta")`: `self._doa_evento("fim_da_resposta")`; `_doa_evento` calls
  `self.doa_regra.resposta_acabou()` for it, keeps `"parada"` → `conversa_fechou()`, and no longer handles
  `fala_comecou`/`fala_parou`. Remove the `_despachar` call that sent those two events to the DOA.
- Capture loop: the echo meter (`self.medidor_eco.alimentar`) runs whenever she is speaking and a connection
  exists, even with the mic closed (spec "Consequences accepted"); sending stays under `microfone_aberto`.

- [ ] **Step 1: Tests** in `test_conexao.py`:
  - `test_resposta_completa`: the `traduzir_rtvi` assertion in Interfaces.
  - `test_segurar_audio_guarda_e_solta`: build `ConexaoFriday` the way existing tests in the file do (or with
    `ConexaoFriday.__new__` + the attributes `enviar` needs); hold, `enviar` 3 × 2560 bytes → mic queue empty;
    `soltar_audio(True)` → the queue holds 3 × 4 frames of 640 bytes, in order.
  - `test_segurar_audio_descarta`: hold, `enviar`, `soltar_audio(False)` → queue empty; a later `enviar` goes
    straight to the queue.
  - `test_segurar_audio_limite`: hold and `enviar` 2 s of audio → after `soltar_audio(True)` the queue holds
    exactly 1.5 s (48 000 bytes) and it is the last 1.5 s.
- [ ] **Step 2:** `cd satellite/program && python3 -m unittest -q test_conexao`. Expected: FAIL.
- [ ] **Step 3:** Implement the wiring above.
- [ ] **Step 4:** `cd satellite/program && python3 -m unittest -q` (whole program suite). Expected: OK. If a test
  outside this plan fails because answers now close the mic (program default True), set
  `only_wake_word_interrupts: False` only in that test's config when the test is about something else, and list
  each such test in the report. Never weaken an assertion of Tasks 1–3.
- [ ] **Step 5:** Commit `feat(satellite): reopen at her stop and release the lock when the first answer ends`.

### Task 4: Bot — forward Gemini's turn_complete to the satellite

**Files:**
- Modify: `voice_gateway/speech_cut.py` (new pure function), `voice_gateway/bot.py` (`_mensagem_vigiada`)
- Test: `voice_gateway/test_speech_cut.py`

**Interfaces:**
- Produces: `speech_cut.turn_complete(msg) -> bool` (True when `msg.server_content.turn_complete` is truthy; False
  for a message without `server_content`). The bot sends `{"evento": "resposta_completa"}` through the existing
  `avisar_pi` (satellites only) after the message passes `corte.filter` and the original handler.

- [ ] **Step 1: Tests:** `test_turn_complete_true`, `test_turn_complete_false_without_flag`,
  `test_turn_complete_false_without_server_content` (use the fake message style already in `test_speech_cut.py`).
- [ ] **Step 2:** `cd voice_gateway && python3 -m unittest -q test_speech_cut`. Expected: FAIL.
- [ ] **Step 3:** Implement; in `bot.py`, `_mensagem_vigiada` calls `await avisar_pi({"evento":
  "resposta_completa"})` when `speech_cut.turn_complete(message)`, wrapped so a failure only logs.
- [ ] **Step 4:** Same command. Expected: OK. Then `python3 -m py_compile voice_gateway/bot.py`. Expected: no output.
- [ ] **Step 5:** Commit `feat(voice): tell the satellite when Gemini completed the answer`.

### Task 5: The setting, from the integration to the program

**Files:** every file where `no_follow_up_after_home_commands` appears, outside `tests/contract/` and `.claude/`:
`custom_components/friday/{settings.py,config_flow.py,strings.json,translations/en.json,translations/pt-BR.json}`,
`agents/satellite/friday_satellite_agent/capabilities.py`, `satellite/program/configuracao.py`, and their tests
`tests/integration/test_options_flow.py`, `tests/integration/test_settings.py`,
`tests/agents/test_satellite_capabilities.py`, `satellite/program/test_configuracao.py`. Not `__init__.py`
(`RETIRED_KEYS` is for keys that had entities; this one never had).

**Interfaces:** Consumes the key name and defaults from Global Constraints. Produces the program config key read by
Task 3.

- [ ] **Step 1: Tests:** next to each existing `no_follow_up_after_home_commands` assertion, the same assertion for
  `only_wake_word_interrupts` with default `True`: general spec `{"type": "bool"}` and `validate_general(...,
  "yes") == "bad_type"`; `CONVERSATION_DEFAULTS` holds `True`; the hidden switch descriptor named
  `"Only the wake word interrupts"`; `state()` True by default and False with the config False;
  `to_program(CFG, "only_wake_word_interrupts", False) == {"config": {"only_wake_word_interrupts": False}}`;
  program `PADROES["only_wake_word_interrupts"] is True` and a non-bool value refused like the neighbor key.
- [ ] **Step 2:** `cd satellite/program && python3 -m unittest -q test_configuracao` and `scripts/test.sh
  tests/integration/test_settings.py tests/integration/test_options_flow.py tests/agents/test_satellite_capabilities.py`.
  Expected: FAIL.
- [ ] **Step 3:** Add the key alongside `no_follow_up_after_home_commands` in every file listed (descriptor:
  `switch`, `hidden=True`, icon `mdi:account-voice-off`).
- [ ] **Step 4:** Same commands. Expected: OK.
- [ ] **Step 5:** Commit `feat: "Only the wake word interrupts" setting for the satellites`.

### Task 6: Release notes and full suite

**Files:** `docs/release-notes/v0.4.2.md`.

- [ ] **Step 1:** Load the `writing-friday-release-notes` skill and update `v0.4.2.md`: the intro no longer says
  nothing changes on the satellites; add the behavior change (only "Friday" interrupts her answers, on by default,
  switch in the integration's general settings), the mic reopening at her stop, the beam lock on channel 0 through
  the first exchange, the `userSpeech` field of PR #44; Changes by area for satellite program, bot, integration and
  satellite agent; Promotion gate checks from the spec's "To confirm on the alpha"; All changes gets a line for #44
  and one for this branch without a number (the controller adds it).
- [ ] **Step 2:** `TMPDIR=… scripts/test-all.sh > /root/.claude/jobs/47cf0b42/tmp/wordonly-testall.log 2>&1`, read
  the tail. Expected: every step OK. The known flake (`tests/integration/test_provisioning.py` timing out) is
  re-run alone with `scripts/test.sh tests/integration/test_provisioning.py`; report both results.
- [ ] **Step 3:** Commit `docs(release): v0.4.2 notes cover the answer guard and the beam lock`. Do not push.
