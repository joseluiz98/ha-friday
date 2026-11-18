# OTA package contract, server-first gate and whole rollback (v0.3.10) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:**
- A server agent of any version packs a newer release from that release's own declaration.
- Satellites only get a package built by a server at least as new as the release.
- A failed satellite activation restores the system files it touched.

**Architecture:**
- **Packager:** `ota/release.json` gains `package.satellite` (`paths`, `system`). `packages.py` reads it from the release, falls back to built-in lists, and fails on a declared file that is missing.
- **Orchestrator:** it gates satellite offers on `server_ota.installed_tag`, records `built_by` and an `offered` marker per package, and refuses or rebuilds stale satellite packages.
- **Satellite apply:** `friday-ota-apply` snapshots the system destinations before migrations and restores them on any way back.

**Tech Stack:** Python 3.11+ (stdlib only in `friday-ota-apply`, which runs on the Pi), bash migrations, pytest.

**Spec:** `docs/superpowers/specs/2026-11-18-ota-package-contract-design.md` (read it first; it is the source of every rule below).

**Execution (owner's choice, 18/11):**
- Tasks 1, 2 and 5 are simple: a Sonnet agent each.
- Tasks 3 and 4 touch state and rollback logic: an Opus agent each.
- No intermediate review. One Opus reviewer reads the whole branch at the end (Task 6).
- Order: 1 → 2 → 3 → 4 → 5 → 6, all on branch `fix/ota-package-contract` (already created from `origin/master`, spec committed).

## Global Constraints

- **Repo:** `/config/dev/ha-friday`, branch `fix/ota-package-contract`.
  - Never push.
  - Never touch the VM, any satellite, or production files.
  - Never use `--no-verify`. The pre-commit sweep rejects private names and ids, so write "the owner" and never satellite ids.
- **Tests:** full suite = `cd /config/dev/ha-friday && FRIDAY_LOCAL_CONFIG=voice_gateway/local.example.env bash scripts/test-all.sh`, which must end ALL GREEN. Per-file runs use `python3 -m pytest <file> -q`.
- **Commits:** English, one per task. End every message with the two trailer lines (Co-Authored-By, Claude-Session) given in your task prompt.
- **`agents/satellite/friday-ota-apply`:** stdlib only. Every path goes under `R` (`FRIDAY_OTA_ROOT`) so the fake-root tests work.
- **Version tags** compare with `friday_agent_core.ota.channels.version_key` on the server side and `_vkey` in the apply.
- **Migrations** stay forward-only: never rename or renumber 0004.

## Review Focus

1. **Pre-contract release** (no `package` key in `release.json`, e.g. a rollback target v0.3.8): it must still pack with the built-in lists. Pinned in Task 1 `test_release_without_contract_uses_builtin_lists`.
2. **Package already in a device's hands:** a package with no `built_by`, built by the v0.3.9 agent and installed on a satellite, must never be rebuilt. Pinned in Task 3 `test_installed_package_without_built_by_is_kept`.
3. **Server stuck behind for days:** one push after 1 h, then at most one per 24 h, never one per poll. Pinned in Task 3 `test_waiting_server_push_once_per_day`.
4. **Activation done by an older apply,** so no snapshot exists. A way back must work exactly as today, with no exception. Pinned in Task 4 `test_rollback_without_snapshot_behaves_as_before`.
5. **New release ships a helper the previous one also ships:** the restore puts back the old bytes and never deletes it. Pinned in Task 4 `test_rollback_restores_overwritten_and_removes_created`.

---

### Task 1: Package contract in `release.json` and the packager (Sonnet)

**Files:**
- Modify: `ota/release.json`
- Modify: `agents/server/friday_server_agent/ota/packages.py` (`SATELLITE_PATHS`, `SYSTEM_SOURCES`, `pack_satellite`, `build_satellite`)
- Test: `tests/agents/test_ota_packages.py`

**Interfaces:**
- Produces:
  - `packages.declared(release: dict) -> tuple[list[str], list[str]]`: returns `(paths, system)` from `release["package"]["satellite"]`, or `(SATELLITE_PATHS, SYSTEM_SOURCES)` when the key is absent. Built-in required paths are always merged in: `REQUIRED_MODERN`, `ota/release.json`, `vault/manifest.json`. The result keeps order and has no duplicates.
  - `build_satellite(...)`, unchanged signature. It now archives twice: first `archive(sha, ["ota/release.json"])` to read the declaration, then `archive(sha, paths)`.
  - `pack_satellite` copies into `system/` every file of every `system` entry: a file path as is, a folder only for names ending in `SYSTEM_SUFFIXES`, as today.
  - A declared `system` entry absent from the tree raises `pv.ProvisioningError("source_missing")` before anything is written.

- [ ] **Step 1: Add the contract to `ota/release.json`.**
  - Add `"package": {"satellite": {"paths": [...], "system": [...]}}`.
  - `paths` = today's `SATELLITE_PATHS`; `system` = today's `SYSTEM_SOURCES`, both copied verbatim from `packages.py`.
  - Above the two lists in `packages.py`, add the comment `# fallback for releases without package.satellite in ota/release.json (the release is the source)`.

- [ ] **Step 2: Write the failing tests in `tests/agents/test_ota_packages.py`.** Extend `_extra_tree` so its `release.json` has a `package.satellite` with `system` listing `satellite/audio/jabra.conf` and `satellite/audio/xvf3800.conf` plus the existing entries. Then add:

```python
def test_declared_system_files_are_packed(src): ...
    # build_satellite → system/jabra.conf and system/xvf3800.conf in the tar

def test_old_builtin_lists_still_pack_the_declared_audio(src, monkeypatch):
    monkeypatch.setattr(pk, "SATELLITE_PATHS", [p for p in pk.SATELLITE_PATHS if p != "satellite/audio"])
    monkeypatch.setattr(pk, "SYSTEM_SOURCES", [p for p in pk.SYSTEM_SOURCES if not p.startswith("satellite/audio")])
    # build_satellite → system/jabra.conf still present (the v0.3.8 packager case)

def test_declared_file_missing_fails_and_writes_nothing(src): ...
    # declare satellite/audio/missing.conf → ProvisioningError("source_missing"); no satellite.tar.gz in out dir

def test_release_without_contract_uses_builtin_lists(src): ...
    # release.json without "package" → package builds, system/ has the built-in entries

def test_builtin_lists_equal_the_repo_contract():
    # the real repo ota/release.json package.satellite == (SATELLITE_PATHS, SYSTEM_SOURCES)

def test_apply_system_names_are_declared():
    # every key of the SYSTEM map in agents/satellite/friday-ota-apply that is a plain file name
    # (not a ".suffix" rule) and exists somewhere under satellite/ or agents/satellite/ in the repo
    # is the basename of a file covered by package.satellite.system in the repo release.json
```

- [ ] **Step 3: Run** `python3 -m pytest tests/agents/test_ota_packages.py -q`. Expected: the new tests FAIL.
- [ ] **Step 4: Implement `declared()` and the two-pass archive in `packages.py`.**
- [ ] **Step 5: Run** the file again and then the full suite. Expected: PASS, ALL GREEN.
- [ ] **Step 6: Commit** `fix(ota): the release declares its satellite package; missing declared files fail the build`.

### Task 2: Migrations fail when they cannot do their job (Sonnet)

**Files:**
- Modify: `satellite/migrations/0004_audio_select.sh`
- Create: `satellite/migrations/0005_audio_files.sh`
- Test: `tests/agents/test_migration_0004.py`, create `tests/agents/test_migration_0005.py`

**Interfaces:**
- Produces:
  - **0004:** `inst` returns 1 when its source is missing and `FRIDAY_RELEASE_DIR` is set, and the script then exits 1 with `0004: missing <name> in the release system files` on stderr.
  - **0005:** same environment (`FRIDAY_OTA_ROOT`, `FRIDAY_RELEASE_DIR`). It installs the same six files with the same modes and destinations as 0004 (see `SYSTEM_FILES` in `test_migration_0004.py`), idempotently.
    - It exits 1 with the same message format if any is missing.
    - Without `FRIDAY_RELEASE_DIR` it exits 1 (`0005: FRIDAY_RELEASE_DIR not set`).
    - It ends with `udevadm control --reload-rules`, only when `R` is empty, as 0004 does.

- [ ] **Step 1: Write the failing tests.**
  - `test_missing_source_fails` in `test_migration_0004.py`: release dir with only `jabra.conf` → returncode 1, `missing` in stderr.
  - In `test_migration_0005.py` (copy the `SYSTEM_FILES` map):
    - `test_installs_all_audio_files_with_modes`;
    - `test_idempotent`;
    - `test_missing_source_fails`;
    - `test_without_release_dir_fails`.
- [ ] **Step 2: Run** both files. Expected: the new tests FAIL.
- [ ] **Step 3: Implement both scripts.** Start 0005 with a comment block in the style of 0004: why it exists (0004 was recorded as done with nothing installed on satellites updated by the v0.3.8 packager, 18/11).
- [ ] **Step 4: Run** both files and the full suite. Expected: PASS, ALL GREEN. Also check that `tests/agents/test_ota_migrations.py` still passes, since discovery accepts `0005_audio_files.sh`.
- [ ] **Step 5: Commit** `fix(satellite): audio migrations fail when the release lacks the files; 0005 reinstalls them`.

### Task 3: Server-first gate, `built_by`, `offered` marker, waiting push (Opus)

**Files:**
- Modify: `agents/server/friday_server_agent/ota/packages.py` (`build_release`)
- Modify: `agents/server/friday_server_agent/ota/orchestrator.py` (`__init__`, `_package`, `offer`, `install`, `package_for_update`, `package_file`, `image_build` path)
- Modify: `agents/server/friday_server_agent/ota/notify.py`
- Test: `tests/agents/test_ota_orchestrator.py`, `tests/agents/test_ota_packages.py`, `tests/agents/test_ota_notify.py`

**Interfaces:**
- Consumes: Task 1's `build_release` (same signature).
- Produces:
  - **`build_release`** writes `"built_by": <tag>` into `meta.json`. The tag comes from a new keyword argument `built_by: str = ""`, and the orchestrator passes its own version.
  - **`packages.mark_offered(root: str, tag: str) -> None`** creates `<root>/packages/<tag>/.offered`. **`packages.is_offered(root, tag) -> bool`** checks it.
  - **`Orchestrator.__init__(..., own_version: Callable[[], str] = version.installed_version)`**, from `friday_agent_core.version`.
  - **`Orchestrator._satellite_package(tag) -> dict | None`** is the only door for satellite consumers (offer, `package_file` for `satellite.tar.gz`/`wheels.tar.gz`, `package_for_update`, image build). It calls `_package(tag)`, then:
    - `built_by` missing or older than `tag` (`version_key`), and the package is "held": return meta unchanged. "Held" means `is_offered`, or a device with `installed == tag`, or the store events have an `installed`/`rolled_back`/`failed` event for that tag.
    - `built_by` missing or older than `tag`, not held, and own version older than `tag`: return `None`. Offer reason `"server_older"`; the image build refuses with `server_older`.
    - `built_by` missing or older than `tag`, not held, and own version ≥ `tag`: remove `<root>/packages/<tag>/`, drop `self.meta[tag]`, rebuild once.
    - The server step keeps calling `_package(tag)` directly (it needs `archive.tar` even from an older agent).
  - **`offer()` order** after the existing checks:
    1. protocol gate;
    2. **server gate:** `server_ota.installed_tag` missing or `version_key` < tag → `offer_payload(None, ..., reason="waiting_server")`;
    3. retry gate;
    4. `_satellite_package`;
    5. `mark_offered` just before returning a tag.
  - **Waiting push:**
    - On a `waiting_server` answer, set `device["waiting_server_since"]` if unset. Clear it on any other answer.
    - When `now - since >= 3600` and `now - store.data.get("waiting_server_push_at", 0) >= 86400`, queue `nt.format_waiting_server(sat_id, tag, why)` and set `waiting_server_push_at = now`.
    - `why` comes from the server state, first match: `"atualização automática desligada"` (auto off and no request), `"build de imagem em andamento"` (images building), `"tentativas esgotadas"` (server attempts exhausted), else `"aguardando a janela de instalação"`.
  - **`nt.format_waiting_server(device, tag, why) -> str`**: `f"F.R.I.D.A.Y.: {device} está esperando o servidor instalar a {tag} para atualizar ({why})."`
  - **`install(sat_id)`**: when the server's `installed_tag` is older than the satellite's target, it also sets `server_ota.install_requested = True`, `attempts=None`, and logs `self.store.event("server", tag, "install_requested")`.

- [ ] **Step 1: Adapt the test `World` so existing tests keep their meaning.**
  - By default it sets `store.data["server_ota"]["installed_tag"]` to the newest release tag and `own_version=lambda: "v99.0.0"`.
  - The fake build puts `built_by` in its meta.
  - Add helpers `server_at(tag)` and `agent_at(tag)`.
  - Run the file: the existing tests still PASS.
- [ ] **Step 2: Write the failing tests in `test_ota_orchestrator.py`:**
  - `test_offer_waits_for_server_install`: server at v0.3.0, target v0.4.0 → reason `waiting_server`; after `server_at("v0.4.0")` → tag v0.4.0.
  - `test_offer_marks_package_offered`
  - `test_stale_package_rebuilt_when_not_held`: `built_by` v0.3.0 for tag v0.4.0, own v0.4.0 → build called twice, new meta `built_by` v0.4.0.
  - `test_installed_package_without_built_by_is_kept`: meta without `built_by`, a device `installed == tag`, own newer → no rebuild, same sha256.
  - `test_older_agent_refuses_satellite_package`: own v0.3.0, tag v0.4.0, not held → offer reason `server_older`; `image_build("v0.4.0")` refused with `server_older`; `package_for_update()` returns `None`.
  - `test_server_step_still_builds_archive_with_older_agent`
  - `test_manual_satellite_install_requests_server_install`
  - `test_waiting_server_push_once_per_day`: clock +59 min → no push; +61 min → one push; +2 h → still one; +25 h → two.
  - In `test_ota_notify.py`: `test_format_waiting_server` asserts the exact string above.
  - In `test_ota_packages.py`: `test_build_release_records_built_by` and `test_mark_offered_roundtrip`.
- [ ] **Step 3: Run** the three files. Expected: the new tests FAIL.
- [ ] **Step 4: Implement** in `packages.py`, `notify.py` and `orchestrator.py` per the Interfaces block.
- [ ] **Step 5: Run** the files and the full suite. Expected: PASS, ALL GREEN.
- [ ] **Step 6: Commit** `fix(ota): satellites wait for the server; packages carry who built them and when they were offered`.

### Task 4: Whole rollback on the satellite (Opus)

**Files:**
- Modify: `agents/satellite/friday-ota-apply` (`_install_system`, `activate`, `_back`, `recover`, `prune`)
- Test: `tests/agents/test_ota_apply.py`

**Interfaces:**
- Produces (all inside `friday-ota-apply`):
  - **`SNAPSHOTS = f"{R}/var/lib/friday/ota/system-backup"`**
  - **`_system_plan(release) -> list[tuple[str, str, int]]`** returns `(src, dest, mode)` for every file `_install_system` would write, sudoers included. `_install_system` is refactored to iterate it, with behavior unchanged: the visudo check still runs first.
  - **`_snapshot(tag, dests: list[str]) -> None`** writes `SNAPSHOTS/<tag>/`:
    - each existing dest is copied with `shutil.copy2` under `files/<dest without R and leading slash>`;
    - `snapshot.json` = `{"saved": [dest...], "created": [dest...]}`, with dests stored relative to `R`;
    - any older snapshot folder is replaced.
  - **`_restore(tag) -> bool`:**
    - if `SNAPSHOTS/<tag>/snapshot.json` is missing, it returns False and changes nothing;
    - otherwise it copies every saved file back (mode kept), removes every created file that still exists, runs `systemctl daemon-reload` and `udevadm control --reload-rules` (the latter only when `R` is empty), and returns True.
  - **`activate`** calls `_snapshot(tag, [d for _, d, _ in _system_plan(release)] + AUDIO_DESTS)` right after `write_state(..., "activating")` and before `_migrations`.
    - `AUDIO_DESTS` = the six 0005 destinations under `R`, so files created only by a migration are covered too.
  - **`_back(tag, previous, stage, why)`** calls `_restore(tag)` before `_switch(previous)`. The rest is unchanged.
  - **`recover`**: in the "cut before the switch" branch it calls `_restore(st["tag"])` before writing the `interrupted` state.
  - **`prune`** removes every `SNAPSHOTS/<t>` with `t != current_tag()`.

- [ ] **Step 1: Write the failing tests in `test_ota_apply.py`**, using the `env`, `package(system=...)` and `staged` helpers. Add a fake `udevadm` to the fake bin.
  - `test_rollback_restores_overwritten_and_removes_created`:
    - the installed v0.1.0 has `/etc/systemd/system/friday-satelite.service` = `"old"` and `/usr/local/sbin/friday-agent-apply` = `"old-apply"`;
    - v0.2.0 ships both changed plus a new `friday-audio-select`;
    - with health failing (`Health.status = {}`), after activate the unit and the apply are byte-equal to `"old"`/`"old-apply"`, `friday-audio-select` does not exist, and `daemon-reload` is in the calls log after the restore.
  - `test_rollback_removes_files_created_by_a_migration`: a migration writes `usr/local/sbin/friday-audio-select` → after the rollback it is gone.
  - `test_rollback_without_snapshot_behaves_as_before`: remove the snapshot folder before the health failure → state `rolled_back`, `current` = previous, no traceback in stderr.
  - `test_interrupted_before_switch_restores_snapshot`
  - `test_prune_keeps_only_current_snapshot`
- [ ] **Step 2: Run** `python3 -m pytest tests/agents/test_ota_apply.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** per the Interfaces block.
- [ ] **Step 4: Run** the file and the full suite. Expected: PASS, ALL GREEN, and all existing apply tests unchanged.
- [ ] **Step 5: Commit** `fix(satellite): a failed activation restores the system files it touched`.

### Task 5: Version bump and release-notes draft (Sonnet)

**Files:**
- Modify: `custom_components/friday/manifest.json` (`"version": "0.3.10"`)
- Create: `docs/release-notes/v0.3.10.md`. The owner's session publishes it on GitHub and adds the warning line to the v0.3.9 notes; agents never touch GitHub.

**Interfaces:** none (text only).

- [ ] **Step 1: Bump the manifest.** Run the full suite (some tests read the manifest version). Expected: ALL GREEN.
- [ ] **Step 2: Write `docs/release-notes/v0.3.10.md`.**
  - **Format:** the same as the v0.3.8 notes (`gh release view v0.3.8`). Sections: title line `# F.R.I.D.A.Y. 0.3.10: …`, a short intro with 3–4 bullets, the pre-release (alpha) line, Highlights, Changes by area, Breaking changes / Compatibility, Live checks (copy the spec's list), All changes.
  - **What it carries:** everything from v0.3.9 (#19–#22, see `gh release view v0.3.9`) plus this fix.
  - **Compatibility** states:
    - v0.3.9 is withdrawn and must not be installed;
    - a satellite rolled back from v0.3.9 heals by updating to v0.3.10;
    - the integration still needs one manual install if it is on 0.3.5;
    - gate B and `built_by` only act from the next release on, because the first rollout is packed by the v0.3.9 agent.
- [ ] **Step 3: Commit** `chore(release): bump to v0.3.10 and draft its notes`.

### Task 6: Whole-branch review (Opus)

- [ ] **Step 1:** Read the spec, this plan and `git diff origin/master...fix/ota-package-contract`.
- [ ] **Step 2: Check** in the code:
  - every spec rule and every Review Focus line;
  - the first-rollout path in the spec: a v0.3.9 agent packs v0.3.10, then the v0.3.10 agent must keep that package;
  - that no change can make an existing satellite on v0.3.8 fail to install v0.3.10 with its own v0.3.8 apply (MK1 has the v0.3.9 apply; a card on the v0.3.8 image has the v0.3.8 apply).
- [ ] **Step 3: Run** the full suite.
- [ ] **Step 4: Report** findings ranked by severity, with file:line. Fix nothing.
