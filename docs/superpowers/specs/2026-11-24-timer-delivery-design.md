# Timer delivery, Echo style (24/11/2026)

Design agreed with the owner on 24/11, in conversation. Ships only through OTA (satellite program, voice gateway,
server agent, integration). Calls marked **(builder's call)** were filled in by the designer. Release number: the
owner's call when it is cut (after v0.4.4).

## Why
On 24/11 the "almoço" timer rang on MK2 and the owner, on the sofa with the TV on, neither heard nor saw it. Findings:
1. At expiry the satellite plays 1.5 s of alarm and opens a full Gemini Live conversation (prompt, 43 tools, memory)
   just to speak the notice and listen for 5-8 s. The alarm sound pauses meanwhile and the ring is blue (listening or
   speaking), not the alarm color: in a 1 min test the conversation lasted 53 s and the alarm color never showed.
2. In that conversation any speech the detector hears counts as "answered" and ends the timer, TV included
   (`maestro.py`, `fala_comecou` while a timer conversation is open).
3. The 1.5 s cut sits inside the 3 s volume ramp (`alarm_ramp_s`): the only alarm heard before her voice is the quiet
   first half of the ramp; the ramp ends during the conversation and the alarm never ramps again.
4. Every timer delivery costs a full Live session even when nobody answers.

## Goals
1. **The timer only rings; no conversation at delivery.** No Gemini session opens at expiry. The microphone is closed
   to the model for the whole delivery; only the wake word and the button act. Speech detection (VAD) never ends a
   timer.
2. **Alarm shape.** The existing ramp (`alarm_ramp_s`, unchanged) plays first, then the beep loops at the alert
   volume. After **3 beep loops following the end of the ramp** the satellite plays one spoken notice of that timer
   (name) and the beeps go on. Cap stays 300 s, then the existing "not answered" notice to Telegram.
3. **Spoken notice made at creation (owner picked this).** When a timer is created, the bot synthesizes
   "o timer <name> terminou" with the Gemini TTS (same `voice_settings.tts` and cache as the announcements) and sends
   the WAV to the satellite with the timer id. The satellite keeps it until the timer ends or is cancelled. At expiry
   the satellite needs no network.
4. **Generic clip as the safety net.** Every satellite always carries a generic WAV ("seu timer terminou", in the
   Friday voice): shipped in the image and added to existing satellites by migration. It plays instead of the named
   notice for a timer with no name, with no per-timer file, or with a file that is missing or unreadable at expiry.
5. **Failures are logged and escalated.** Each TTS failure at creation is logged (timer id, error class, ms). When
   N failures happen inside a window, the server device exposes a problem sensor in HA; it clears when the window
   passes without a new failure. Defaults **(builder's call)**: 3 failures in 30 min. The push to the owner is a
   house-side automation (outside the repo, owner's OK before it is created).
6. **Stopping, Echo style.** Only after the wake word (or the button): a normal conversation opens on the satellite that
   heard it, already knowing which timers are ringing (name and room, on any satellite). "Friday, parar" alone stops
   **all ringing timers on all satellites** (like the Echo's "stop"); "parar o timer do escritório" stops the timers of
   that room; "parar o timer do almoço" stops that name. After stopping, she says which timer(s) ended and a closing
   phrase, in that same turn (this is the "feedback por voz ao encerrar o timer" idea). The stop reaches other
   satellites through a bot tool that fans the command out to every registered satellite (same path as the
   announcements).
7. **Ring.** The alarm state is **purple**, pulsing **faster** than do-not-disturb; both share brightness **40 %**
   (do-not-disturb dims from 100 % to 40 %: designer's reading of "ambos podem compartilhar o brilho", confirm on the
   alpha). Orange stays reserved for system states (setup, update). The ring shows the alarm color for the whole
   delivery since there is no conversation to cover it. Jabra unchanged.

## Non-goals
- Any change to the ramp or to who gets promoted/when (nothing else in OTA).
- The music alarm (R11, a timer with music): keeps its flow; must not regress.
- Volume levels (`alarm_volume`, normal volume). The owner decides those in HA.
- Speech recognition without the wake word ("stop" alone). Not available on the satellite.

## Anti-goals (a failure even if it technically works)
- The TV, a conversation in the room or any non-wake-word sound ends or mutes a timer.
- A TTS failure, a missing file or no network silences a timer: it must always ring and always have the generic clip.
- A Live session opening at expiry.
- "Parar" leaving a ringing timer on another satellite.

## Constraints
- One satellite can have several timers expiring together (existing queue); each plays its own notice once, at its turn.
- Timers live on the satellite; the bot only knows them through the commands it sends (`criar_timer` →
  `timer_criar`). The per-timer WAV must follow the timer id through restarts of the satellite program.
- The satellite endpoint receiving the WAV follows the announcements' pattern (port 8700, bearer, size/time limits).
- Git, comments and release notes in English; nothing from the house in the repo; sweep must pass.

## Approaches considered
1. **Notice synthesized at creation (chosen).** Zero network and zero latency at expiry; cost is one short TTS per
   named timer, even if later cancelled (cache softens repeats).
2. Synthesized at expiry: spends only on timers that ring, but needs the network at that moment and can delay the
   spoken name.
3. Generic clip only: free and offline but never names the timer. Kept as the fallback (goal 4), which together with 1
   covers every case.

## Left to the builder
- The new satellite endpoint and storage for per-timer WAVs, and cleanup (ended, cancelled, orphaned at boot).
- How the bot's failure counter reaches HA as a sensor (server agent / integration), thresholds as defaults above.
- The wake word during an alarm: the satellite that heard it pauses its alarm while the conversation is open and
  resumes it if the command did not stop its timers **(builder's call)**.
- Where the generic WAV is produced and shipped (vault, image, migration). Producing it is a paid TTS call: ask the
  owner first.
- The bot tool for stopping (`parar_timers`: optional room or name, default all ringing, all satellites) and the context
  injected into the wake-word session.

## To confirm on the alpha
A named timer on MK2 with the TV on: purple pulse, ramp, beeps, the name after 3 loops; "Friday, parar" from MK1 stops
the MK2 timer too and she names it; an unnamed timer and a forced TTS failure both play the generic clip; three forced
failures raise the HA problem sensor and it clears after the window.
