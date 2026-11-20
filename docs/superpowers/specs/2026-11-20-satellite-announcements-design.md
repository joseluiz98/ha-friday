# Announcements on the satellites (20/11/2026)

Design approved by the owner in conversation on 20/11. Calls marked **(builder's call, owner to check)** were filled
in by the designer.

## Why
The house announcement script (Home Assistant, ~40 automations, ~5 calls a day: washer finished, coffee, AC, etc.)
speaks only on the Echos. The satellites (MK1 office, MK2 living room) should speak the same announcements, side
by side with the Echos until the Echos are retired. The living-room Echo is unplugged for a few days while the MK2
is tested, so today the living room hears nothing.

## Goals
- A versioned F.R.I.D.A.Y. capability: "announce this text on these satellites", exposed by the integration as a
  service. It plays a chime, then the fixed text spoken in the F.R.I.D.A.Y. voice (the same voice as the
  conversation, `voice_settings`).
- The satellite decides when to play: it waits while a conversation is open and drops the announcement in
  do-not-disturb.
- Repeated texts cost nothing after the first time (cache).

## Non-goals
- No conversation: the announcement does not open the Gemini Live and does not open the microphone afterwards.
  ("We can evolve later", owner, 20/11.)
- No paraphrase: the text is spoken verbatim.
- No quiet-hours window in the satellite: do-not-disturb is the only gate. Quiet periods are house automations that
  turn each satellite's do-not-disturb on and off (owner, 20/11).
- The Alexa "cheguei" (arrival) routine is out: it is the next step, once announcements work.
- Retiring the Echos, or deciding which Echo stays silent when a satellite shares its room, is out.

## Anti-goals
- An announcement cutting or talking over an open conversation with her.
- An announcement playing in do-not-disturb.
- A satellite announcing a stale message long after the event.
- House orchestration inside the repo: which Echos and which satellites speak, and when, stays in the house script.

## Boundary (owner, 20/11)
- **Versioned (repo, ships by release):** the integration service, the server agent command, the bot endpoint,
  the TTS and cache, the satellite queue and playback, the chime as a satellite sound.
- **House (outside git):** `script.alexa_s_broadcast_announce` orchestrates Echos and satellites ("broadcast"):
  it calls `notify.alexa_media` for the Echos and the integration service for the satellites. Quiet-period
  automations that toggle the satellites' do-not-disturb also live here.

## Approaches
1. **Chosen: through F.R.I.D.A.Y.** The integration service reaches the bot on the VM; the bot makes the speech
   with the Gemini TTS (same model family the whisper mode already uses, `whisper_mode.py`), caches it, and sends
   the audio to each satellite; the satellite queues and plays it under its own rules.
2. Rejected: Home Assistant TTS played through Music Assistant on the satellite's player. Only the MK1 has a
   player (the MK2 does not), and the satellite would not know an announcement is playing, so it could not wait for
   an open conversation or honor its do-not-disturb.
3. Rejected: reuse `/deliver` (doorbell + Live conversation). She paraphrases, starts seconds later, opens the
   microphone, and costs a Live session per announcement.

## Behavior (decided)
- **Sound:** the chime, then the speech, both at the alert volume (`volume_alertas`), like reminders.
- **Open conversation:** the announcement waits in the satellite's queue and plays when the conversation ends.
  Same for a ringing timer alarm, a pending wake-word claim, or a delivery being opened. **One queue order
  (builder's call): alarms, then deliveries already queued, then announcements**; deliveries keep their 90 s expiry.
- **Maximum wait: 10 minutes (builder's call, owner to check)**, counted from the satellite's receipt. Older than
  that, it is dropped and logged.
- **Do-not-disturb:** dropped (not queued), like the Echo in DND. Answer `calado`.
- **Microphone muted (owner, 20/11):** it plays; mute means "don't listen", not "don't speak". Mute state still
  unknown (`mudo is None`, right after boot): it waits in the queue, as deliveries do.
- **Wake word during the announcement (builder's call, owner to check):** an active wake word stops the
  announcement and opens a normal conversation, consistent with the 19/11 rule "only an active wake word interrupts
  her". Room speech does not stop it. The detector already runs while she speaks (the 19/11 gate depends on it);
  stopping means killing the announcement's `aplay` and opening the conversation with `como_abriu: palavra`. If
  that proves unsafe in the build, the fallback is to finish the announcement and then honor the word (ledgered
  ruling).
- **Several announcements in a row:** played in order, one chime each.
- **Music playing (MK1):** ducked the same way as a reminder.
- **No answer expected:** after the speech the satellite returns to idle; no microphone, no "listening" light.

## The chime: same path as the other satellite sounds (owner, 20/11)
- Original: the owner's ElevenLabs file (1.48 s, 48 kHz stereo 16-bit) goes to the sounds master
  `tools/satelite/sons/originais-elevenlabs/announcement.wav` (house), prepared by `preparar_sons.py` into
  `anuncio_elevenlabs.wav` (trim, fade, peak −7.5 dBFS), and listed in the master's `LEIA-ME.md`.
- Vault: `satellite/sounds/anuncio_elevenlabs.wav` in `vault/manifest.json` (source
  `tools/satelite/sons/anuncio_elevenlabs.wav`), like the other tones. New images carry it (`vault_payload.py`).
- Registry: new key `sons.anuncio` in `configuracao.PADROES` = `anuncio_elevenlabs.wav`; synthetic fallback
  `anuncio_a` in `sons.py` (two rising notes), created by `garantir` when missing, like the other tones.
- Running satellites: today the OTA bundle carries only `satellite/sounds/onboarding/` from the vault, and
  `friday-ota-apply` already merges `program/sons/` into `~/satelite/sons/` (never deletes). **Builder's call
  (owner to check):** add the top-level `satellite/sounds/` to `VAULT_FOR_BUNDLE`, so every release carries the
  top-level tones (12 files, ~8.7 MB today, +1 with this one) and a satellite that predates a tone gets it. A
  migration points `sons.anuncio` at the new file when the file exists, like `0003_elevenlabs_tones.sh`.

## Flow
1. **Home Assistant, house script:** for the satellites it calls `friday.announce` (`message`, optional
   `device_id` list of satellite devices; default: every satellite), with `continue_on_error: true`.
2. **Integration, `friday.announce` (new service):** validates (non-empty, ≤ 500 chars; devices are F.R.I.D.A.Y.
   satellites) and sends an `announce` command to the server agent over the existing authenticated connection,
   with the satellite ids. Returns the per-satellite answers as the service response (`supports_response:
   optional`). Logbook line per call.
3. **Server agent (VM host), `announce` command:** posts to the bot at `http://127.0.0.1:7860/announce` (the
   container uses host networking), bearer token from `/srv/friday/state/announce.token` (600; created by the agent
   when missing; never printed), and relays the bot's answer.
4. **Bot (VM), `POST /announce`:**
   - Token checked like `/reminder`; validation repeated (text, satellite ids from `satellites.json`).
   - Speech: plain (unstyled) Gemini TTS with `voice_settings.voz()`; model from a new env var
     `FRIDAY_ANNOUNCE_TTS_MODEL` (default: the whisper TTS model, `gemini-3.8-flash-tts`). `stream_whisper_tts`
     hard-codes `style: whispers`, so the call is a sibling, not a reuse. Key = sha256(voice + model + text).
     Cache hit → reuse; miss → TTS (PCM 24 kHz mono) resampled to 48 kHz stereo 16-bit WAV, stored in
     `/srv/friday/state/announce-cache/` (cap 200 files, oldest out). TTS timeout 15 s. The WAV is speech only;
     the satellite plays its own chime.
   - Sends the WAV to each satellite's local port (cable IP, then Wi-Fi IP, as `_tocar` does) in parallel; each
     answer is `vou_tocar | na_fila | calado | sem_suporte (404) | erro`.
   - Replies at once with the per-satellite answers (it does not wait for playback). Logs one NDJSON line per
     announcement in the deliveries log (`tipo: "anuncio"`), with cache hit/miss and TTS ms. The TTS call goes
     into the cost log like the other voice calls.
5. **Satellite, `POST /announce` on the local port** (same auth as `/doorbell`): body = WAV bytes, header
   `X-Announcement-Id`. Body cap 8 MB (500 chars of speech ≈ 35 s ≈ 6.7 MB); larger → 413. The program writes it
   to `~/anuncios/<id>.wav` and asks `Maestro.anuncio(id, agora)`, which returns `calado | na_fila | vou_tocar`
   and actions. Playing = alert volume, chime (`sons.anuncio`), speech, back to rest; files are deleted after
   playing or expiring.

## Failures
- TTS fails or times out: `erro` for every satellite; the Echos still speak (the house script does not depend on
  the satellites). Logged.
- Satellite unreachable or on an older version (404): logged; no retry.
- VM or server agent down: the service raises; the house script continues (`continue_on_error`).

## House part (not in this repo, done at release time)
- `script.alexa_s_broadcast_announce`: new step calling `friday.announce` with `continue_on_error`.
- Fix found on 20/11: the Echo step runs only when the living-room Echo's DND switch is `off`; with that Echo
  unplugged the switch is `unavailable` and **no Echo speaks** (the bedroom one too). The gate should skip only the
  living-room Echo when its switch is not `off`, and keep the others.
- Quiet-period automations toggling each satellite's do-not-disturb (owner's schedule).

## Ships
Integration, server agent, bot and satellite program → alpha (MK1) first; the MK2 gets it when the release is
promoted to stable. Until then the MK2 answers 404 and is logged as `sem_suporte`.

## Tests
- Integration: service schema, device filtering (non-satellite device refused), command sent, response relayed.
- Server agent: command posts with the token, token file created 600 when missing, bot down → error.
- Bot: token refused/accepted; validation; cache hit does not call the TTS; TTS failure → `erro`; per-satellite
  fan-out with one satellite down; 404 → `sem_suporte`; log line.
- Maestro: idle → plays; open conversation → queued, plays on close; DND → `calado`; muted → plays; mute unknown →
  queued; older than 10 min → dropped; two queued play in order; alarm/delivery before the announcement; wake word
  cuts it and opens a conversation; room speech does not cut it.
- Local port: auth, bad body, size cap, file written.
- Sounds: `garantir` creates `anuncio_a`; bundle carries the top-level tones; migration idempotent.

## Left to the builder
Module layout, how the WAV reaches `aplay` (reuse the existing playback path of sounds and the alert volume), the
exact NDJSON fields, and the cache eviction details.
