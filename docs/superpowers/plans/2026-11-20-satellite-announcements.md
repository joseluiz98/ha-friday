# Announcements on the satellites Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `friday.announce` (Home Assistant service) makes every chosen satellite play the announcement chime and then the fixed text in the F.R.I.D.A.Y. voice, without opening a conversation.

**Architecture:** Integration service → server agent command `announce` → bot `POST /announce` on `127.0.0.1:7860` (Gemini TTS, cache, fan-out with retry) → satellite local port `POST /announce` (WAV body) → `Maestro` queues/plays under its own rules (DND, open conversation, boot).

**Tech Stack:** Python 3; Home Assistant custom integration (`custom_components/friday`), agents (`agents/server`), bot (FastAPI/pipecat, `voice_gateway`), satellite program (aiohttp, `satellite/program`, `unittest`), `google-genai`.

**Spec:** `docs/superpowers/specs/2026-11-20-satellite-announcements-design.md`

## Global Constraints

- Git in English; never `--no-verify`; nothing from the house in the repo (the commit hook runs `scripts/sweep.py`; say "the owner", never a name).
- Satellite tests run with a private TMPDIR: `cd satellite/program && TMPDIR=$(mktemp -d) python3 -m unittest -q <module>`; never leave files in `/tmp`.
- Never print tokens (`/srv/friday/state/*.token`, satellite `senha`, `GEMINI_API_KEY`).
- Text: non-empty after strip, ≤ 500 chars. Body cap on the satellite: 8 MB (`8 * 1024 * 1024`), larger → 413.
- Maximum wait: 300 s (5 min), counted on the satellite from receipt; the bot's retry window is also 300 s from the request.
- Bot retry: only connection errors (refused/timeout), every 15 s, until 300 s; 404 → `sem_suporte` (final); other 4xx/5xx → `erro` (final).
- Answers per satellite: `vou_tocar | na_fila | calado | sem_suporte | erro | reenviando | nao_entregue`.
- TTS identity is centralized: every TTS call takes voice, model and speaking rate from `voice_settings.tts(cfg)` (Task 4a); nothing else reads `FRIDAY_VOICE`, `FRIDAY_TTS_MODEL` or `FRIDAY_WHISPER_TTS_MODEL`. Announcement TTS timeout 15 s.
- Cache dir `/srv/friday/state/announce-cache/` (env `FRIDAY_ANNOUNCE_CACHE`), cap 200 files, oldest mtime out; key `sha256(f"{voice}\n{model}\n{rate}\n{text}")` hex.
- Token file `/srv/friday/state/announce.token` (env `FRIDAY_TOKEN_ANNOUNCE`), mode 600, created by the server agent with `secrets.token_urlsafe(32)` when missing.
- Satellite WAV dir `/home/jose/anuncios/` (env `FRIDAY_ANNOUNCE_DIR`); id regex `^[a-z0-9]{8,32}$` (same as `_ID_MENSAGEM`).
- Sound key `sons.anuncio = "anuncio_elevenlabs.wav"`; fallback file `anuncio_a.wav`.

## Review Focus

- The same announcement queued twice (HA retried) → plays twice; acceptable, but two different ids must never overwrite each other's file (`<id>.wav`). Test in Task 3.
- DND turned on while an announcement waits in the queue → it is dropped at play time, not played. Test in Task 2.
- A conversation opened by the wake word during the chime/speech: playback stops at once and the WAV file is removed. Test in Task 2 (actions) and Task 3 (file).
- Text with only spaces / > 500 chars reaching the bot directly (not via HA) → 400, no TTS call. Test in Task 4.
- Bot restart while a retry is pending → the retry is lost (accepted; logged at start). No test; noted in release notes.

---

### Task 1: The announcement chime as a satellite sound

**Files:**
- Modify: `satellite/program/sons.py` (`VARIANTES`)
- Modify: `satellite/program/configuracao.py:17` (`PADROES["sons"]`)
- Modify: `agents/server/friday_server_agent/provisioning.py:93` (`VAULT_FOR_BUNDLE`)
- Modify: `vault/manifest.json` (new entry via `scripts/vault.py put`)
- Test: `satellite/program/test_sons.py` (create if missing), `tests/agents/test_ota_packages.py`
- House (outside git, same task): `/homeassistant/tools/satelite/sons/originais-elevenlabs/announcement.wav`, `anuncio_elevenlabs.wav`, `LEIA-ME.md` row.

**Interfaces:**
- Produces: `cfg["sons"]["anuncio"] == "anuncio_elevenlabs.wav"`; `anuncio_a.wav` created by `sons.garantir(pasta)`; release bundles carry `program/sons/<every top-level vault sound>`.

- [ ] **Step 1: Failing tests**
  - `test_sons.py::test_garantir_cria_anuncio_a`: `sons.garantir(tmp)` returns a dict containing `"anuncio_a"` and the file is a 48 kHz stereo WAV.
  - `test_sons.py::test_padrao_do_anuncio`: `configuracao.PADROES["sons"]["anuncio"] == "anuncio_elevenlabs.wav"`.
  - `test_ota_packages.py::test_bundle_carries_top_level_sounds`: with a fake vault containing `satellite/sounds/anuncio_elevenlabs.wav` and `satellite/sounds/atuais-20261012/bipe.wav`, the package file list has `program/sons/anuncio_elevenlabs.wav` and not `program/sons/atuais-20261012/bipe.wav` (follow the fixture pattern of `test_package_refuses_missing_vault_set`).
- [ ] **Step 2: Run, expect FAIL** — `cd satellite/program && TMPDIR=$(mktemp -d) python3 -m unittest -q test_sons`; `bash scripts/test.sh -k ota_packages` (or the suite's selector).
- [ ] **Step 3: Implement**
  - `VARIANTES["anuncio"] = {"a": [(784, 0.10), (1047, 0.16)]}` (two rising notes).
  - `PADROES["sons"]["anuncio"] = "anuncio_elevenlabs.wav"`.
  - `VAULT_FOR_BUNDLE["satellite/sounds/"] = "program/sons/"` (the packager already takes only direct children of a prefix).
- [ ] **Step 4: House + vault**
  - Copy the owner's file (`/root/.claude/uploads/6dd83224-0a1a-41e6-8a55-c8cc3a2cc67e/09e05b8b-Soft_warm_smart-spe_4-1795183865725.wav`) to `/homeassistant/tools/satelite/sons/originais-elevenlabs/announcement.wav`.
  - `cd /homeassistant/tools/satelite/sons && python3 -c "import preparar_sons as p; p.preparar('originais-elevenlabs/announcement.wav','anuncio_elevenlabs.wav')"`; add the `LEIA-ME.md` row (`sons.anuncio`, desde 20/11, origem, pico −7,5 dBFS).
  - On the VM, `python3 scripts/vault.py put <file> satellite/sounds/anuncio_elevenlabs.wav --source tools/satelite/sons/anuncio_elevenlabs.wav` (same way the other tones were stored; copy the file to the VM first); commit the manifest change.
- [ ] **Step 5: Run, expect PASS**; **Step 6: Commit** `feat(satellite): announcement chime as a satellite sound`.

### Task 2: `Maestro` announcement queue and playback state

**Files:**
- Modify: `satellite/program/maestro.py`
- Test: `satellite/program/test_maestro_anuncio.py` (new; reuse the `Maestro`/clock helpers of `test_maestro_alertas.py`)

**Interfaces:**
- Produces:
  - constant `ANUNCIO = "anuncio"` (new state); `Ajustes.anuncio_max_s: float = 300.0`.
  - `Maestro.anuncio(anuncio_id: str, agora: float, ocupado: bool = False) -> tuple[str, list]` → `("calado", [])` in DND; `("na_fila", [])` when `ocupado`, `estado != PARADA`, an alarm is active, or `mudo is None`; else `("vou_tocar", acoes)`.
  - Playing actions: `[("volume_alerta", True), ("luz", "falando"), ("anuncio_tocar", anuncio_id)]`.
  - Event `evento("anuncio_fim", agora, anuncio_id)` → `[("volume_alerta", False), ("luz", <repouso>), ("anuncio_apagar", anuncio_id)] + self._proxima_da_fila(agora)`.
  - Dropped (expired or DND at play time) → `[("anuncio_apagar", anuncio_id), ("registrar_anuncio", {"id": ..., "resultado": "expirado"|"calado"})]`.
  - In `ANUNCIO`: `palavra` with `mudo is False` → `[("anuncio_parar", id), ("volume_alerta", False), ("anuncio_apagar", id)] + self._abrir(agora, "palavra", self._sussurro(valor), self._palavra(valor))`; any `botao` → `[("anuncio_parar", id), ("volume_alerta", False), ("luz", <repouso>), ("anuncio_apagar", id)]`; other events → `[]`.
  - `publico()` gains `"fila_anuncios": len(self._fila_anuncios)`.
- `_proxima_da_fila` order: alarms, then deliveries (unchanged), then `_fila_anuncios` (oldest first, dropping expired/DND ones).

- [ ] **Step 1: Failing tests** (`TestAnuncio`):
  - `test_idle_plays`: `anuncio("abcd1234", 0)` → `("vou_tocar", [("volume_alerta", True), ("luz","falando"), ("anuncio_tocar","abcd1234")])`, `estado == "anuncio"`.
  - `test_dnd_is_dropped`: DND on → `("calado", [])`, queue empty.
  - `test_muted_plays`: `mudo = True` → `"vou_tocar"`.
  - `test_boot_holds`: `mudo = None` → `"na_fila"`; after `mudo` becomes `False`, a `tique` at 10 s plays it.
  - `test_open_conversation_waits_then_plays`: conversation open → `"na_fila"`; after the conversation closes and returns to `parada`, the next tick emits `("anuncio_tocar", id)`.
  - `test_expires_after_300s`: queued at 0, tick at 301 → actions contain `("anuncio_apagar", id)` and `("registrar_anuncio", {"id": id, "resultado": "expirado"})`, no `anuncio_tocar`.
  - `test_dnd_while_queued_drops`: queued, DND on, conversation ends → dropped with `"calado"`.
  - `test_two_in_order`: two queued play one after the other (`anuncio_fim` of the first triggers the second).
  - `test_alarm_and_delivery_go_first`: queued alarm and delivery play before the announcement.
  - `test_wake_word_cuts_and_opens`: in `anuncio`, `palavra` (mudo False) → starts with `("anuncio_parar", id)` and opens a conversation with `como_abriu == "palavra"`.
  - `test_button_stops`: in `anuncio`, `botao` → stops, back to `parada`, no conversation.
  - `test_room_speech_ignored`: in `anuncio`, `fala_comecou` → `[]`.
- [ ] **Step 2: Run, expect FAIL**: `cd satellite/program && TMPDIR=$(mktemp -d) python3 -m unittest -q test_maestro_anuncio`
- [ ] **Step 3: Implement** in `maestro.py` (`_fila_anuncios` initialised outside `_zerar`, next to `_fila_entregas`; `self._anuncio` = id playing or None).
- [ ] **Step 4: Run the new module and `test_maestro test_maestro_alertas`, expect PASS**; **Step 5: Commit** `feat(satellite): announcement queue in the maestro`.

### Task 3: Satellite program and local port

**Files:**
- Modify: `satellite/program/porta_local.py` (`criar_app`/`iniciar` gain `anuncio_fn=None`; route `POST /announce`)
- Modify: `satellite/program/friday_satelite.py` (`anuncio()`, `_agir` cases, startup cleanup, pass `s.anuncio` to `porta_local.iniciar`)
- Test: `satellite/program/test_porta_local_anuncio.py` (new), `satellite/program/test_satelite_ligacao.py` (extend)

**Interfaces:**
- Consumes: Task 2's `Maestro.anuncio` and the actions `anuncio_tocar | anuncio_parar | anuncio_apagar | registrar_anuncio`.
- Produces: `POST /announce` — auth as `/doorbell` (401); header `X-Announcement-Id` matching the id regex (else 400); body > 8 MB → 413 (set `client_max_size` on the app to the cap + 1 KB); body must start with `RIFF` (else 400); writes `<dir>/<id>.wav` atomically (`.tmp` + `os.replace`); returns `{"resposta": anuncio_fn(id)}`. Without `anuncio_fn` the route does not exist (404).
- `FridaySatelite.anuncio(anuncio_id: str) -> str` calls `self.maestro.anuncio(anuncio_id, time.monotonic(), ocupado=self._claim_pendente)`, runs the actions, prints `{"anuncio": id, "resposta": ..., "estado": ...}`.
- `_agir("anuncio_tocar", id)`: task that plays `self._som_anuncio()` (configured file, or `anuncio_a.wav` when missing) then `<dir>/<id>.wav` with `self.alto.tocar_wav`, sleeps the sum of both durations (`_duracao_wav`), then feeds `self.evento("anuncio_fim", id)` (follow how `_repetir_alarme`/timer events reach the maestro). `anuncio_parar`: cancel that task, `self.alto.parar_ja()`. `anuncio_apagar`: unlink the file, ignore missing. `registrar_anuncio`: print JSON line.
- At startup: delete leftover `*.wav`/`*.tmp` in the dir.
- Music playing (MK1): duck it during `anuncio_tocar` exactly as the reminder chime of a delivery does (find that path in `ducking.py`/`friday_satelite.py` and reuse it; restore on `anuncio_fim`/`anuncio_parar`). Test with a fake ducker: duck on play, restore on end and on cut.

- [ ] **Step 1: Failing tests**: 401 without token; 400 bad id; 400 non-RIFF; 413 over cap; 200 writes `<id>.wav` and returns the fn's answer; two ids → two files; `_agir("anuncio_apagar")` removes the file; `_agir("anuncio_tocar")` with a missing configured chime plays `anuncio_a.wav` then the WAV (fake `alto` recording calls).
- [ ] **Step 2: Run, expect FAIL**; **Step 3: Implement**; **Step 4: full satellite suite PASS** (`cd satellite/program && TMPDIR=$(mktemp -d) python3 -m unittest -q`); **Step 5: Commit** `feat(satellite): /announce on the local port`.

### Task 4a: One source for the TTS voice, model and speaking rate

**Files:**
- Modify: `voice_gateway/voice_settings.py`
- Modify: `voice_gateway/whisper_mode.py` (`stream_whisper_tts` takes the settings and the rate)
- Modify: `voice_gateway/bot.py:1566-1571` (whisper wiring reads `vs_cfg.tts(cfg_voz)`)
- Test: `voice_gateway/test_voice_settings.py`, `voice_gateway/test_whisper_mode.py`

**Interfaces:**
- Produces:
  - `voice_settings.TTS_MODELO_PADRAO = "models/gemini-3.8-flash-tts"`.
  - `voice_settings.tts(cfg=None, ambiente=None) -> dict` = `{"voice": voz(cfg, ambiente), "model": <FRIDAY_TTS_MODEL or FRIDAY_WHISPER_TTS_MODEL or TTS_MODELO_PADRAO>, "rate": cfg.get("speaking_rate") or "normal"}`.
  - `voice_settings.tts_texto(texto: str, rate: str) -> str`: the text sent to the TTS with the pace applied. Before writing it, check the installed `google-genai` (the friday image) for a pace/rate field in `types.SpeechMetadata` or `SpeechConfig`; if one exists, use it and keep `tts_texto` returning the text unchanged; if not, prefix the Gemini TTS pace instruction in Portuguese for `slow` ("Fale devagar: ") and `fast` ("Fale rápido: "), nothing for `normal`. Record which one in the ledger as a ruling.
  - `whisper_mode.stream_whisper_tts(client, tts: dict, text: str)` (replaces `model, voice` arguments; keeps `style="whispers"`; applies the rate the same way).
- Consumed by Task 4 (`anuncios.falar` receives `tts: dict`).

- [ ] **Step 1: Failing tests**: `tts({})` with an empty environment → voice `"Charon"`, model `TTS_MODELO_PADRAO`, rate `"normal"`; `tts({"voice": "Kore", "speaking_rate": "slow"})` → `"Kore"`, `"slow"`; `FRIDAY_TTS_MODEL` wins over `FRIDAY_WHISPER_TTS_MODEL`; `tts_texto("Oi", "normal") == "Oi"`; `slow`/`fast` change the request (prefix or field, per the ruling); `stream_whisper_tts` builds its request from the dict (fake client records the call: voice name, model, style `whispers`).
- [ ] **Step 2–4**: FAIL → implement → PASS (voice gateway suite on the VM image). **Step 5: Commit** `refactor(voice): one source for TTS voice, model and rate`.

### Task 4: Bot `/announce` (TTS, cache, fan-out with retry)

**Files:**
- Create: `voice_gateway/anuncios.py`
- Modify: `voice_gateway/bot.py` (route in `registrar_rotas`, token reader, `DepsAnuncio`)
- Test: `voice_gateway/test_anuncios.py`

**Interfaces:**
- Produces (`anuncios.py`):
  - `validar(corpo, satelites: dict) -> tuple[str, list[str]]` raises `ValueError`; `satellites` missing → `sorted(satelites)`; unknown id → error.
  - `class Cache(pasta: str, limite: int = 200)` with `ler(chave) -> bytes | None`, `gravar(chave, wav: bytes)` (evicts oldest mtime over the cap).
  - `wav_48k_estereo(pcm24_mono: bytes) -> bytes` (resample with `whisper_mode._reamostrar(pcm, 24000, 48000)`, duplicate channels, WAV header).
  - `async def falar(client, tts: dict, texto: str) -> tuple[bytes, dict]` — `tts` from `voice_settings.tts(voice_settings.ler())` read **per announcement** (Home Assistant changes apply without restart); non-streaming `client.aio.models.generate_content` with `response_modalities=["AUDIO"]`, `PrebuiltVoiceConfig(voice_name=tts["voice"])`, the rate applied per Task 4a (`tts_texto`), no style; returns (PCM 24 kHz mono, usage dict for `usage_ledger`).
  - `chave(tts: dict, texto: str) -> str`: sha256 of voice, model, rate and text.
  - `async def anunciar(corpo, deps) -> dict` → `{"id": <token_hex(6)>, "satelites": {sid: answer}, "cache": "hit"|"miss"}`; on TTS error/timeout every satellite gets `"erro"`; satellites that failed by connection error get `"reenviando"` and a background retry task (every 15 s until 300 s from start; final answer logged).
  - `deps` provides: `satelites`, `ip_wifi(sid)`, `enviar(ip, senha, anuncio_id, wav) -> str` (POST `http://{ip}:8700/announce`, header `X-Announcement-Id`, `Content-Type: audio/wav`, timeout 5 s; 404 → `"sem_suporte"`), `tts(texto) -> (pcm, usage)`, `cache`, `registrar(linha)`, `agora()`, `dormir(s)`.
- `bot.py`: `@app.post("/announce")` — `cc.token_confere(_ler_token_announce(), Authorization)` else 401; `ValueError` → 400; returns `anunciar(...)` JSON. Logs `entrega: {...,"tipo":"anuncio"}` per announcement; appends the TTS usage with `ul.append_voice(..., model=<modelo>, ...)` (session `anuncio:<id>`).

- [ ] **Step 1: Failing tests** (`unittest.IsolatedAsyncioTestCase`, fake deps): changing `speaking_rate` or `voice` in the settings file misses the cache and the TTS gets the new values; token checks (route-level test with the FastAPI app like other route tests, or `cc.token_confere` + `anunciar`); `"   "` and 501 chars → `ValueError`, TTS not called; cache hit → `tts` not called, `cache == "hit"`; TTS raises → all `"erro"`; one satellite refuses connection then succeeds on the 2nd try (fake clock) → first answer `"reenviando"`, log shows final `"vou_tocar"`; never succeeds → final `"nao_entregue"` after 300 s; 404 → `"sem_suporte"`, no retry; Wi-Fi IP tried after the cable IP fails; `wav_48k_estereo` output is 48 kHz, 2 ch, twice the frames of the input.
- [ ] **Step 2: Run, expect FAIL** (voice gateway tests run on the VM image: `bash scripts/test-all.sh` step "voice gateway", or the module alone there).
- [ ] **Step 3: Implement**; **Step 4: PASS**; **Step 5: Commit** `feat(voice): /announce with TTS cache and satellite fan-out`.

### Task 5: Server agent `announce` command

**Files:**
- Create: `agents/server/friday_server_agent/announce.py`
- Modify: `agents/server/friday_server_agent/__main__.py` (add `**announce.make_handlers(...)` to the handler map)
- Test: `tests/agents/test_announce.py`

**Interfaces:**
- Produces: `token(path: str) -> str` (creates the file 600 with `secrets.token_urlsafe(32)` when missing; never logs it); `make_handlers(post, token_path) -> {"announce": handler}` where `handler(cmd: dict) -> dict` posts `{"text": cmd["text"], "satellites": cmd.get("satellites")}` to `http://127.0.0.1:7860/announce` with the bearer and returns the bot's JSON; bot unreachable → `{"ok": False, "error": "bot_unreachable"}`; HTTP 400 → `{"ok": False, "error": "invalid", "detail": <bot's erro>}`.
- [ ] **Step 1: Failing tests**: token file created with mode `0o600`, reused on the 2nd call; handler posts with the bearer (fake `post`); unreachable → `bot_unreachable`; 400 → `invalid`.
- [ ] **Step 2–4**: FAIL → implement → PASS (`bash scripts/test.sh`, agents part). **Step 5: Commit** `feat(server-agent): announce command`.

### Task 6: Integration service `friday.announce`

**Files:**
- Modify: `custom_components/friday/services.py` (schema, handler, registration with `supports_response=SupportsResponse.OPTIONAL`)
- Modify: `custom_components/friday/services.yaml`, `strings.json`, `translations/en.json` (and `pt-BR.json` if present)
- Test: `tests/integration/test_announce_service.py` (pattern of `test_build_image_service.py`)

**Interfaces:**
- Consumes: Task 5's command `announce` (fields `text`, `satellites`).
- Produces: service `friday.announce` with `message` (required, string, ≤ 500 chars after strip, non-empty) and `device_id` (optional, list of F.R.I.D.A.Y. satellite devices; each resolved with `find_satellite` → `connection.hello.device["id"]`). Sends `await find_server(hass).send("announce", timeout=20, text=..., satellites=[ids] or None)`. Response = the bot's per-satellite answers. One logbook entry per call (follow `logbook.py`: "Announcement sent to MK1, MK2: <answers>"). Server error → `ServiceValidationError` with the code.
- [ ] **Step 1: Failing tests**: empty / 501-char message refused; non-satellite device refused; no device → `satellites=None`; two devices → both ids; response relayed when `return_response=True`; server disconnected → error.
- [ ] **Step 2–4**: FAIL → implement → PASS. **Step 5: Commit** `feat(integration): friday.announce service`.

### Task 7: Full suite, release notes, house handoff doc

**Files:**
- Modify: next release notes file in `docs/release-notes/` (the version after the last tagged one; if `v0.4.1.md` is still unreleased, add to the next PATCH file)
- House (outside git): `/config/docs/claude/friday-producao.md` short section "Anúncios nos satélites" (how to call `friday.announce`, the token file, the cache dir, logs).

- [ ] **Step 1**: `bash scripts/test-all.sh` → rc 0.
- [ ] **Step 2**: Release notes: feature, changes by area (integration, server agent, bot, satellite, sounds in every bundle), promotion-gate checks: announcement plays on the MK1 idle; waits during a conversation; dropped in DND; held during a reboot and played after; wake word cuts it; TTS cache hit on the 2nd identical text (log).
- [ ] **Step 3: Commit** `docs(release): announcements on the satellites`; open the PR per the owner's rules (pull master first; ready when CI is green).

The house script change (`script.alexa_s_broadcast_announce` calling `friday.announce`, and the living-room DND gate fix) happens after the release reaches the MK1, not in this plan.
