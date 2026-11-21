# Satellite error light: red after 2 min without network, and a red flash with every error beep (21/11/2026)

Short design, approved by the owner on 21/11. Calls marked **(builder's call)** were filled in by the designer.

## Why
On 21/11 the MK2's mesh node went down. Every request played the error beep ("caiu do Wi-Fi"), but the ring showed
nothing. The red network light (`net_error`) only lights when the recovery chain is **exhausted** (3 chain reboots in
24 h). When the Pi is not at fault (router or mesh node down), the chain never reboots and never exhausts, so the
light **never** lit. And the error beep had no visual feedback at all.

## Goals
1. **Persistent red after 2 min without network, whatever the fault (owner, 21/11).** The `net_error` light comes on
   when the network has been failing for ≥ 120 s, with the Pi at fault or not. It goes off when the network is back.
   - `friday-netwatch.timer` runs every **1 min** (today 5 min), so the light comes on 2–3 min after the drop and goes
     off at most ~1 min after the network returns.
   - netwatch records `failing_since` (first failed check of the episode, set even during boot grace or an open
     conversation: those only hold the actions) and `last_check` (every run) in its state file. A new boot, a healthy
     check or no profile clears `failing_since`.
   - The program (already reading the state file every 5 s) lights red when `now - failing_since >= 120` **and**
     `now - last_check <= 180` (a stale file, netwatch not running, never keeps the light on) **(builder's call)**.
   - The ladder keeps today's pace **(builder's call)**: a round counts at most once every 300 s, so the first
     action still comes ~5 min after the first counted failure and one step at most every 5 min; the 3-reboot cap in
     24 h stays. `exhausted` stays, now only for the reboot cap (and in the log); it no longer drives the light.
   - Unchanged: the "sem Wi-Fi" speech (once per episode, DND off) and the unmute when the network returns (spec
     2026-11-08), now tied to the 2-min light instead of exhaustion.
2. **Red flash with every error beep (owner, 21/11: any cause, Gemini or other).** Wherever the program plays the
   `erro` sound, the ring shows the momentary `error` state for **2 s** **(builder's call: the 10 s of the external
   error command stays for OTA/setup)**, then returns to what it showed. On the Jabra ring it is a no-op (its `error`
   would be persistent and it has no red outside the mute LED) **(builder's call)**.

## Non-goals
- Persistent red for Gemini/brain/HA outages with Wi-Fi fine: those flash on each error beep only.
- The "aprendizagem cancelada" sound that falls back to the error beep (it is not a failure): no flash
  **(builder's call)**.
- Changing the ladder itself (steps, cap, Pi-at-fault test).

## Anti-goals
- A red light that stays on after the network is back or after netwatch stops running.
- The ladder bouncing the Wi-Fi every minute because the timer is faster.
- The flash leaving the ring stuck in red.

## Delivery
The timer unit lives in `/etc/systemd/system` from the image; OTA does not rewrite it. Migration 0008 adds a drop-in
`friday-netwatch.timer.d/interval.conf` (`OnUnitActiveSec=1min`) and reloads systemd; the repo's unit file changes too
for new images.

## To confirm on the alpha (MK1)
Turn the Wi-Fi off at the router for 3 min: red within 2–3 min, off within ~1 min after it returns, no Wi-Fi bounce
before ~5 min. A failed request (Wi-Fi off) beeps and flashes red for ~2 s. `systemctl list-timers friday-netwatch`
shows ~1 min.
