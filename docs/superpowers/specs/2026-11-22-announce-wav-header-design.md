# Announcement noise: the TTS answer is a WAV file (22/11/2026)

Short design, approved by the owner on 22/11.

## Why
Every satellite announcement clicks before the speech and hisses after it. The cached announcement WAVs start with
the bytes `RIFF…WAVEfmt…` and end with ~120 bytes of text-like data, played as audio near full scale. The TTS model
returns a complete WAV file (header + trailing chunk), and `anuncios.falar()` treats every inline blob as raw PCM.
The chime file is clean; the satellite plays what it receives.

## Goals
- `falar()` returns only the audio samples: a blob starting with `RIFF` is opened with the `wave` module, its
  frames are taken at the header's rate, stereo is mixed down to mono, then the existing path (24 kHz, then 48 kHz
  stereo) runs. A raw PCM blob is unchanged. A `RIFF` blob that `wave` cannot read raises (never played as noise).
- The cache key gets a version so the noisy cached files are never served again; the size limit (lowered to 100 by the owner, 22/11)
  evicts them over time. No manual change on the VM.

## Non-goals
- The satellite program (it plays what it gets).
- The streaming whisper TTS (`whisper_mode.stream_whisper_tts`): not reported; left as is (builder's call).

## Approaches
Only one sensible way: strip at the source. Stripping on the satellite would leave the cache and every other
consumer wrong; deleting the cache by hand without the code fix would refill it with noise.

## To confirm on the alpha
One announcement on MK1 and MK2: chime, speech, no click or hiss before or after.
