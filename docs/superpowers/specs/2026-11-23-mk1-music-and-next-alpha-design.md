# MK1 music end to end, brain Code Mode, and "next alpha" promotion (23/11/2026)

Short design, approved by the owner on 23/11. Ships only through OTA (satellite migration, server image, voice gateway,
integration). Release: **v0.4.4** alpha (together with PR #51). Calls marked **(builder's call)** were filled in by the
designer.

## Why
On 23/11 at 09:00 "reproduzir o meu Flow do Deezer" on MK1 failed three times over:
1. MK1's music player (`sendspin-cli`) has been crash-looping since the 23:08 reboot (5,911 restarts):
   `cannot open ALSA device 'music_vol'`. `/etc/asound.conf` links to `/etc/friday-satelite/asound/jabra.conf`, and
   `/etc/friday-satelite` is `drwx------ jose` (it holds tokens). The service runs as user `sendspin-cli` and cannot
   traverse it. Latent since 19/11 (0004/0005 put the files there); the first reboot exposed it. Every satellite.
2. Gemini called `script__music_play` with `media_player.f_r_i_d_a_y_office`, an id it made up from the friendly name
   (the prompt said `media_player.escritorio`). The script returned `ok:false` with no reason after 5 s.
3. The brain fell back to Code Mode, wrote `process.env.FRIDAY_HA_URL` and died
   (`Cannot read properties of undefined (reading 'FRIDAY_HA_URL')`); no answer was delivered.

Separately, the Alpha promotion sensor shows the newest alpha even when an older one is promoted first.

## Goals
1. **Music audio files readable by the player.** Board ALSA files live in `/etc/friday-audio/` (dir 0755, files 0644);
   `/etc/asound.conf` points there. `/etc/friday-satelite` keeps its mode. A new migration moves existing satellites and
   restarts `sendspin-cli`; the image builds the new layout. The old files under `/etc/friday-satelite/asound/` are left
   in place so a rollback to v0.4.3 still finds them (**builder's call**).
2. **Bot corrects the player on its own (owner: option 1).** For a satellite session with a player in the catalog,
   before any `script__music_*` call reaches HA:
   - each player in `players` / `player` / `to_players` that does not exist in HA is replaced by this satellite's
     player (duplicates dropped); log at info `player corrigido: <bad> -> <own>`;
   - a player argument that is missing gets this satellite's player only where the script requires one
     (`players` on `music_play`, `player` on the single-player scripts) **(builder's call)**;
   - when the resulting players are all `unavailable`, the call does not reach HA and returns
     `{"result": {"ok": false, "reason": "player_unavailable", "players": [...]}}` at once.
   - Existing players that are not this satellite's are left untouched (a request for the living room stays there).
   - HA state read fails (timeout 1 s): pass the call through unchanged (**builder's call**: never block music on the
     guard).
3. **Brain never uses Code Mode.** The server image sets `tools.codeMode.enabled false` (OpenClaw 2026.11.3 key,
   verified on the VM) at start, next to the other `openclaw config set` lines, so HA calls go through `exec`, which sees
   the environment.
4. **Alpha promotion sensor shows the alpha promoted first (owner, 23/11, corrected).** Promotion itself is
   unchanged. `sensor.friday_server_ota_alpha_promotion` shows the alpha with the earliest promotion time (state and
   `tag`); with none timed (blocked, failed, not installed), the newest as before. Trigger: an older alpha promoting at
   lunch while the sensor said "tomorrow" (the newest).

## Non-goals
- The house package `friday_music.yaml` (outside the repo, not OTA).
- The brain's `AGENTS.md` (outside the repo).
- `whisper_mode` or any other music path.

## Anti-goals
- Music landing on another room because the guard rewrote an existing player.
- A guard that adds latency to every music call when HA is slow (1 s ceiling, then pass through).
- Any change to which alpha is promoted, or when.

## To confirm on the alpha (MK1)
"Reproduzir o meu Flow do Deezer" plays on MK1; reboot MK1 and music still plays; a brain request that touches HA (e.g.
"quais rotinas da Alexa existem") answers; the Alpha promotion sensor shows v0.4.3 while v0.4.4 soaks.
