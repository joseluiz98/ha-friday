# Fine satellite volume (27/11/2026)

Design for the owner's approval. Calls marked **(builder's call)** were filled in by the designer.

## Why
Two separate faults, found on 27/11 on the first satellite (MK1, Jabra):

1. **The Music Assistant (MA) volume stopped working.** MA drives a house-side mirror (`input_number`) and an
   automation sends it to the Pi's local port with a copy of the port password. The password was rotated on 22/11
   and the copy was not: every request gets 401, silently (`continue_on_error`). Pi to HA uses a webhook with another
   secret, so the slider still follows voice and buttons.
2. **The slider had dead gaps even when it worked.** The satellite volume is the hardware PCM step: 12 positions
   (0-11, 4 dB apart on the Jabra). Percent becomes `max(1, round(p*11/100))`, so about 9 slider points share one
   step (69-77 all give step 8), 0-13 all give step 1, and the Pi echoes the step's percent back, so the slider
   jumps. The Jabra LED ring has one LED per step. The XVF3800 has 61 raw 1 dB levels, but the program uses 12.

## Goals
- Each of the percents 1-100 gives its own loudness, for voice and music together (the 21/10 "one volume" decision
  stays).
- The slider stays where it is put: the satellite reports the percent that was asked.
- The 12 grid percents (0, 9, 18, 27, 36, 45, 55, 64, 73, 82, 91, 100) of positions 1-11 sound exactly as today.
- Jabra and XVF3800. New satellites get it from the image; existing ones by OTA.
- MA uses the integration's number entity. No copied password, no fixed IP.

## Non-goals
- Finer LED feedback: the ring keeps showing the hardware step.
- A new spoken scale. "Volume 1-10" and "louder/quieter" behave as today.
- Changing ducking, the music equalization or the alarm ramp.

## Anti-goals
- A second volume the owner can see or has to keep in step.
- A satellite that is silent or stuck loud after a reboot, an update or a rollback.
- A loud blip while the slider moves.
- A music player volume of its own coming back through Sendspin: it stays at 100.

## Approaches
- **A. Keep 12 steps.** Cheapest. MA's slider is always 0-100, so the gaps stay.
- **B. Hardware step plus a software trim (picked by the owner).** The board sits on the step at or above the asked
  loudness and a new software control attenuates the rest. Costs: ALSA config, program, image, OTA; ring stays
  coarse.
- **C. Fine control for music only.** Cheaper, but music and voice get two volumes.

## Design

### Scale
- The truth is an integer percent 0-100.
- Each board has an anchor table: for each position k, `(p_k, dB_k)`, with `p_k = round(k*100/11)`.
  Jabra: `dB_k = -36 + 4k` (read from the mixer: -36..+8 dB). XVF3800: from today's `VOLUME_XVF` raw values,
  `dB = raw - 60`; position 0 changes from raw 0 (near silence) to raw 26 (-34 dB), 4 dB under step 1.
- Target loudness is the linear interpolation between anchors. The hardware position is the lowest k with
  `p_k >= p`; the trim is `target - dB_k` (zero or negative). On a grid percent the trim is zero.
- The XVF3800 keeps the same 12 hardware positions and uses the same trim **(builder's call:** one mechanism for
  both boards; using its 1 dB hardware levels would add a second path for no audible gain**)**.
- Positions 10 and 11 stay above 0 dB on the Jabra, as today.

### Bottom of the slider
**(builder's call, for the owner to confirm)** The satellite is never muted by volume, as today (mute is its own
switch). 0% is position 0, the quietest audible level (Jabra -36 dB, XVF3800 -34 dB), and 1-9% rise from there to
today's step 1. Today 0-13% all sound like step 1, so the bottom gets 4 dB quieter than it can go now.

### Audio path
A new ALSA softvol control, `Fine`, sits between both clients and the dmix, in `jabra.conf` and `xvf3800.conf`:
`voice -> [mono_out on XVF] -> fine -> dmix` and `music -> softvol Music -> fine -> dmix`. `Music` stays the ducking
control. Range about -6..0 dB in steps of 0.05 dB or finer, never the control's mute value. On the XVF3800 the trim
is applied before the USB output, so the echo canceller reference includes it. Sendspin keeps `output = music_vol`.
The control must exist before the first write (prime it, as `Music` is primed).

Hardware changes at once; a softvol change is heard after the buffer (about 0.4 s). When a change crosses a step the
program must never let the sum get louder than both the old and the new volume: it orders (and, if needed, delays)
the two writes. A short dip is acceptable. The bench decides the delay.

### Program
- `config.json` gets `volume_pct` (0-100). `volume` (position) is still written, as the hardware position, so a
  rollback to the previous release keeps a close volume. Without `volume_pct`, it is derived from `volume`
  (`round(k*100/11)`): nothing changes loudness on update. If an older program changed `volume` after a rollback
  (it no longer matches the position of `volume_pct`), `volume` wins on the next load.
- Voice `numero`: the position's grid percent. `percentual`: that exact percent. `mais`/`menos`: one hardware step
  from the current level, keeping the trim (4 dB on the Jabra, as today).
- Jabra +/- buttons: the board moves one step by itself; the trim stays; the program recomputes the percent from
  position and trim and reports it. At the ends the percent is clamped to what is heard.
- The alert volume, the alarm ramp and the restore after an alert use the same mapping; the restore returns the exact
  percent. The Jabra countdown, which draws with the volume LEDs, restores position and trim.
- The factory volume of a new satellite stays 65% on the Jabra and, on the XVF3800, the loudness of today's factory
  raw 75%.
- Answers to the voice gateway keep the same keys; `percentual` is the exact value.

A drag in MA sends many changes in a row (today most of them change nothing). The latest value wins: pending
writes and boundary delays are replaced, never queued. The `config.json` write is delayed until changes stop (as for
the buttons), on the local-port path too, to spare the SD card.

### Agent and integration
The `volume` number reports and sets `volume_pct`. Same entity, same range. A change made by voice, button or HA
shows in the entity at once, without waiting for the 20 s poll (check the existing event path; add it if missing).
During a drag, neither the poll nor the HA-change tracking may republish an older value.

The entity goes unavailable while the program does not answer (updates, restarts, outages), and the old mirror never
did. MA reads a state it cannot parse as volume 0. Before the switch, restart the program with music playing and
record what MA shows, whether it sends a volume when the entity returns, and whether it falls back to Sendspin's own
volume. Acceptable: MA shows a wrong number while the satellite is down. Not acceptable: loudness changes by itself
or Sendspin's volume moves. If it is not acceptable, the design comes back to the owner (fallback: a house-side
`input_number` kept in step with the entity, with no password or IP).

### Retiring the bridge
- MA: each satellite player's "volume control" becomes its integration number (MA's Home Assistant plugin accepts
  `number` entities; checked in MA 2.10.x source). MK1 and MK2. This is a manual MA setting, so it goes into the
  new-satellite runbook. Before switching MK2, record what controls its MA volume today and Sendspin's current
  level: if MA drives Sendspin and it is below 100, the switch changes how loud music is.
- This step does not depend on the OTA: it fixes the 401 today, still with 12 steps.
- Remove from the house, with backups, after MA is proven on the new entity: the volume bridge package, its two
  secrets, its test (`tools/friday_music/test_satellite_volume_package.py`) and, optionally, `volume_webhook_url` in
  the Pi config. Update the production doc, the new-satellite runbook and the memory note that describe the bridge.
- Remove the webhook code from the program. A leftover `volume_webhook_url` key stays harmless.

### Delivery and image
- Existing satellites: OTA. A new migration (0009) installs the two ALSA files and restarts what holds the output
  open (`sendspin-cli`, `friday-silence`), like 0008. It must install them itself: activation runs migrations before
  it installs system files, and restarts only the program and the agent. The activation snapshot, taken before the
  migrations, already covers both files, so a rollback restores them.
- Accepted edge: after a rollback `sendspin-cli` keeps the new chain, at the last trim, until it restarts, so music
  and voice can differ by up to 4 dB until then.
- New satellites: the image already takes both ALSA files from the release. `verify_image.sh` also checks that they
  define the `Fine` control, and the migration is recorded as done by the existing baking step.
- Release number: decided when it is cut.

## Proof
- Unit tests: for every percent 0-100 and both boards, position and trim; loudness strictly rises from 1 to 100;
  grid percents have zero trim; percent survives restart, old config and rollback; button and voice steps; write
  order across step boundaries.
- Bench (MK1 with the Jabra, MK2 with the XVF3800): the `Fine` control exists and acts on voice and on music; a
  microphone capture shows loudness changing inside one step; no loud blip while dragging the slider with music
  playing; no new Sendspin dropouts in 1 h; on the XVF3800 the wake word with music is no worse than before.
- End to end: MA slider moves the sound at each point and stays put, also while being dragged with music; the entity
  follows voice and the Jabra buttons; no feedback loop between MA and the entity; Sendspin's own volume stays at 100;
  a program restart with music does not change loudness.
- The OTA gate is the usual one: satellites are tested on the alpha channel; promotion to stable is the merge gate.

## Left to the builder
Softvol parameters, the write delay, where the anchor tables live, and test layout.
