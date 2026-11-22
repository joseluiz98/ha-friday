# Satellite address: cable first, Wi-Fi as fallback, no reload storms (21/11/2026)

Short design. Calls marked **(builder's call)** were filled in by the designer.

## Why
On 21/11 at 23:09 the integration logged "Satellite <living-room id> moved to <cable IP>" and, 10 s later, "moved to
<Wi-Fi IP>". The MK2 has two interfaces (cable and Wi-Fi). The agent announces only the default-route address,
but the announcement points at `friday-satellite.local`, and `avahi-daemon` on the Pi answers that name with both
addresses. The zeroconf step (v0.4.2, #47) took every answer as a move, updated the subentry and scheduled a reload.
Two reloads ran together and left the MK1's entities registered twice: every later setup logs
`ID <office id>_… is already used` and the MK1 shows all entities `unavailable` until Home Assistant restarts.

## Goals
1. **The satellite declares its addresses.** The announcement carries `wired=<ip>` and `wifi=<ip>` (each omitted when
   that interface has no IPv4). The agent re-announces when either changes (today's 30 s check).
2. **The integration prefers the cable (owner, 21/11).** The connection uses `wired` when present, otherwise `wifi`.
   An announcement only updates the subentry when the stored addresses differ from the declared ones; the IP the
   zeroconf answer came from is ignored for an announcement that carries `wired`/`wifi`. Old agents (no properties) keep
   v0.4.2's behaviour.
3. **Falls back without waiting for an announcement.** The subentry stores both addresses. When the connection on the
   cable fails, the next reconnect tries the Wi-Fi at once, with no entry reload.
4. **Returns to the cable by itself (owner, 21/11).** While on the Wi-Fi, the integration probes the cable and switches
   back after it answers for **60 s in a row** **(builder's call: avoids flapping on a loose cable)**.
5. **No reload storms.** An address change never reloads the whole entry: only that satellite's connection reconnects
   to the new host. Any entry reload that remains (e.g. pairing) is coalesced so two never run at once.

## Non-goals
- Disabling avahi on the Pis.
- DHCP reservations; changing `/share/friday/satellites.json` (the house channel).
- IPv6.

## Anti-goals
- A satellite's entities duplicated or left `unavailable` after any address change.
- Ping-pong between cable and Wi-Fi from answers of the same announcement.
- A satellite with only Wi-Fi (MK1) behaving differently from today.

## To confirm on the alpha (MK1) / after promotion (MK2)
MK1 (Wi-Fi only): no "moved to" lines over 24 h; entities stay available across an HA restart. MK2: connects on the cable;
unplugging the cable moves the connection to the Wi-Fi within one reconnect, with no reload; plugging it back returns to the cable
after ~60 s.
