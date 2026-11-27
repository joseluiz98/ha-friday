# Announcement WAV Header Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** announcements carry only audio samples: a WAV-wrapped TTS blob is unwrapped, and the noisy cache is bypassed.

**Architecture:** a pure helper `pcm_do_blob(dados: bytes, mime: str) -> tuple[bytes, int]` in `voice_gateway/anuncios.py` returns (mono S16LE PCM, rate); `falar()` uses it before resampling to 24 kHz. `chave()` gains a version prefix.

**Tech Stack:** Python 3, stdlib `wave`/`io`/`audioop`-free numpy, unittest (`voice_gateway/test_anuncios.py`).

**Spec:** `docs/superpowers/specs/2026-11-22-announce-wav-header-design.md`

## Global Constraints
- Detection: `dados[:4] == b"RIFF"`. Rate for WAV = header framerate; for raw = `whisper_mode._rate_do_mime(mime)` (as today).
- WAV with sampwidth != 2 or unreadable → raise `RuntimeError` (no fallback to raw).
- Stereo WAV → mono by averaging the two channels (numpy, int16 result).
- Raw blob path must be byte-identical to today (odd trailing byte dropped).
- Cache key: `chave()` hashes `"v2\n" + <current string>`; a constant `CACHE_VERSAO = "v2"` in `anuncios.py`.
- Comments in English; git message in English; never `--no-verify`.
- Tests: `bash scripts/test.sh voice_gateway/test_anuncios.py` with `FRIDAY_TEST_DIR=ha-friday-wav` (if pytest does not collect it there, run `cd voice_gateway && python3 -m unittest test_anuncios` locally only if numpy is available; otherwise via test.sh with `-k`).

## Review Focus
- WAV whose data chunk is followed by a LIST chunk: output has no byte from that chunk — `test_blob_wav_drops_trailing_chunk`.
- First output sample is the first audio sample, not `RIFF` (18770) — same test.
- Raw PCM unchanged — `test_blob_raw_unchanged`.
- 48 kHz stereo WAV → rate 48000, mono length = frames — `test_blob_wav_stereo_48k`.
- Old cache entries not served — `test_chave_has_version`.

---

### Task 1: unwrap WAV blobs and version the cache

**Files:**
- Modify: `voice_gateway/anuncios.py` (`chave`, new `pcm_do_blob`, `falar` loop)
- Test: `voice_gateway/test_anuncios.py`

- [ ] **Step 1: Failing tests** (new class `BlobTest`):
  - `test_blob_wav_drops_trailing_chunk`: build a 24 kHz mono 16-bit WAV with `wave` from samples `[100, -100] * 50`, append `b"LIST" + struct.pack("<I", 12) + b"INFOISFTabcd"` and fix the RIFF size; `pcm, rate = an.pcm_do_blob(blob, "audio/wav")` → `rate == 24000`, `pcm == struct.pack("<100h", *[100, -100] * 50)`.
  - `test_blob_raw_unchanged`: `an.pcm_do_blob(PCM + b"\x07", "audio/L16;codec=pcm;rate=24000") == (PCM, 24000)`.
  - `test_blob_wav_stereo_48k`: 48 kHz stereo WAV, frames L=200,R=0 ×10 → `rate == 48000`, pcm = 10 samples of 100.
  - `test_blob_wav_invalid_raises`: `b"RIFF" + b"\x00" * 10` → `RuntimeError`.
  - `test_chave_has_version`: `an.chave(t, "oi") != hashlib.sha256(f"{t['voice']}\n{t['model']}\nnormal\noi".encode()).hexdigest()` and is stable across calls.
  - Extend the existing `falar` test path if one exists (fake client returning a WAV blob → returned PCM has no `RIFF`); if none exists, add `test_falar_unwraps_wav` with a fake `client.aio.models.generate_content` returning one part whose `inline_data` has `data=<wav>` and `mime_type="audio/wav"`.
- [ ] **Step 2: Run** — Expected: the new tests FAIL (`pcm_do_blob` missing).
- [ ] **Step 3: Implement** `pcm_do_blob(dados: bytes, mime: str) -> tuple[bytes, int]`; in `falar()` replace the two lines building `dados`/`pcm +=` with `pcm_b, rate = pcm_do_blob(blob.data, blob.mime_type)` then `pcm += _reamostrar(pcm_b, rate, 24000)`. Add `CACHE_VERSAO` to `chave()`.
- [ ] **Step 4: Run** the whole `test_anuncios` — Expected: all PASS. Commit `fix(voice-gateway): strip the WAV wrapper from announcement TTS`.
