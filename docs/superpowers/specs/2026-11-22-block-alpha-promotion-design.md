# Block an alpha's promotion by hand (22/11/2026)

> Superseded in part by `2026-11-23-mk1-music-and-next-alpha-design.md` (Goal 4): the switch and the promotion now follow the **next** (oldest waiting) alpha, not the newest.

Short design, approved by the owner on 22/11 (option 1). Calls marked **(builder's call)** were filled in by the designer.

## Why
v0.4.2 shipped a regression that the alpha soak cannot see: the integration flip-flopped a dual-interface satellite's
address and left the alpha satellite's entities `unavailable`. The soak only fails on an OTA rollback or exhausted
retries on the alpha satellite, so v0.4.2 would have been promoted to stable on its own 48 h later. On 22/11 the
promotion was stopped by editing the OTA state on the VM by hand (`soak.v0.4.2.failed = true`). The owner needs that
stop as a control in Home Assistant.

## Goals
1. **A switch on the Server device: "Alpha promotion"** (`switch.friday_server_alpha_promotion`, entity category
   config). It follows the **newest promotable alpha** (owner, 22/11): the newest eligible pre-release not yet decided
   (not promoted or superseded), not failed, and not older than the newest stable, whether it is blocked or not. Example:
   v0.4.2 failed and v0.4.1 soaking: the switch tracks v0.4.1 (on); turning it off blocks v0.4.1.
   - **on** = it will be promoted when the soak ends; **off** = blocked by hand.
   - **unavailable** only when no such alpha exists. It shows the real state, never the intent.
   - The sensor keeps listing every alpha with its own status (failed ones included); the switch skips the failed.
2. **An action `friday.set_alpha_promotion`** (`tag` optional, default the alpha the switch tracks; `allowed: bool`), for
   automations and for an alpha other than the tracked one. The switch calls the same server command.
3. **A manual block is its own flag** (`soak.<tag>.blocked`), separate from `failed` **(builder's call)**: allowing
   again clears only the hand block, never an automatic failure (that evidence stays). `promoted()` requires
   `not failed and not blocked`. The soak clock keeps running while blocked, so allowing after 48 h promotes on the
   next OTA cycle.
4. **Visible:** the sensor's `status` gains `blocked` (rows of `alphas` too); the block and the allow are written to
   the OTA events log and to the logbook (`by: owner`).
5. **The manual edit of 22/11 is honoured:** v0.4.2 stays `failed`; the switch skips it (unavailable when no other promotable alpha exists), and neither the switch nor the action can block or allow a failed tag (`failed` error).

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

## Final review additions (22/11)
- A block that arrives while the promotion call to GitHub is in flight: the orchestrator re-checks `blocked`/`failed`
  under the lock right before the call and skips it; if the block still lands during the call, the tag is recorded as
  promoted, a warning is logged and a Telegram push tells the owner the block came too late.
- A superseded tag has its own error code, `superseded` ("A newer release replaced this alpha; it will not be promoted."),
  distinct from `promoted`.
