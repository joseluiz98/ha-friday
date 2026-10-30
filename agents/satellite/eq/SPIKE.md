# EQ spike runbook (step 1, live, on the Pi 192.0.2.15)

State found 30/10: asound.conf = dmix `mixer_out` (Jabra) + `voice` / `music_vol` (softvol) / `music`;
no `/usr/lib/ladspa`, no caps, no alsaequal installed. `amixer -D equal` -> "Invalid CTL equal".

Never run in parallel with other heavy jobs (Pi 3). Stop `friday-satelite` before tests.

1. Backup: `sudo cp /etc/asound.conf /etc/asound.conf.bak.$(date +%Y%m%d)`
2. `sudo apt install caps libasound2-plugin-equal`; `sudo install -d -o jose /var/lib/friday`
3. Apply `asound.conf.proposed` (point `voice` and `music_vol` at `eq_out`). Do not touch the MA EQ.
4. Flat check: `amixer -D equal scontrols` lists 10 bands; all at 66 (0 dB). Play a reference file via `aplay -D voice`;
   compare by ear/recording with the pre-change capture; must be indistinguishable.
5. Calibrate FLAT/STEP in `eq.py` (dB per slider unit): `amixer -D equal cset name='00. 31 Hz' 80` and measure.
6. Metrics (30 min, music via Sendspin + one conversation): CPU of the player/aplay < 5 % of a core
   (`top -b -n1`, `pidstat 5`); no xruns: `journalctl -u friday-satelite -u sendspin --since "30 min ago" | grep -i -e xrun -e underrun` empty;
   no sync loss in Sendspin.
7. Pass -> restart agent (it detects `equal`, declares eq_bass/mid/treble). Fail -> do not declare; note reason in README.
Rollback: `sudo cp /etc/asound.conf.bak.<date> /etc/asound.conf`; restart sendspin and friday-satelite.
