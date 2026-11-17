# Satellite arbitration: automatic gain (AGC) check

Status: configuration and documentation review done. The live 6 dB playback test is **pending** (needs the owner in
the room); its exact procedure is at the end.

## Why this matters

Arbitration picks the satellite where the voice is loudest: `score = voice_dbfs + per-device offset`, with
`voice_dbfs` computed by `Satelite._voz_dbfs()` in `satellite/program/friday_satelite.py`. That only ranks satellites
correctly if the capture chain is linear (a 6 dB louder voice reads about 6 dB higher). Automatic gain control would
flatten the difference between a near and a far satellite and make the ranking meaningless.

## How `voice_dbfs` is computed (software side)

- `Captura` (`hardware.py`) runs `arecord -D default -f S16_LE -r 16000 -c 1 -t raw` and reads 2560-byte chunks (80 ms).
- `captura()` appends each raw chunk to `self.recentes` unchanged (`self.recentes.append(pcm)`).
- `_voz_dbfs()` concatenates the last 12 chunks (about 1 s before the detection) and calls `rms_dbfs()`
  (`detectores.py`): `20 * log10(rms / 32768)` over int16 samples; silence or any failure gives -120.
- **No software gain, normalization, AGC or noise suppression runs before it** (no numpy scaling, no audioop, no
  speex/webrtc processing on this path; the wake-word detector receives the same raw chunks). Verdict for the
  software path: linear.
- The `default` capture PCM is `jabra_entrada` in `/etc/asound.conf` on the MK1 Pi: an ALSA `plug` over
  `hw:CARD=USB,DEV=0` forced to 16 kHz mono. `plug` only converts format/rate/channels; it does not apply gain.

So the question reduces to what the microphone hardware/firmware does before the samples reach ALSA.

## Device 1: Jabra Speak 410

**Attached now:** yes. On the MK1 Pi (checked 2026-11-16):

```
$ arecord -l
card 1: USB [Jabra SPEAK 410 USB], device 0: USB Audio [USB Audio]
$ lsusb | grep -i -e jabra -e 2886
Bus 001 Device 004: ID 0b0e:0410 GN Netcom Jabra SPEAK 410      (no 2886:001a device present)
$ systemctl is-active friday-satelite
active
```

**Capture path the running program uses:** `arecord -D default` -> `pcm.!default` (`capture.pcm "jabra_entrada"`) ->
`plug` -> `hw:CARD=USB,DEV=0`, 16 kHz, 1 channel, S16_LE. `/proc/asound/card1/stream0` confirms the device itself
captures only mono S16_LE at 16000 Hz (`Rates: 16000`, `Channels: 1`). The services carry a drop-in
`jabra-only.conf` (`ExecCondition` runs a small presence check that waits up to 30 s for the Jabra); it does not
change the capture path.

**ALSA mixer (read-only, `amixer -c USB contents`):**

```
numid=5  'Headset Capture Switch'  BOOLEAN   values=off     (see below: the satellite's own software mute)
numid=6  'Headset Capture Volume'  INTEGER   min=0,max=7    values=5   dBminmax-min=-15.00dB,max=6.00dB
numid=4  'PCM Playback Volume'     ...       (playback, not relevant)
```

The only capture controls are one switch and one fixed hardware volume step. There is **no AGC switch** in ALSA.
`Headset Capture Switch = off` is not an AGC indicator: it is the satellite program's own software mute
(`ControleMudo` in `hardware.py` runs `amixer sset Headset nocap` / `cap`), and the satellite was muted when read.
While muted no wake word is acted on, so the owner must unmute before the live test. `Headset Capture Volume` was at
step 5 of 0..7 (range -15 dB to +6 dB); record the step before the test and leave it untouched (the volume step itself
changes the gap only by a fixed amount, but changing it between plays would corrupt the comparison).

**USB descriptors (read-only, `lsusb -v -d 0b0e:0410`):** the capture Feature Unit (unit 5) advertises
`bmaControls(0) = 0x03` = "Mute Control" and "Volume Control" only. The standard USB Audio Class "Automatic Gain
Control" bit is **not** advertised, so the host cannot read or disable AGC on this device through the standard
interface (the Feature Unit being absent of an AGC bit says nothing about firmware-internal processing).

**Vendor documentation:** the Speak 410 is a closed-DSP speakerphone. Public material lists echo cancellation and an
omnidirectional microphone; no Jabra datasheet found states whether the mic uplink applies AGC (the official
technical sheet could not be fetched here; third-party listings only mention "echo cancellation" and a noise filter,
and one retailer review calls the microphone level "regulated (AGC)"; that is user feedback, not a specification).
The HA-side plan (`/config/docs/claude/satelite-sala-xvf3800.md`, kept in the HA config, not in this repo) already records that the Jabra's AEC and noise
suppression are closed, with no adjustment and no raw audio.

**Verdict: UNKNOWN (cannot be ruled out, treat as suspect).** There is no control to inspect or disable, vendor
documentation is silent, and a speakerphone DSP commonly includes level regulation. Only the live 6 dB test can settle
it. Extra caveat for the test: the Jabra applies echo cancellation while it plays sound, so the test audio must come
from a separate speaker, not from the Jabra.

## Device 2: ReSpeaker XVF3800 (card name `Array`, VID:PID 2886:001a)

**Attached now:** no. `arecord -l` and `lsusb` on the MK1 Pi show only the Jabra; the board is on the bench and the
services were set to start only when the Jabra is present. The board was flashed to firmware v2.1.1 48k2ch on
2026-11-15, so the only parameter dump available is from the **factory v2.0.6** firmware (before the flash):
`/share/friday/xvf3800-backup-20261115/backup/params_v2.0.6.txt`. It must be re-read on v2.1.1 when the board is
attached (read-only: `xvf_host <PARAM>`; do not write).

**Capture path the program would use (planned, not yet configured):** per the plan
(`/config/docs/claude/satelite-sala-xvf3800.md`), `asound.conf` `jabra_entrada` would become a `plug` from
`hw:CARD=Array` (48 kHz, 2 channels) with a `route` taking **channel 1 (ASR)** and resampling to 16 kHz mono. Today
`/etc/asound.conf` on the MK1 Pi only defines the Jabra path, so nothing captures from the XVF3800 yet.
Channel 0 = conference output (post-processed, with AGC), channel 1 = ASR output, per the firmware note in the plan.
Cross-check in the v2.0.6 dump: `AUDIO_MGR_OP_L = MUX_USER_CHOSEN_CHANNELS[8] 0` (the processed/conference channel),
`AUDIO_MGR_OP_R = MUX_AEC_RESIDUALS[7] 3` (the slot that `AEC_ASROUTONOFF` switches to the autoselect beam, i.e. the
ASR output).

**AGC parameters in the v2.0.6 dump (evidence, command `xvf_host <PARAM>` captured in the backup):**

```
PP_AGCONOFF 1            PP_AGCDESIREDLEVEL 0.0045    PP_AGCMAXGAIN 64
PP_AGCGAIN 6.824211      PP_AGCTIME 0.8999988         PP_LIMITONOFF 1   PP_LIMITPLIMIT 0.47
AEC_ASROUTONOFF 1        AEC_ASROUTGAIN 1             AUDIO_MGR_MIC_GAIN 90
```

- The `PP_AGC*` block is the post-processor AGC. It is **on** (`PP_AGCONOFF 1`, max gain 64 = 36 dB, live
  `PP_AGCGAIN` 6.8 = not at unity). The post-processor limiter is also on.
- `AEC_ASROUTGAIN` is a single fixed number (1), not an adaptive one. `AUDIO_MGR_MIC_GAIN` is fixed.

**Vendor documentation (XMOS XVF3800 user guide, "Automatic speech recognition"):**
- "The ASR audio is extracted after the beamformer, and it is not fed into the post processor." The AGC lives in the
  post processor ("gain control block which ensures a consistent output level regardless of the distance of the
  speaker from the microphone", datasheet "Voice Processing Pipeline"). So the ASR channel bypasses the AGC and the
  noise suppression.
- "An optional fixed gain can be applied to match the requirements of different wake word engines"
  (`AEC_ASROUTGAIN`); the datasheet says ASR mode provides "a configurable fixed gain".
- The ASR output is not enabled by default (`AEC_ASROUTONOFF` selects it). The v2.0.6 dump reads `AEC_ASROUTONOFF 1`
  although the plan notes say the board ships with it off; re-read it on v2.1.1 and do not assume.
- Sources: https://xmos.com/documentation/XM-014888-PC/html/modules/fwk_xvf/doc/user_guide/08_automatic_speech_recognition.html
  and https://xmos.com/documentation/XM-014888-PC/html/modules/fwk_xvf/doc/datasheet/03_audio_pipeline.html

**Residual non-linearity to keep in mind (documentation does not rule these out):** the autoselect beam is chosen and
steered adaptively, so its level can shift a little with source direction (a position effect, which arbitration
partly wants, but it is not a pure level measurement); if the Pi ever captured channel 0 instead of channel 1, AGC
would be **on** and arbitration by loudness would be unreliable.

**Verdict: LINEAR on channel 1 (ASR) by documentation, AGC ON on channel 0; not yet verified on the v2.1.1
firmware or by measurement.** Requirements: capture channel 1 only; re-dump parameters on v2.1.1 and confirm
`AEC_ASROUTONOFF 1` and `AEC_ASROUTGAIN` unchanged; run the 6 dB test.

## Summary

| Device | Attached now | Capture path | AGC evidence | Verdict |
|---|---|---|---|---|
| Jabra Speak 410 | yes | `default` -> `jabra_entrada` (plug) -> `hw:CARD=USB`, 16 kHz mono | no ALSA/USB AGC control; vendor silent; closed DSP | unknown (suspect) |
| XVF3800 | no | planned: `hw:CARD=Array` 48k 2ch, channel 1 (ASR) -> 16 kHz mono; not configured | v2.0.6 dump: `PP_AGCONOFF 1` (channel 0 post-processor); vendor doc: ASR bypasses post-processor, fixed gain only | linear on channel 1 by doc, unverified; AGC on channel 0 |
| Software path | n/a | raw chunks into `rms_dbfs` | no gain/normalization before `_voz_dbfs` | linear |

## Interim stance and merge gate

Arbitration ships **loudness-first**, as the spec decided (`score = voice_dbfs + offset`, wake-word `score` only breaks
ties inside 1 dB). This live test is a **merge-gate item of the arbitration PR**: until it is run, the Jabra is
unproven. `pick()` is unchanged by this task, and `pick()` is global (one rule for every satellite), so the outcome
below changes it for all devices at once.

| Result | Action |
|---|---|
| Jabra passes (valid run, gap 4.5 to 7.5 dB, or above 7.5 dB) | `pick()` stays loudness-first. XVF3800 is then checked the same way when it is attached and capturing channel 1. |
| Jabra fails (valid run, gap clearly below 4.5 dB) | `pick()` switches to the wake-word `score` as the main criterion, globally (Task 6 step 3). |
| Mixed (one device passes, the other fails) | Use `score` globally until both devices pass and the per-device offsets are calibrated. |
| Spread too wide, quiet file too close to ambient, or too few samples | Inconclusive: repeat (in a quieter room or louder playback); it is neither a pass nor a fail. |
| Gap above 7.5 dB | Counts as linear (no AGC compression); the excess is window jitter or an expander, not AGC. |

Later calibration of the per-device offsets (`device_offset_db`, the spec's per-device offset) is a separate step: see
section 5.7 of the arbitration spec (about 10 spoken "Friday" per spot with both satellites in place).

## Pending live test: 6 dB linearity (needs the owner in the room and the owner's OK)

Run once per device (the Jabra now; the XVF3800 when attached and capturing channel 1). It does not write to the
satellite; the only action is producing sound.

### What the test really triggers (read first)

`voice_dbfs` is only produced for an **acted** wake word. Each play that passes the wake word detector (and is not
muted or in a conversation without a message) opens a **real conversation**: the bot answers, a **billed** Gemini
Live session starts, and a follow-up window stays open for a few seconds. Plays that land inside an open or pending
conversation are dropped, or print `{"arbitration": "pending"}` and make no claim. A run of about 10 plays means
about 10 short billed sessions (5 per level, plus ambient and optional steps): **the owner's OK is required at test
time** (measuring with paid models needs permission). Total sessions with the optional -12 dB step: about 15.

### Where the value is, and where it is absent

- **Primary source: the gateway arbitration log** on the VM, one NDJSON file per day under
  `/srv/friday/state/arbitration/YYYY-MM-DD.ndjson`. Every acted word that reaches the gateway leaves a record whose
  `candidates` list holds one entry per claiming satellite with `sid`, `wake_word`, `score`, `voice_dbfs`,
  `offset_db`, `busy`; `reason` is `alone` or `record_only` when the MK1 is the only satellite online (that is the
  normal single-satellite case), or `best`/`only_one_heard` after a round. Filter by the MK1 `sid`. Stop the
  simulated satellite (`tools/satellite_sim`) first, or its claims share the log.
- **Secondary source: the Pi journal** (`journalctl -u friday-satelite --no-pager | grep '"evento": "palavra"'`): the
  event line carries `"voice_dbfs"` inside `valor`. It is printed only when the program actually acts on the word.
- **Absent, by design:** shadow-mode words never emit an event (no claim, no value); a word dropped while a claim is
  pending prints only `{"arbitration": "pending"}`; a word that is not acted on (muted, test or enrolment state,
  open conversation without a message, wake-word stale) never claims; a claim that **loses** to another satellite is
  logged in the gateway round record but the losing Pi does not print a `palavra` event for it; a claim lost in
  transit (gateway unreachable) leaves no gateway record. If a play yields no record, count it as no sample and replay
  it; never fill the gap with a guess.

### Procedure

1. **Prepare two files from one recording** of a clear "Friday": `friday_hi.wav` (as recorded) and `friday_lo.wav` =
   the same file scaled by -6.0 dB in the digital domain (`sox friday_hi.wav friday_lo.wav vol -6dB`). Do not use a
   volume knob for the 6 dB step; it must be exact. Optional third file at -12 dB (`vol -12dB`).
2. **Fixed setup:** a **separate** speaker (phone or laptop; **not** the satellite's own speaker, because the Jabra
   runs echo cancellation while it plays) at a fixed point about 1 m from the satellite, aimed at it. On that player
   turn **off every sound enhancement** (loudness equalizer, Dolby/spatial effects, auto-volume, volume leveling) and
   leave its volume untouched for the whole test. Room quiet, no music, TV off. Unmute the satellite; record
   `Headset Capture Volume` (`amixer -c USB sget Headset`) and keep it unchanged. Stop the simulated satellite.
3. **Ambient reading (no speech):** the program holds the microphone exclusively, so measure the room level with the
   same function the program uses: with the owner's OK, `sudo systemctl stop friday-satelite`, record 5 s of silence
   (`arecord -D default -f S16_LE -r 16000 -c 1 -d 5 -t raw /tmp/ambient.raw`), restart the service, and compute
   `rms_dbfs` (from `satellite/program/detectores.py`) over the whole recording and over its last 1 s windows. Call the
   result `ambient_dbfs`. Remove `/tmp/ambient.raw`. (Repeat after the plays to check the room did not change.)
4. **Play** `friday_hi.wav` and `friday_lo.wav` alternately, at least **5 valid samples per level** (a sample is a
   play that produced a record with `voice_dbfs`). **Between plays, let the conversation close** (about 8 s of silence
   after her reply ends the short wait) **or end it by voice** (a short "thanks" closes it), and confirm in the journal
   that the state is back to idle before the next play. Never queue plays inside an open conversation.
   If the quiet file does not trigger, raise the player volume for both files together and start over.
5. **Compute** per level: median, min and max (or IQR) of `voice_dbfs`, and the count of valid samples. Gap =
   median(hi) - median(lo).
6. **Validity checks (all must hold, else the run is inconclusive):**
   - quiet-file median is at least **10 dB above `ambient_dbfs`** (otherwise noise-floor compression looks like AGC);
   - at least 5 valid samples per level;
   - spread (max - min) within each level is at most 4 dB (`voice_dbfs` is the RMS of the last ~1 s before the
     detection, so window jitter alone moves a single sample by a few dB; a wider spread cannot resolve a 3 dB-wide
     pass band; this 4 dB cut is a proposal for the live session to confirm).
7. **Verdict from the gap:** 4.5 to 7.5 dB = pass; above 7.5 dB = linear (counts as pass); below 4.5 dB (typically 0 to
   3 dB) on a valid run = fail (compression). Report every sample, both medians, the spread, `ambient_dbfs`, the
   quiet-file margin above ambient, and the capture volume step; do not change the gap criterion silently.
8. **Optional extension** (about 5 more sessions, only with the owner's OK): a third file at -12 dB; a linear path
   gives **12 +/- 3 dB** between hi and that file; an AGC path compresses it.
9. Record the result in this document (device, date, numbers, verdict) and apply the outcome table above.

Note: the deployed MK1 program may predate the `voice_dbfs` field (it exists on the arbitration branch). Deploy the
branch (bot first, then satellite) before the test, or neither the journal nor the gateway log will carry it.
