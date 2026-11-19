# Timer and presentation: only an active wake word interrupts her (19/11/2026)

Short design, approved by the owner on 19/11. Extends `2026-11-16-delivery-barge-in-wakeword-design.md` (v0.3.7), which
already covers pending brain answers and Pi deliveries (reminders, `/deliver`, `/reminder`).

## Why
When the satellite opens a conversation on its own, room speech (people talking, the TV) cuts her before anyone
hears the notification. On 19/11 a timer on the MK2 (13:46) connected with someone already talking in the room and
that speech went into the conversation. The onboarding of the MK2 in the living room, with the TV on and people
talking, was hard for the same reason.

## Scope: every conversation the satellite opens by itself
| Opening | Today | After |
|---|---|---|
| Pi delivery / late brain answer | gated (v0.3.7) | unchanged |
| Timer (after the first alarm loop) | mic open | gated: her first speech |
| Presentation of a new satellite | mic open | gated: **every** speech of hers |
| Musical alarm, wake-word test, voice enrollment | no Live conversation | not applicable |

## Behavior (decided)
- **Gate** = the v0.3.7 "recado": while she speaks, the microphone is closed for the conversation (no audio to the
  Live), the wake-word detector keeps running, the light says "speaking". An active wake word (any word in `active`
  mode; `shadow` words do not count) cuts her (`parar_fala`) and opens the microphone. When she stops for more than
  `RECADO_PAUSA_S` (1.5 s) the microphone opens for the answer.
- **Timer:** her first speech is gated; after it the conversation goes on as today (same as deliveries).
  The timer counts as answered by the wake word, the button, or speech heard **after** the microphone opened. Room
  speech while she is speaking does not count, so if nobody answers, the alarm comes back as today (up to 5 min).
- **Presentation:** each time she starts speaking the gate closes again; the microphone opens only when she stops,
  i.e. when she expects the person's answer (the presentation is a fixed sequence of questions). The wake word cuts her at any
  step. The "say Friday twice" step is unchanged (the Pi's detector counts it).
- **The gate starts at connect** (gap found while planning, 19/11): `conectar` opens the microphone
  (`friday_satelite.py`) and the v0.3.7 gate only closed it at her first word, so room speech between the connection
  and her first word reached the Live, in deliveries too (the MK2 timer at 13:46 shows exactly this). Every
  conversation the Pi opens by itself starts with the microphone closed.
- **Unchanged:** conversations opened by the wake word or the button; hardware mute (the gate is in software:
  a hardware mute would also silence the wake-word detector).

## Approaches
- **Chosen: reuse the Pi-side gate** in `satellite/program/maestro.py` (`_fechar_mic_do_recado`, `_recado`).
- Rejected: a bot-side gate (a second mechanism for the same rule); hardware mute (kills the wake word, shows "muted").

## Tests
Room speech during the timer/presentation does not cut her; an active word cuts her; a shadow word does not; the
microphone opens at the end of each of her speeches; room speech during the timer speech does not mark it answered;
deliveries and normal conversations unchanged.

## Ships
Satellite program only → alpha (MK1) first, MK2 when the owner promotes the release to stable.
