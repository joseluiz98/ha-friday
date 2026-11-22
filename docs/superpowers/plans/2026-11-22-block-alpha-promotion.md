# Block Alpha Promotion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The owner blocks or allows the promotion of an alpha from Home Assistant (switch "Alpha promotion" on the Server device, action `friday.set_alpha_promotion`); a block survives restarts and is separate from automatic soak failures.

**Architecture:** the OTA store gains a per-tag `blocked` flag; `channels.promoted()` honours it; the orchestrator exposes `set_promotion(tag, allowed)` and reports `blocked` in `alpha_promotion()`; the server agent publishes a switch value `ota_alpha_promotion_allowed` (None when nothing to allow) and handles it in `set_dispatch`; the integration adds the action, which sends a new server command `alpha_promotion`.

**Tech Stack:** Python (server agent, Home Assistant custom integration), pytest via `scripts/test.sh`.

**Spec:** `docs/superpowers/specs/2026-11-22-block-alpha-promotion-design.md`

## Global Constraints
- Store: `soak.<tag>.blocked: bool` (default False when missing; existing state files load unchanged). `failed` is never cleared by this feature.
- `promoted(r, soak, now)`: pre-release needs `started_at` set, `not failed`, `not blocked`, soak elapsed (`SOAK_S`, unchanged).
- `SoakRecord` gains `blocked: bool = False` (keep positional compatibility: `SoakRecord(started_at, failed, blocked=False)`).
- Sensor status values: `failed` wins over `blocked`; then `waiting_install` / `ready` / `soaking`. A blocked tag's `promotes_at` is None.
- Switch: key `ota_alpha_promotion_allowed`, name `"Alpha promotion"`, `platform="switch"`, `category="config"`, icon `mdi:rocket-launch`. Value: True = tracked alpha allowed, False = blocked, None (→ `unavailable`) when there is no tracked alpha or it is `failed`.
- Server command for the action: `{"cmd": "alpha_promotion", "tag": <tag or absent>, "allowed": bool}` → `{"ok": True, "tag": ...}` or `{"ok": False, "error": "no_alpha" | "unknown_tag" | "failed" | "promoted" | "value"}`. Blocking a `failed` tag is allowed (sets the flag); allowing a `failed` tag returns `error: "failed"`.
- Events: `store.event("server", tag, "promotion_blocked" | "promotion_allowed")`; integration logbook entry via `push_log("Alpha promotion of <tag> blocked/allowed by the owner", {"tag": tag})`.
- Action: `friday.set_alpha_promotion` (`tag`: optional string `0.4.3`/`v0.4.3`, normalized with `services.normalize_version`; `allowed`: required boolean). Errors map to `ServiceValidationError` with English messages. Add it to `services.yaml` and `strings.json`/`translations/en.json`.
- Every test run uses `FRIDAY_TEST_DIR=ha-friday-block`. Temp files never under `/tmp` directly. Git messages in English; never `--no-verify`; no house values in the repo.

## Review Focus
- Existing state file with only `started_at`/`failed` (as on the VM today): loads, nothing blocked — Task 1 `test_store_old_soak_rows_unblocked`.
- Allowing never clears `failed` (the 22/11 manual stop of v0.4.2 stays) — Task 1 `test_allow_keeps_failed`.
- Blocked past 48 h, then allowed: promoted on the next `_promote_step` — Task 1 `test_allow_after_soak_promotes_next_step`.
- The switch shows `unavailable` (None) for a failed tracked alpha, never True — Task 2 `test_switch_value_none_when_failed`.
- A block survives a server agent restart (new store instance on the same file) — Task 1 `test_block_persists`.

---

### Task 1: store, channels and orchestrator

**Files:**
- Modify: `agents/common/friday_agent_core/ota/channels.py` (`SoakRecord`, `promoted`)
- Modify: `agents/server/friday_server_agent/ota/store.py` (`soak()` reads `blocked`; `soak_block(tag, blocked: bool)`)
- Modify: `agents/server/friday_server_agent/ota/orchestrator.py` (`set_promotion(tag: str | None, allowed: bool) -> dict`, `alpha_promotion()` status, `promotion_allowed() -> bool | None`)
- Test: `tests/agents/test_ota_channels.py`, `tests/agents/test_ota_orchestrator.py`

**Interfaces:** Produces `Orchestrator.set_promotion(tag, allowed) -> dict` (the command reply shape above, `tag` resolved to the tracked alpha when None; saves the store; writes the event) and `Orchestrator.promotion_allowed() -> bool | None` (tracked alpha of `alpha_promotion()`; None when none or failed).

- [ ] **Step 1: Failing tests:** channels `test_promoted_false_when_blocked`; orchestrator `test_store_old_soak_rows_unblocked`, `test_block_stops_promotion` (soaked 49 h + blocked → `_promote_step` does not call the publisher), `test_allow_after_soak_promotes_next_step`, `test_allow_keeps_failed` (failed tag: allow → `error: "failed"`, `failed` still True), `test_block_persists`, `test_set_promotion_default_tag_is_tracked`, `test_set_promotion_unknown_tag`, `test_set_promotion_already_promoted`, `test_alpha_promotion_status_blocked` (status `blocked`, `promotes_at` None, row too), `test_promotion_allowed_values` (True / False / None for failed / None with no alpha), `test_block_writes_event`.
- [ ] **Step 2: Run** `FRIDAY_TEST_DIR=ha-friday-block bash scripts/test.sh tests/agents/test_ota_channels.py tests/agents/test_ota_orchestrator.py` — Expected: new tests FAIL.
- [ ] **Step 3: Implement** under `self.store.lock`, save with the store's existing save path.
- [ ] **Step 4: Run** — Expected: PASS. Commit `feat(ota): block an alpha's promotion by hand`.

### Task 2: server agent switch and command

**Files:**
- Modify: `agents/server/friday_server_agent/collectors.py` (descriptor next to `ota_auto_install`, line ~249)
- Modify: `agents/server/friday_server_agent/__main__.py` (`ota_entities` adds `ota_alpha_promotion_allowed`; `set_dispatch` handles the key; new handler `"alpha_promotion"`; both push state via `ota_state_push()` and log via `core.push_log`)
- Test: `tests/agents/test_server_collectors.py`, `tests/agents/test_server_main.py` (or the file that covers `ota_entities` / `set_dispatch`; create `tests/agents/test_server_alpha_promotion.py` if none fits)

- [ ] **Step 1: Failing tests:** `test_descriptor_alpha_promotion_switch` (exact key/name/platform/category/icon); `test_ota_entities_has_allowed`; `test_switch_value_none_when_failed`; `test_set_dispatch_alpha_promotion` (value False → `set_promotion(None, False)`; non-bool → `error: "value"`); `test_alpha_promotion_command_passes_tag`.
- [ ] **Step 2: Run** the test files — Expected: new tests FAIL.
- [ ] **Step 3: Implement.** The switch acts on the tracked alpha (`tag=None`).
- [ ] **Step 4: Run** — Expected: PASS. Commit `feat(server-agent): Alpha promotion switch`.

### Task 3: integration action

**Files:**
- Modify: `custom_components/friday/services.py` (`SERVICE_SET_ALPHA_PROMOTION = "set_alpha_promotion"`, schema, handler using `find_server` and `connection.send("alpha_promotion", ...)`, registration)
- Modify: `custom_components/friday/services.yaml`, `custom_components/friday/strings.json`, `custom_components/friday/translations/en.json`
- Test: `tests/integration/test_set_alpha_promotion_service.py` (new, modeled on `test_build_image_service.py`)

- [ ] **Step 1: Failing tests:** `test_block_sends_command` (`allowed: false`, no tag → `send("alpha_promotion", allowed=False)`); `test_tag_normalized` (`0.4.3` → `tag="v0.4.3"`); `test_error_failed_message` (agent raises `HomeAssistantError("failed")` → `ServiceValidationError` mentioning the automatic failure); `test_server_not_connected`.
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement.** Messages: `no_alpha` "There is no alpha in soak.", `unknown_tag` "Unknown alpha.", `failed` "This alpha failed its soak on its own (rollback or exhausted retries); it cannot be allowed.", `promoted` "This release is already stable."
- [ ] **Step 4: Run** — Expected: PASS. Commit `feat(integration): set_alpha_promotion action`.

### Task 4: full check
- [ ] **Step 1: Run** `FRIDAY_TEST_DIR=ha-friday-block bash scripts/test-all.sh` — Expected: every step passes (lone `test_provisioning` timeout = known flake).
- [ ] **Step 2:** `git push -u origin feat/block-promotion`.
