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
