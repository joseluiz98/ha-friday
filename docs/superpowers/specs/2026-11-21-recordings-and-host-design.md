# Satellite recordings play on every satellite, extra words record no-voice rejections, and a satellite that changes IP is followed (21/11/2026)

Short design, approved by the owner on 21/11 (items 1–4). Calls marked **(builder's call)** were filled in by the designer.

## Why
- On the MK2 (living room) no recording in the System Health card plays: every one says "recording not ready". The
  satellite agent serves `/v1/recordings*` with the **program's local password** (`__main__.py`: `add_routes(app, token, …)`),
  but Home Assistant presents the **HA token** (`ha_token`). On the MK1 (paired by hand, legacy) both are the same
  password, so it works; on any satellite adopted with the spoken code (MK2 and every new one) the agent answers 401.
  Since 03/11 (`df1c0b0`).
- The integration turns every non-200 into 404, so a refused token, an offline satellite and a missing file all read
  "recording not ready".
- No-voice rejections of the extra words (`sexta_feira`) are never recorded; only Friday's are (`sem_voz_…`). Since
  v0.4.0 the MK2 rejected `sexta_feira` without voice 9 times in one afternoon (TV) with no clip: exactly the
  negatives the wake word training needs.
- The satellites have no DHCP reservation (the router takes 16). The integration stores the satellite `host`; when the
  agent announces itself with another address (zeroconf), the flow aborts "already configured" and nothing updates:
  after a router/mesh reset that moves the IP, the satellite goes unavailable until fixed by hand.

## Goals
1. **Recordings accept the HA token (owner, 21/11).** The agent's recordings routes accept the HA token **or** the
   local password (legacy satellites keep working) **(builder's call: both)**.
2. **The card says what happened (owner, 21/11).** The proxy view answers: 404 only for an unknown device or a missing
   file; **403** when the agent refuses the token (401/403); **502** when the satellite is known but not connected, or the
   agent fails or answers anything else. The card shows "recording not ready" (404), "satellite refused" (403) and
   "satellite offline" (502).
3. **Extra words record their no-voice rejections (owner, 21/11).** Same as the patience rejection already does: the
   clip is the whole episode plus ~2 s before (`trecho_do_episodio`), file prefix `<word>_sem_voz_`
   **(builder's call)**, registry line type `sem_voz` with `palavra`. Only with "Record activations" on and no other
   recording in progress, as every other clip. Titles without event attributes read "rejected (no voice)" for
   `*_sem_voz` and "rejected (patience)" for `*_paciencia` **(builder's call: the prefix fallback already mislabels the
   extras' patience clips as conversations)**.
4. **A satellite that moves is followed (owner, 21/11).** When a known, paired satellite announces itself (zeroconf)
   with another host or port, the integration updates its subentry and reloads the entry. Same address: nothing
   happens. A pairable announcement keeps today's adoption path.
   - Note: the agent announces its default-route address. On the MK2 that is the cable (.25) while the subentry holds
     the Wi-Fi address (.34); after this ships the MK2 connection moves to the cable, which is the better path.

## Non-goals
- A DHCP reservation, or changing `/share/friday/satellites.json` (the house channel already falls back to the Wi-Fi
  address reported to HA).
- Rotating the satellites' local passwords (separate, later, owner 21/11).
- Re-recording old episodes; the sync of recordings to the VM.

## Anti-goals
- A legacy satellite (MK1) losing its recordings.
- A recording served to a caller with neither token.
- A zeroconf announcement from a pairable or unknown device rewriting a configured satellite's host.

## To confirm on the alpha (MK1) / after promotion (MK2)
MK1: recordings in the card still play; a `sexta_feira` no-voice rejection leaves a `sexta_feira_sem_voz_…` clip that
plays. MK2 (after promotion): its recordings play; its subentry host becomes the cable address after the next agent
announcement.
