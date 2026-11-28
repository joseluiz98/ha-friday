# Satellite settings: the long version

The settings dialog of each satellite (gear → Advanced) keeps its help texts short. This page holds what does not fit there.

## Continuous sound cutoff (`continuous_speech_limit`, Pi key `speech_max_s`)

**What it does.** The satellite ends the conversation when it hears sound with no pause for this many seconds, while she is not speaking and nothing is pending with the brain. It exists so that TV, music or noise that the speech detector takes for a voice does not keep the microphone open forever. Default: 15 s. Origin: R13 of the satellite media spec (2026-10-21).

**How the count works.**
- The count starts when the detector hears speech start and restarts on every pause it notices.
- Speech that was already going on when the conversation opened (one breath: "Friday, turn on the…") or that began during her answer holds the conversation open, up to this limit (v0.4.6).
- Nothing pending with the brain: a request still waiting for the brain is never cut by this limit.

**The trade-off.**
- The detector may miss the short pauses of a long dictation. On 10 Nov a 15 s reminder dictation was cut at the limit, so the office satellite (MK1) runs with 0.
- **0 = off:** no dictation is ever cut, but a sound that never pauses (TV) keeps the conversation open until she ends it herself. This is a known open problem (evolution item 25, 25 Nov).
- A higher value (for example 30 s) keeps most dictations and still closes on TV, only later.

## Two-beam listening (`two_beam_listening` and six related keys)

**What it does.** On XVF3800 boards the satellite listens with two beams during a conversation: one aimed at the person who called her, one aimed at the TV. Speech that comes from the TV beam does not reach her and does not keep the conversation open. It needs "Only the wake word interrupts" to be on.

**When it falls back to v0.4.6 behavior.** With no direction lock on the caller, with no wake word starting the conversation, in whisper mode, or on a Jabra (which has no beams), the satellite behaves as in v0.4.6: the continuous sound cutoff applies and nothing is filtered by direction. The reason is written to the satellite journal (`why_not` in the `two_beam_config` line), not shown in the entity's attributes.

**Learn TV direction.** The "Learn TV direction" button listens for a short time while the TV is playing and stores `tv_azimuth` (degrees as the board sees it; -1 = no TV) and `tv_elevation`. The `TV direction learn` event reports `stored` or `refused`. It refuses with one of these reasons: `too_few_frames` (the room is too quiet), `spread` (the sound has no stable direction), `near_caller` (the direction is too close to where the caller usually stands), `aborted` (a conversation started meanwhile) or `error` (a USB failure). The board is always released at the end. Set the values by hand if you prefer.

**The margins.**
- `caller_margin` (dB): how much louder the caller's beam must be than the TV beam for the sound to count as the caller.
- `tv_margin` (dB): how much louder the TV beam must be than the caller's beam for the sound to count as the TV.
- Between the two, the sound is undecided: it counts as speech (so it can hold the turn open) until `speech_max` or the caller-silence rule ends it. Larger margins decide less often but wrongly less often.
- `caller_silence` (s) ends the turn after this long without the caller's voice, even if other sound goes on.

**The turn ceiling.** `turn_ceiling` (default 60 s) is the longest single turn. Longer speech is split in two and the conversation goes on; it is not cut.

## Volume (the `volume` number, Pi keys `volume_pct` and `volume`)

**What it does.** One volume per satellite, 0-100%, for voice and music together. Each percent from 1 to 100 has its own loudness. The board sits on its own level at or above the asked loudness (Jabra: 12 steps of 4 dB, one per ring LED; XVF3800: 61 levels of 1 dB) and an ALSA softvol named `Fine` attenuates the rest. The ducking of the music while she speaks is a separate control (`Music`).

**What you will notice.**
- The Jabra ring still shows steps: 70% and 77% light the same LEDs and sound different.
- The percents 0, 9, 18, 27, 36, 45, 55, 64, 73, 82, 91 and 100 sound as positions 0-11 did before (position 0 is new on the XVF3800: 4 dB under 9%). Below 9% it now keeps getting quieter. Volume never mutes.
- "Volume 1-10", "louder"/"quieter" and the Jabra buttons move one step, as before.
- `volume` in the satellite's configuration is the position 0-11, kept so that a rollback to an older release has a close volume.
- After a rollback, the music player keeps the new audio chain until it restarts, so voice and music can differ by up to 4 dB until then.

**Music Assistant.** Use the satellite's volume number, or a house mirror of it, as the player's volume control. The player's own volume stays at 100.
