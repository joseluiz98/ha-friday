# Two-beam listening: the satellite hears the person who called her (26/11/2026)

**Approved by the owner on 26/11/2026**, with every recommendation below (Q1–Q6). Design for option A of the owner's 26/11 investigation (house notes:
`/config/docs/research/2026-11-26-ouvir-quem-fala-investigacao.md`). The owner picked A on 26/11 and sees it as a
candidate for 1.0.0. Releasing 1.0.0 stays the owner's milestone (`docs/releasing.md`).

Marks used below:
- **(owner)**: decided by the owner.
- **(builder's call)**: filled in by the designer; the owner can overturn it.
- **(owner, 26/11)**: answered by the owner on 26/11 (last section).

## Why

The living-room satellite (MK2, XVF3800) sits just under the TV. The owner keeps it there: it is where the old Echo
stood, it faces the sofa, and it is the only spot with power **(owner)**. TV speech still reaches the conversation in
three ways.

1. **The tail of the request.** After the person stops, TV speech keeps Gemini's server VAD open and is glued to the
   request.
   - 18–21/11: 11% of turns with the TV on.
   - 23–25/11, after v0.4.2: ~2%.
2. **Follow-up turns made only of TV.** About 6 of 32 follow-ups with the TV on in 18–21/11. Three got a spoken answer
   to the TV ("Claro! Em que posso ajudar?").
3. **Continuous TV sound keeps the conversation open** (evolution item 25). The Pi's speech detector takes TV for a
   person. The only limit is `speech_max_s`, which also cuts long dictations, so the office satellite runs with it at 0.

The market answer for a TV the device does not control is to refuse what does not come from the caller (research
report "Voz em sala barulhenta 2", 19/11). The XVF3800 gives us two tools for that which we leave unused:
- **Per-beam speech detection.** `AEC_SPENERGY_VALUES` holds one value per beam: focused 0, focused 1, free-running,
  auto-select. Above 0 = probable speech.
- **Two fixed beams with their own azimuth and elevation.** Today both point at the same angle and the elevation is
  never set.

Today every speech decision runs on the Pi's Silero VAD over channel 1. That is the auto-select beam, which follows any
voice, the TV's included.

## How it works today (the parts this touches)

- **Capture** (`friday_satelite.py`, `CapturaDupla`):
  - Channel 1 (`OP_R 7 3`, ASR output of the auto-select beam) feeds the wake word, Silero (`openwakeword.vad`), the
    recordings and the arbitration level.
  - Channel 0 (`OP_L 8 0`, the post-processed auto-select) goes to Gemini (`xvf_conversa_canal0`).
- **Speech events.**
  - `DetectorFala` turns Silero probabilities (80 ms frames) into `fala_comecou` / `fala_parou`.
  - The maestro uses them for deadlines, "speech holds the conversation", `speech_max_s` (`fala_longa`) and the
    25/11 rule that her close waits for a new sentence.
- **Turns.**
  - In a normal conversation the turn ends by Gemini's server VAD (`silence_duration_ms` from `end_of_speech_pause_s`).
  - Only whispered conversations end turns from the Pi: `PiSpeechTurns`, server VAD disabled, `speech_started` /
    `speech_stopped` sent by the Pi.
  - With the server VAD off, pipecat streams audio **only inside the activity window**, plus a rolling preroll
    (`user_audio_preroll_secs`, 1.5 s for whispers). Gemini discards anything outside that window.
- **Beam lock** (`doa.py`, v0.4.2):
  - On the wake word: `AEC_FIXEDBEAMSAZIMUTH_VALUES [θ, θ]`, then `AEC_FIXEDBEAMSONOFF 1`, then channel 1 to `7 0` and
    channel 0 to `6 0`.
  - Released when the first answer ends, when the conversation closes, after 90 s, or at program start.
  - Outside a conversation the board runs in factory mode.

## Goals

1. **In a satellite conversation, only the caller's speech opens, holds and ends a turn.** TV speech heard through
   the board does not reach Gemini, does not start a follow-up turn, and does not keep the conversation open.
2. **No more false activations (owner's criterion, 26/11).**
   - Outside a conversation, the wake word, its channel and the factory beam behavior are unchanged.
   - Inside a conversation, nothing new can wake her.
3. **Item 25 without cutting dictation.** Continuous TV sound no longer holds the conversation. A long dictation from
   the caller is never cut by it.
4. **Identity as a tie-breaker (phase 2).** When both beams carry speech, the VM can check whether the voice is one
   enrolled on that satellite, and end the turn when it is not.
5. **Measured.** Every conversation records what decided each turn, so the owner can judge the release (and 1.0.0)
   from data, not impressions.

## Non-goals

- Moving the satellite **(owner)**.
- The 6-channel firmware and per-beam audio (option B), and identity-only (option C). Both stay recorded as fallbacks.
- Custom XMOS firmware.
- Changing the wake word, its threshold or its verifier.
- Jabra satellites: without an XVF3800 everything stays as today.
- The web client (`/client/`).
- Post-processing tuning on channel 0 (`PP_ATTNS_MODE`, `PP_MIN_NN`). It may be measured later, not shipped here.

## Anti-goals (a failure even if it works)

- A request the caller is saying is cut, or its start is lost.
- A dictation is cut. On 10/11 a 15 s reminder was cut as `fala_longa`.
- More false activations per hour with the TV on. Today: ~0.5–0.7/h, tiny sample.
- She keeps waiting in silence for a turn that never ends (a stuck activity window).
- A board left in fixed-beam mode after a crash, or the beam setting saved to flash.

## Constraints

- **Pi 3.** Never two heavy jobs at once. The new work is a few USB control reads per second and arithmetic: no second
  model on the Pi.
- **One USB owner (`XvfLink`).**
  - Every new read goes through it, with a hard time limit. The vendor read loop can retry for ~1 s, so cap it as the
    telemetry logger does: 300 ms timeout, at most 5 retries.
  - A failed read means "no energy data". It never blocks capture.
- **Never `SAVE_CONFIGURATION`.** Startup always releases the fixed beams, as today.
- **Gating stays off.** Gating mutes the beam with less speech energy, which would be the caller whenever the TV is
  louder.
- **Energy leaks between beams** on a 66 mm array: the TV shows up in every beam. Decisions use the **ratio** between
  the caller beam and the TV beam, never "energy above 0".
- **The processed DoA (`AUDIO_MGR_SELECTED_AZIMUTHS[0]`) chooses between the fixed beams by energy.** With a TV beam
  configured it may report the TV, so the wake-word direction keeps using the free-running window from before the lock.
- **Every TV test runs on the MK2, which is on stable.** Testing on the alpha needs the owner to switch its channel.
- **Repo rules.** Public repository: no house values. Settings through the integration. Tests in `scripts/test-all.sh`.

## Approaches (from the investigation, 26/11)

- **A. Two beams + identity, current 48k2ch firmware (chosen by the owner).** Spatial decision on the Pi from
  per-beam speech energy, Pi-driven turns, identity on the VM as a tie-breaker. No sound or firmware cost.
- **B. Per-beam audio with the 16k6ch firmware.** Channels 3–6 carry each beam's ASR output, so the VM decides on
  audio. Costs the MK2's music at 16 kHz and a reflash. It is the escalation if A's energies separate but not enough.
- **C. Identity only.** Speaker gate on today's channel 0. The fallback if the TV and the sofa cannot be told apart by
  direction or elevation.

## Design

### D1. Pi-driven turns in every satellite conversation

In conversations opened on a satellite with two-beam listening on (D8), the gateway runs as whispered conversations do
today:
- Gemini's server VAD is disabled.
- `PiSpeechTurns` turns the Pi's `speech_started` / `speech_stopped` into activity start / end.
- The Pi sends those events for every conversation, not only whispered ones.

This is what keeps TV audio out of Gemini: outside the caller's speech, nothing is streamed. **(builder's call:
reuse the whisper path, already in production since 20/10 with the 1007 trap solved.)**

- **Preroll.** The first turn uses 1.5 s, as whispers do: the wake word, the pre-recording and a request said in one
  breath ("Friday, liga a luz") arrive around the connect and must not be clipped. Every later turn uses 0.5 s: a longer
  preroll would bring back up to 1.5 s of TV from just before the caller spoke, and Silero starts after 2 frames
  (160 ms), so 0.5 s covers the onset. **(builder's call, tune from the recordings)**
- **Whispered conversations keep their own path:** whisper ratio, 1.5 s preroll, TTS voice.
- **Barge-in.** With "Only the wake word interrupts" on (default since v0.4.2) nothing changes, because no audio goes
  to Gemini while she speaks. With it off, two-beam listening is off too (D8). The user-speech barge-in needs the
  server VAD. **(builder's call)**

### D2. Two fixed beams during a conversation

While a conversation is open, the board runs in fixed-beam mode with two different directions:
- **Beam A (focused 0) = the caller.** It starts at the direction validated on the wake word, as today (`doa.py`
  window, consistency checks unchanged).
- **Beam T (focused 1) = the TV.** It uses the azimuth and elevation stored for the satellite (D5). When no TV
  direction is stored, beam T = beam A, and every rule below falls back to today's behavior.
- **Gating off. Elevation of beam A** stays 0, as today.
- **Routing for the whole conversation:**
  - channel 1 = `7 0` (ASR output of beam A), for the wake word, Silero, recordings and level;
  - channel 0 = `6 0` (processed beam A), to Gemini;
  - with the same read-back and fallbacks as the v0.4.2 lock.
- **When the caller direction cannot be validated (builder's call).** The `doa.py` check fails on 18% of the MK2's
  wake words (38 of 216, 22–26/11), and 27 of those 38 had the TV on.
  - **First retry.** Run the check again without the samples within ±30° of beam T (the TV sector, which the beam-lock
    research of 17/11 already planned as optional). The free-running beam bouncing to the TV is the usual reason the
    check fails with the TV on.
  - **If it still fails,** that conversation runs exactly as v0.4.6: server VAD, no beam T, factory beams.
  - **The Pi says which mode it chose in the connect body** (`pi_turns: true/false`). The gateway builds the Gemini
    session accordingly; the VAD mode is fixed at connect.
  - **Conversations opened without a wake word** (button, delivery, timer, presentation, announcement) also run as
    v0.4.6.
  - **The NDJSON records the mode,** so the release metrics show how much TV-on traffic the feature covered.
- **Lock duration:** the whole conversation **(owner, 26/11, replacing the first-exchange rule of 17/11 and 21/11)**.
  Release on close, error, the 90 s ceiling (re-armed on each caller turn, see D4) and program start.
- **The wake word during her answers** listens on channel 1, the caller beam. This lowers TV-triggered interruptions,
  and someone saying "Friday" from another corner reaches it attenuated. **(owner, 26/11: accepted)**

### D3. The caller-speech detector (Pi)

A new pure detector, `DetectorQuemChamou` in `detectores.py`, sits in front of `DetectorFala` while two-beam listening
is active in a conversation. Per 80 ms frame it combines:
- the Silero probability on channel 1 (now beam A);
- the latest energies read about every 125 ms with the DoA poll that already runs: `eA = SPENERGY[0]`,
  `eT = SPENERGY[1]`, plus the free-running `eF = SPENERGY[2]` for D4.

Each frame is classified:

| Class | Rule (thresholds are settings, defaults from Phase 0) | Effect |
|---|---|---|
| `caller` | Silero ≥ 0.5 and (eT == 0 or 10·log10(eA/eT) ≥ `caller_margin_db`, default 3 dB) | counts as speech, as today |
| `tv` | Silero ≥ 0.5 and eT > 0 and 10·log10(eA/eT) ≤ −`tv_margin_db` (default 6 dB), or eA == 0 < eT | **not speech**: does not start, hold or extend a turn |
| `unclear` | Silero ≥ 0.5 and the ratio between the two margins, or no energy data (read failed, both 0) | speech, but subject to `speech_max_s` (D6) |
| `silence` | Silero < 0.5 | silence |

- **`DetectorFala`** receives probability 1.0 for `caller` frames, the real Silero value for `unclear` frames, and 0
  for `tv` and `silence`. Its hysteresis and the end-of-speech pause stay as today.
- **The turn ends when the caller's voice stops (builder's call).** Once a turn has had `caller` frames, the turn ends
  after `caller_silence_s` (default 1.5 s) without a `caller` frame, even if `unclear` frames keep Silero up. This is the
  research's option 1 (end of turn by the caller's voice).
  - It covers the TV tail that sits between the two margins with no 800 ms pause.
  - `fala_parou` carries `por: "quem_chamou"` so the NDJSON can count it.
- **Classes are tracked per frame, not per segment.** The maestro learns the class of each segment from the detector,
  and it is the frame-level rule above that bounds a segment, not the class it had at onset.
- **Hard turn ceiling: 60 s (owner, 26/11).** With Gemini's VAD off, only the Pi ends a turn. Continuous `caller`-class
  sound could otherwise hold a turn indefinitely, for example a phone call in the room with the TV off, so eT = 0.
  - After `turn_max_s` (60 s, Advanced setting) of one turn, the Pi ends the **turn**: Gemini answers what it heard.
    The conversation goes on.
  - A longer dictation is split into two turns.
- **No TV direction stored, or energy reads failing for over 1 s:** the detector passes Silero through unchanged,
  which is today's behavior. It logs once.

**(builder's call:** an energy ratio, because leak makes absolute energy meaningless. The margins are **asymmetric**
(caller 3 dB, TV 6 dB): taking the caller for TV costs more than the reverse. The Phase 0 telemetry sets them before
promotion. Every run of `tv` frames of at least 0.5 s with Silero up goes to the Pi journal, so a caller quieter than
the TV can never vanish from the metrics.**)**

### D4. What the classes drive

- **Turn start and end (D1).**
  - `speech_started` / `speech_stopped` come from `DetectorFala`, which now ignores TV frames.
  - The tail of TV after the caller stops is `tv`. The turn ends at the caller's pause, and the TV audio never enters
    the activity window.
- **Follow-up.** A follow-up turn opens only on `caller` or `unclear` speech. A follow-up made only of TV never opens a
  turn, so Gemini never hears it.
- **"Speech holds the conversation"** (cérebro pending, `keep_listening_while_speaking`, the 25/11 rule "her close
  waits for a new sentence") counts only `caller` speech, plus `unclear` speech under `speech_max_s`.
- **Re-aiming beam A.** At the start of each follow-up window (after an answer ends), if the free-running beam shows
  speech (`eF > 0`) in at least 3 of 4 polls at a stable azimuth (within ±20°), and that azimuth is:
  - at least 30° away from beam A,
  - and at least 30° away from beam T,

  then beam A is re-aimed there. That covers the caller moving, or a second person in the household answering.
  **(owner, 26/11)**
- **The 90 s ceiling** counts from the last `caller` turn, not from the wake word. A long useful conversation is no
  longer released mid-way. **(builder's call)**

### D5. Where the TV is

- **Per-satellite setting "TV direction"** (integration, Advanced): azimuth (0–359°, in the board's `DOA_VALUE`
  frame) and elevation (0–80°), or `off` (default).
- **Button "Learn TV direction"** (integration, satellite device):
  - **When.** Run with the TV playing speech and nobody talking. The satellite must be idle, with no conversation; it
    refuses otherwise.
  - **Azimuth (20 s, fixed beams off).** It collects the free-running azimuth on frames with speech energy, then
    takes the circular median and the spread.
  - **Elevation.** With both fixed beams on that azimuth, it sweeps the elevation over 0, 15, …, 75° for 3 s each,
    keeping the one with the highest mean `eT`.
  - **Refusal.** It does not store the result if:
    - fewer than 40 frames had speech;
    - the spread exceeds ±30°;
    - or the azimuth falls within 45° of the median caller direction of the last 7 days. The Pi keeps that median from
      the lock log.
  - **Result.** It goes to the HA entity: stored, or refused with the reason.
  - **Cleanup.** It always ends with the fixed beams off. Nothing is saved to the board's flash.
- **Both ways (owner, 26/11).** The owner can also type the direction, for example from the Phase 0 telemetry. The
  button is the path for any new satellite.

### D6. Item 25 and `speech_max_s`

- `speech_max_s` keeps its meaning (continuous speech without a pause closes as `fala_longa`) but counts only
  **`unclear`** speech.
- **`caller` speech** is never cut by it, so dictations survive.
- **`tv` speech** is not speech, so it never holds the conversation and closes nothing either: the conversation simply
  ends by its normal deadlines.
- **Defaults.** With two-beam listening on, the satellite's `speech_max_s` default can be 15 s again. The office
  satellite (no TV, so no beam T) keeps the owner's 0 until the owner changes it. **(builder's call: no migration
  touches the owner's value)**

### D7. Identity tie-breaker (phase 2, VM)

- **Enrollment per satellite.**
  - Voice-learning sessions recorded on that satellite are already uploaded to the VM
    (`/srv/wakeword-data/incoming/<sat>/vozes/<person>/`).
  - The VM builds one mean embedding per person per satellite:
    - model: 3D-Speaker ERes2Net base (`sherpa-onnx`), with 1.5 s windows and 0.5 s hop;
    - storage: the house state on the VM, never in the repo.
  - The MK1 enrollment did not transfer to the MK2 (EER 23% vs 5% in-room), hence per satellite.
- **When it runs.** Only on turns the Pi marks `unclear`, or on `caller` turns while beam T also carries speech. The Pi
  sends a flag with `speech_started`. The gateway scores the channel 0 audio of the activity window.
- **Decision.** It applies only if the conversation's caller matched an enrolled voice on the wake word. After 2
  consecutive windows (≈2 s) below the threshold while beam T carries speech, the gateway ends the activity, a turn
  end without that audio's tail. Otherwise it changes nothing.
  - **People without enrollment** (visitors, anyone who never did the voice-learning session) get today's behavior.
    **(owner, 26/11)**
  - **Who is enrolled:** the owner on MK1 and MK2; another household member once they record a voice-learning session
    on that satellite **(owner, 26/11)**.
- **Threshold.** A setting, with the default from a clean labelled test (Phase 0). The 26/11 study gave EER 5% with
  in-room enrollment, but only on 16 positive windows from the enrollment session itself. That is not enough to ship
  active.
- **Which channel is scored (builder's call, decided by Phase 0 data).**
  - Enrollment audio and the 26/11 study clips are **channel 1** (ASR output, linear). Today Gemini gets **channel 0**
    (processed, AGC). The study's EER does not carry over to channel 0.
  - The controlled session records both channels, and the EER is measured on each. If channel 1 wins, two-beam
    conversations send channel 1 to the gateway. The switch already exists (`xvf_conversa_canal0: false`), and since
    v0.4.2 her own voice cannot reach Gemini, which was the reason for channel 0 on 18/11.
  - That choice also changes what Gemini hears, so it is measured with the glued-TV ruler before it ships.
- **Ships in shadow.** The gateway records the score and what it would have done; the owner turns it on after the
  data.
- **Cost.** About 0.05 s of CPU per 1.5 s window on the VM. Nothing runs on the Pi.

### D8. Settings and switch-back

- **New per-satellite setting "Two-beam listening"** (integration, Advanced):
  - **On:** D1–D6 apply on XVF3800 satellites.
  - **Off:** exactly today's v0.4.6 behavior, with the v0.4.2 lock, server VAD and Silero.
  - Its default is **on** for every XVF3800 satellite **(owner, 26/11)**. Without a TV direction (MK1), it brings
    Pi-driven turns, the whole-conversation lock and the caller-voice turn end. The alpha proves there that nothing got
    worse.
  - It requires "Only the wake word interrupts". With that switch off, two-beam listening behaves as off and says so in
    the Pi journal (`why_not` of the `two_beam_config` line); it is not shown in the entity's attributes.
- **Margins (`caller_margin_db`, `tv_margin_db`) and the D7 threshold.** Advanced numbers, with defaults from Phase 0.
- **"TV direction" and "Learn TV direction"** (D5).
- **The existing "Beam lock" switch.** It stays meaningful only with two-beam listening off, where it is today's lock.
  With two-beam listening on, the beams are part of the feature. **(builder's call: no second switch for the same
  hardware)**

### D9. Measurement (Phase 0 now, and in every release)

- **Phase 0, running since 26/11 11:50.** A read-only logger on the MK2 (`tools/satellite_measurements/xvf_telemetry.py`,
  transient unit `friday-xvf-telemetry`, 7 days) records azimuths, energies, processed DoA and fixed-beam state twice a
  second.
  - Analysis: `tools/satellite_measurements/xvf_telemetry_report.py`, new, read-only. It crosses the log with the TV's
    HA history and the conversation NDJSON, and answers:
    - the TV azimuth and its spread;
    - its distance to the caller directions;
    - eA/eT during real requests;
    - whether the auto-select output follows the free-running beam in fixed mode.
  - It sets the default margins and tells whether D5's refusal rules fit the room.
- **Phase 0, controlled session (owner present, ~15 min, not at 10:00).**
  - The TV alone, then the owner speaking from the sofa with and without the TV, with the satellite program stopped and
    a script driving the beams (`tools/satellite_measurements/two_beam_session.py`, new).
  - It gives labelled clips for D7's threshold and a direct eA/eT measurement with beam T aimed.
  - **It does not block the implementation (owner, 26/11).**
- **Per conversation, in the NDJSON** (`userSpeech` gains fields):
  - frames per class;
  - median eA/eT on `caller` frames;
  - beam A and T angles;
  - re-aims;
  - D7 scores and shadow decisions.
- **Key-event logs (owner, 26/11: no log inflation).**
  - The Pi journal and the gateway log record only key events: the mode chosen per conversation and why, re-aims,
    energy going stale, turn-ceiling splits, candidate dropped requests (TV-classified speech with nobody speaking), a
    Pi-driven turn that got no answer, and the calibration.
  - One summary line closes each two-beam conversation.
  - Nothing is logged per frame or per poll. The list and fields are in the plan's Traceability section.
- **Release judgement (the owner's).** Measured before vs after on the MK2:
  - glued-TV rate (`tools/friday_tv_in_request`);
  - TV-only follow-ups;
  - conversations that closed with the Pi hearing "speech" (12–31% on 25/11);
  - false activations per hour with the TV on (must not rise);
  - dictations cut (must stay 0).

## Rollout

1. **Phase 0 analysis** sets the defaults; the implementation proceeds in parallel **(owner)**.
2. **MK1 alpha (office, no TV).** Proves no regression with beam T off:
   - Pi-driven turns;
   - the whole-conversation lock;
   - re-aim;
   - nothing lost at the start of a request;
   - dictations intact.
3. **MK2 on alpha** (the owner switches its channel) with the TV direction set: the measurements of D9, and D7 in
   shadow.
4. **Promotion.** D7 is turned on only after its data.

## What is left to the builder

- Module layout. The detector is pure and tested, like the others in `detectores.py`; the beam control extends
  `doa.py`.
- How the class travels from the capture thread to the maestro (event payload).
- Read scheduling inside the existing 8 Hz DoA poll.
- The NDJSON field names.
- The calibration UI wording, in Home Assistant style (English) and pt-BR translation.

## Owner decisions of 26/11 (all as recommended)

- **Q1.** The caller lock lasts the whole conversation, with re-aim at each follow-up.
- **Q2.** The wake word listens through the caller beam during her answers: accepted.
- **Q3.** TV direction: both the "Learn TV direction" button and the typed value.
- **Q4.** Identity: the owner is enrolled on MK1 and MK2. Another household member is enrolled after their own
  voice-learning session on that satellite. Visitors and non-enrolled people get today's behavior.
- **Q5.** "Two-beam listening" is on by default on XVF3800 satellites.
- **Q6.** Hard per-turn ceiling of 60 s (`turn_max_s`). It ends the turn, not the conversation.
- **Execution:** Sonnet implementers advised by the designer. The owner is considering moving the MK2 to alpha early,
  for live tests.
