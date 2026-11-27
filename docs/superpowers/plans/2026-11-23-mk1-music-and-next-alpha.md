# MK1 Music End to End and Next-Alpha Promotion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Music plays on every satellite after a reboot, the bot fixes a made-up player id by itself, the brain stops using Code Mode, and the Alpha promotion follows the next (oldest waiting) alpha.

**Architecture:** four independent tasks. Satellite: ALSA board files move to `/etc/friday-audio/` (applier, audio-select, image, migration 0008). Voice gateway: an `MCPClient` subclass rewrites the player arguments of `script__music_*` before the call. Server image: `tools.codeMode.enabled false`. Server agent: tracked/promoted alpha = the oldest waiting one.

**Tech Stack:** Python 3 (pytest via `scripts/test.sh`; `unittest` for `voice_gateway`), bash, OpenClaw 2026.11.3.

**Spec:** `docs/superpowers/specs/2026-11-23-mk1-music-and-next-alpha-design.md`

## Global Constraints
- New ALSA dir: `/etc/friday-audio` mode `0755`, files `jabra.conf`/`xvf3800.conf` mode `0644`; link `/etc/asound.conf -> /etc/friday-audio/<board>.conf`. Never chmod `/etc/friday-satelite`. Old files in `/etc/friday-satelite/asound/` are not deleted.
- Migration number: `0008`, file `satellite/migrations/0008_audio_readable.sh`, idempotent, runs as root, honours `FRIDAY_OTA_ROOT` and `FRIDAY_RELEASE_DIR` like 0005.
- Guard log line (info): `player corrigido: <bad> -> <own>`. Unavailable result exactly: `{"result": {"ok": false, "reason": "player_unavailable", "players": [<ids>]}}`. HA state read timeout: 1 s; on any read failure, pass the call through unchanged.
- OpenClaw key: `tools.codeMode.enabled` = `false`.
- Promotion: tracked = oldest eligible pre-release above newest stable, not decided, not failed (blocked included). Promoting it marks every older undecided alpha `superseded`.
- `voice_gateway` tests must not import pipecat on the tested path (CI has no pipecat): put the pure logic in a module of its own. See memory pitfall: check every CI job via `gh pr checks N --json name,bucket`.
- Every `scripts/test.sh` / `test-all.sh` run uses `FRIDAY_TEST_DIR=ha-friday-music`. Temp files never under `/tmp` directly.
- Git messages in English; never `--no-verify`. Comments in English in `agents/`, `custom_components/`, `satellite/audio`, `voice_gateway` new module.
- Release is **v0.4.4** (with PR #51): the controller updates `docs/release-notes/v0.4.4.md` after #51 merges; no manifest change here.

## Review Focus
- A rollback to v0.4.3 after 0008 ran: old `/etc/friday-satelite/asound/*.conf` still exist, so the old link target works — Task 1 `test_0008_keeps_old_files`.
- A satellite with the XVF3800 board: link points to `/etc/friday-audio/xvf3800.conf`, not jabra — Task 1 `test_0008_keeps_board`.
- A request naming another existing room's player (`media_player.escritorio_3`) is never rewritten — Task 2 `test_existing_other_player_untouched`.
- HA slow/down during the guard: call goes through unchanged within ~1 s — Task 2 `test_state_read_failure_passes_through`.
- v0.4.3 failed, v0.4.4 soaked: v0.4.4 becomes tracked and is promoted; v0.4.3 ends superseded — Task 4 `test_failed_oldest_is_skipped_then_superseded`.

---

### Task 1: ALSA board files readable by the music player

**Files:**
- Modify: `satellite/audio/friday-audio-select` (`apply()`: `target = f"/etc/friday-audio/{...}.conf"`)
- Modify: `agents/satellite/friday-ota-apply:59,71` (install map and snapshot list → `etc/friday-audio/…`, dir 0755)
- Modify: `satellite/image/customize-chroot.sh:33-35,78-80`, `satellite/image/verify_image.sh:28` (new paths; verify also `stat -c %a` of `/etc/friday-audio` = 755)
- Create: `satellite/migrations/0008_audio_readable.sh`
- Test: `tests/agents/test_audio_select.py` (update the 0004-era expectation at line 81), `tests/agents/test_migration_0008.py` (pattern of `test_migration_0005.py`), any `test_ota_apply*` that asserts the old path

**Interfaces:** Produces the on-device layout in Global Constraints. 0008: `mkdir -p $R/etc/friday-audio && chmod 0755`; install `jabra.conf`, `xvf3800.conf` from `$FRIDAY_RELEASE_DIR/system` (0644) and `friday-audio-select` (0755, `usr/local/sbin`); run `$R/usr/local/sbin/friday-audio-select` with root `$R` only when `R` is empty (real device), else repoint the link itself from the current `etc/friday-satelite/audio_board` (missing/`none` → jabra); when `R` is empty, `systemctl restart sendspin-cli` (ignore failure).

- [ ] **Step 1: Failing tests:** `test_apply_points_to_friday_audio` (`apply("jabra", root)` → link `/etc/friday-audio/jabra.conf`); `test_0008_installs_readable_dir` (dir mode 0o755, files 0o644); `test_0008_repoints_link_jabra`; `test_0008_keeps_board` (audio_board `xvf3800` → link `/etc/friday-audio/xvf3800.conf`); `test_0008_keeps_old_files` (pre-existing `etc/friday-satelite/asound/jabra.conf` unchanged); `test_0008_idempotent` (second run exit 0, same result); `test_0008_missing_source_fails` (exit ≠ 0, message names the file); applier test: `jabra.conf` installed under `etc/friday-audio`.
- [ ] **Step 2: Run** `FRIDAY_TEST_DIR=ha-friday-music bash scripts/test.sh tests/agents/test_audio_select.py tests/agents/test_migration_0008.py` — Expected: new tests FAIL.
- [ ] **Step 3: Implement** as in Interfaces. `grep -rn "friday-satelite/asound" agents satellite tests` afterwards must list only migrations 0004/0005 and their tests.
- [ ] **Step 4: Run** the same plus `tests/agents/test_ota_packages.py tests/agents/test_migration_0005.py` and the applier tests — Expected: PASS. Commit `fix(satellite): keep ALSA board files readable by the music player`.

### Task 2: bot corrects the music player

**Files:**
- Create: `voice_gateway/player_guard.py` (pure, no pipecat import)
- Modify: `voice_gateway/bot.py:826` (use the subclass for satellite sessions with a catalog player)
- Test: `voice_gateway/test_player_guard.py`

**Interfaces:**
- `async def guard(tool: str, args: dict, own: str | None, read_state) -> tuple[dict, dict | None]` — returns `(new_args, early_result)`. `read_state(entity_id) -> str | None` (None = entity does not exist; raises on failure). Applies only when `tool.startswith("script__music_")` and `own` is set; keys `players` (list), `player` (str), `to_players` (str or list). Missing key: `script__music_play` gets `players=[own]`; scripts whose args already use `player` and lack it get `player=own`. `early_result` per Global Constraints when every resulting player reads `unavailable`.
- `async def read_state_http(entity_id, ha_url, token, timeout_s=1.0) -> str | None` — GET `/api/states/<id>`; 404 → None; other non-200 or timeout → raise.
- In `bot.py`, `class GuardedMCPClient(MCPClient)` (defined in bot.py, since it needs pipecat) overrides `async def _call_tool_text(self, tool_name, arguments)`: calls `guard`; on exception from `read_state` passes through; on `early_result` returns `json.dumps(early_result)`; else calls `super()._call_tool_text(tool_name, new_args)`. `own` = `(await _cadastro()).get(satelite_id, {}).get("player")` for satellite sessions (pass it into the tools builder; non-satellite sessions keep plain `MCPClient`). Before writing the override, confirm the signature of `MCPClient._call_tool_text` in the installed pipecat on the VM (`sudo docker exec friday python -c "import inspect, pipecat.services.mcp_service as m; print(inspect.signature(m.MCPClient._call_tool_text))"`) and adapt.

- [ ] **Step 1: Failing tests** (`unittest`, fake `read_state`): `test_made_up_player_replaced` (`players=["media_player.f_r_i_d_a_y_office"]`, own `media_player.escritorio` → `["media_player.escritorio"]`, log line asserted with `assertLogs` or captured logger); `test_existing_other_player_untouched` (`media_player.escritorio_3` exists → unchanged); `test_duplicates_dropped` (bad + own → `[own]`); `test_missing_players_on_play_gets_own`; `test_single_player_script_fixed` (`script__music_seek`, `player` bad → own); `test_unavailable_returns_early` (own state `unavailable` → early result exact dict); `test_state_read_failure_passes_through` (read_state raises → args unchanged, no early result — in bot this is caught; in `guard` assert it propagates, and test the bot wrapper's pass-through with a tiny fake subclass only if it can be done without pipecat, else cover in `guard` via a `safe_guard` helper in `player_guard.py` that catches and returns `(args, None)` — **use `safe_guard` from the bot**); `test_non_music_tool_untouched`; `test_no_own_player_untouched`.
- [ ] **Step 2: Run** `cd voice_gateway && FRIDAY_LOCAL_CONFIG=local.example.env python -m unittest -q test_player_guard` — Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `player_guard.py`, then the bot wiring.
- [ ] **Step 4: Run** the same, then the CI-like run from memory `pitfall_ci_verde_falso` (VM, `python:3.14-slim`, no pipecat, whole `python -m unittest -q`) — Expected: PASS. Commit `fix(voice): correct a made-up music player to this satellite's`.

### Task 3: brain never uses Code Mode

**Files:**
- Modify: `server/image/run.sh` (next to line 84–87: `openclaw config set tools.codeMode.enabled false >/dev/null 2>&1 || echo "codeMode: config set failed" >&2`)
- Test: the existing run.sh test that asserts `openclaw config set` lines (`grep -rn "chatCompletions" tests server/image` to find it); add `test_run_disables_code_mode`.

- [ ] **Step 1:** Failing test asserting the exact line is present and ordered after the gateway config block.
- [ ] **Step 2: Run** it — Expected: FAIL.
- [ ] **Step 3:** Add the line. Confirm `run.sh` ships with the server OTA package (`grep -n "run.sh" agents/server/friday_server_agent/ota/packages.py ota/release.json`); if it does not, add it to the declared server files and say so in the report.
- [ ] **Step 4: Run** — Expected: PASS. Commit `fix(server): turn off OpenClaw Code Mode for the brain`.

### Task 4: promotion follows the next alpha

**Files:**
- Modify: `agents/server/friday_server_agent/ota/orchestrator.py` (`_promote_step` ~313, `alpha_promotion` ~589, `tracked_promotion_tag` ~662; docstrings)
- Modify: `docs/superpowers/specs/2026-11-22-block-alpha-promotion-design.md` (one line: superseded by the 23/11 spec)
- Test: `tests/agents/test_ota_orchestrator.py`, `tests/agents/test_server_alpha_promotion.py`

**Interfaces:** `alpha_promotion()` rows ordered **oldest first**; `attrs["tag"]` = first row whose status ≠ `failed` (blocked included), falling back to the last decided tag as today. `tracked_promotion_tag()` returns that same tag. `_promote_step`: the only promotion candidate is the tracked tag, and only when soaked and not blocked/failed; after it is promoted on GitHub, every older undecided eligible pre-release is recorded `{"result": "superseded"}`. No other alpha is superseded by the selection itself.

- [ ] **Step 1: Failing tests:** rewrite `test_two_soaked_prereleases_promote_only_the_newest` → `test_two_soaked_prereleases_promote_the_oldest_first` (v0.4.3 and v0.4.4 soaked → v0.4.3 promoted; next cycle v0.4.4 promoted; GitHub promote calls in that order); `test_newer_waits_while_next_is_soaking` (v0.4.3 soaking 10 h, v0.4.4 soaked → nothing promoted); `test_blocked_next_holds_the_queue` (v0.4.3 blocked, v0.4.4 soaked → nothing promoted; tracked = v0.4.3); `test_failed_oldest_is_skipped_then_superseded` (v0.4.3 failed → tracked v0.4.4, promoted when soaked, v0.4.3 `superseded` after); `test_switch_tracks_next_alpha` (replaces `test_switch_tracks_newest_promotable_when_newest_failed`); adapt `test_set_promotion_default_tag_is_tracked`, `test_alpha_promotion_newer_stable_after_supersede` and `test_server_alpha_promotion.py` expectations to oldest-first.
- [ ] **Step 2: Run** `FRIDAY_TEST_DIR=ha-friday-music bash scripts/test.sh tests/agents/test_ota_orchestrator.py tests/agents/test_server_alpha_promotion.py` — Expected: new/changed tests FAIL.
- [ ] **Step 3: Implement.** Keep the block/failure re-check right before `publisher.promote` and the `PROMOTE_RETRY_S` throttle.
- [ ] **Step 4: Run** — Expected: PASS. Commit `feat(ota): promote the next alpha, not the newest`.

### Task 5: full check
- [ ] **Step 1: Run** `FRIDAY_TEST_DIR=ha-friday-music bash scripts/test-all.sh` — Expected: every step passes (lone `test_provisioning` timeout = known flake, rerun that step).
- [ ] **Step 2:** `git push -u origin fix/mk1-music-promotion`. The controller opens the PR (ready), checks every CI job by JSON, and updates the v0.4.4 notes after #51 merges.

Task order: 1, 2, 3, 4, 5 (independent).
