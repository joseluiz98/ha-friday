# Block an alpha's promotion by hand (22/11/2026)

Short design, approved by the owner on 22/11 (option 1). Calls marked **(builder's call)** were filled in by the designer.

## Why
v0.4.2 shipped a regression that the alpha soak cannot see: the integration flip-flopped a dual-interface satellite's
address and left the alpha satellite's entities `unavailable`. The soak only fails on an OTA rollback or exhausted
retries on the alpha satellite, so v0.4.2 would have been promoted to stable on its own 48 h later. On 22/11 the
promotion was stopped by editing the OTA state on the VM by hand (`soak.v0.4.2.failed = true`). The owner needs that
stop as a control in Home Assistant.

## Goals
1. **A switch on the Server device: "Alpha promotion"** (`switch.friday_server_alpha_promotion`, entity category
   config). It controls the tracked alpha (the same one the `Alpha promotion` sensor shows):
   - **on** = it will be promoted when the soak ends; **off** = blocked by hand.
   - **unavailable** when there is no tracked alpha, or the tracked alpha already failed on its own (rollback /
     exhausted): there is nothing to allow **(builder's call)**. It shows the real state, never the intent.
2. **An action `friday.set_alpha_promotion`** (`tag` optional, default the tracked alpha; `allowed: bool`), for
   automations and for an alpha other than the tracked one. The switch calls the same server command.
3. **A manual block is its own flag** (`soak.<tag>.blocked`), separate from `failed` **(builder's call)**: allowing
   again clears only the hand block, never an automatic failure (that evidence stays). `promoted()` requires
   `not failed and not blocked`. The soak clock keeps running while blocked, so allowing after 48 h promotes on the
   next OTA cycle.
4. **Visible:** the sensor's `status` gains `blocked` (rows of `alphas` too); the block and the allow are written to
   the OTA events log and to the logbook (`by: owner`).
5. **The manual edit of 22/11 is honoured:** v0.4.2 stays `failed` (the new switch shows it `unavailable`).

## Non-goals
- Superseding older alphas when a newer one ships (option 3; left for later).
- Blocking a manual promotion done on GitHub (the owner's click stays final).
- Blocking stable releases or a satellite's own install.

## Anti-goals
- A block lost on a server agent restart or an OTA update of the server agent (it lives in the state file).
- The switch showing "on" for a tag that cannot be promoted.

## To confirm on the alpha
Turn the switch off on the next alpha: sensor `status: blocked`, no promotion after 48 h; turn it on: promoted at the
next OTA cycle.
