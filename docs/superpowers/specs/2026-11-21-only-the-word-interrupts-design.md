# Satellite answers: only the wake word interrupts, and the beam lock reaches Gemini (21/11/2026)

Short design, approved by the owner on 21/11. Calls marked **(builder's call)** were filled in by the designer.

## Why
On the MK2 (living room, under the TV), TV speech is glued to the user's request in 11% of the turns with the TV
on, against 1% with it off (18–21/11, `/config/tools/friday_tv_in_request/`). In at least 10 of 12 glued turns,
the Gemini Live sent an `interrupted` signal just before: her answer starts, TV speech reaches Gemini, the server
VAD cuts her and the TV text joins the turn ("TV aberta" → "Você disse que" → "Sim, pode falar. O que foi que eu
disse?"). Today, in a normal answer the satellite keeps sending microphone audio while she speaks. Only a
"recado" (a brain answer, a delivery, a presentation) closes the mic, and then only the wake word interrupts.
Separately, the beam lock (17/11) only routes channel 1 to the locked beam. The conversation audio comes from
channel 0 since 18/11, and the lock releases at the first pause the Pi hears, before Gemini closes the turn.

## Goals
1. **Only the wake word interrupts, in every answer, on both satellites, with or without TV (owner, 21/11).**
   While she speaks, the satellite sends no audio to Gemini. "Friday" cuts her and opens listening, through the
   same path the recado uses today (`parar_fala` + `cortar_fala`). The button keeps working as today.
2. **The mic reopens as soon as she finishes (builder's call).** The bot tells the satellite when Gemini marked
   the answer complete (`turn_complete`): then the mic reopens at her stop. Without that signal the satellite
   waits `RECADO_PAUSA_S` (1.5 s), as the recado does, because answers have pauses in the middle. The audio of
   that wait is kept and sent when the mic opens, so a "repeat" said right after her loses nothing. This shortcut
   applies to every recado, brain answers included (builder's call).
3. **The beam lock lasts through the first exchange and reaches Gemini (owner, 21/11).** Lock on the word as
   today (Beam lock switch, consistent direction). While locked, channel 0 also comes from the locked beam
   (`AUDIO_MGR_OP_L 6 0`, the processed focused beam 0), next to channel 1 (`OP_R 7 0`, as today). It releases
   when her first answer ends (the mic reopens, or the word cuts her), instead of at the first Pi pause. The other
   releases stay: conversation closed, USB failure, the 90 s ceiling, program start. If the board does not read
   back `6 0`, channel 0 stays on `8 0`, the program logs it, and the lock still holds on channel 1.
4. **Switch to go back (builder's call):** general setting "Only the wake word interrupts"
   (`only_wake_word_interrupts`), on by default. Off = today's behavior (mic open during her answers).

## Non-goals
- The web page client (`/client/`) keeps the free barge-in.
- TV speech that enters the follow-up window after the mic reopens (~6 turns in the data): the speaker gate, later.
- Ideas B (follow whoever speaks, refusing the TV direction) and C (follow the enrolled voice): recorded in
  `/config/docs/claude/friday-evolucoes.md`, section "XVF3800: beamforming e voz na sala".
- No change to the voice's texts (the "Se o usuário interromper você" sentence stays).

## Anti-goals
- A "repeat" right after her answer losing its first syllables.
- Her own voice reaching Gemini (the 18/11 problem): with the mic closed while she speaks, it cannot.
- The lock stuck after a crash (the start-up release stays).

## Consequences accepted
- Interrupting her without saying "Friday" no longer works on satellites (owner, 21/11).
- The echo meter (`eco_rms_db`) keeps measuring while she speaks, now with the mic closed (builder's call).

## To confirm on the alpha (MK1)
No audio underrun or robotic voice while she answers (with the mic
closed the Pi runs the wake word during her speech, as it already does in recados; the 06/10 skip existed for CPU);
`OP_L 6 0` is accepted and read back; channel 0 level while locked vs factory (if AGC does not apply there,
adjust before promotion); "Friday" cuts her at once; a "repeat" right after her answer keeps its start. After
promotion, on the MK2: glued TV turns and Gemini `interrupted` signals per turn, before vs after.
