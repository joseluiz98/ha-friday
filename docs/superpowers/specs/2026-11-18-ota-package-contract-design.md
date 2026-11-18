# OTA fix package (v0.3.10): the release says what it ships, satellites wait for the server, a rollback is whole

Date: 2026-11-18. Owner: the owner. Status: design, awaiting approval.

## Why

The v0.3.9 rollout broke MK1 in three steps, all confirmed in its logs, the VM state and the code.

1. **Wrong package.** The VM's server agent was still v0.3.8 when it built the v0.3.9 satellite package (07:27). Five minutes later it installed itself as v0.3.9 (07:37).
   - The v0.3.8 packager has a hard-coded list of folders (`packages.py`: `SATELLITE_PATHS`, `SYSTEM_SOURCES`), and that list does not know `satellite/audio`.
   - The package therefore shipped the new `friday-satelite.service` but not `friday-audio-select`, `jabra.conf`, `xvf3800.conf`, the udev rule, the sendspin drop-in or `friday-silence.service`. The new unit's `ExecCondition` needs `friday-audio-select`.
   - The list of what each release ships already exists in the release itself (`ota/release.json` → `components.satellite`). The packager never reads it.
2. **Silent migration.** Migration 0004 copies the audio files from `$FRIDAY_RELEASE_DIR/system`. When a file is missing it returns 0 (`inst` → `[ -f ] || return 0`). It was recorded as done with nothing installed, so it will never run again on MK1.
3. **Half rollback.**
   - The new unit could not start the program, so the health check failed ("program not answering") and the satellite rolled back.
   - `_back` only switches `/opt/friday/current` and restarts. The system files the activation installed stay, so the v0.3.9 unit keeps pointing at the missing script and v0.3.8 cannot start either.
   - MK1 is up, with the agent connected, but the voice program is stopped.

**Same cause, second victim.** The v0.3.9 provisioning image was built from that same package and failed: `install: cannot stat '/opt/friday/releases/v0.3.9/system/jabra.conf'`.

## Goals

- A server agent of any version packs a newer release with everything that release declares. A file the release declares and the tree lacks fails the build loudly. It is never shipped incomplete.
- Satellites and the image build only use a package built by a server agent at least as new as the release (the owner's choice C: contract **and** order).
- A satellite rollback restores the system files the failed activation overwrote and removes the ones it created, so the previous version starts as it did before.
- A migration that cannot do its job fails, which refuses the activation and rolls it back.
- v0.3.10 heals MK1 by itself through OTA.

## Non-goals

- Reversible migrations. They stay forward-only (OTA spec §143). 0004 removing the provisional Jabra-only drop-ins is harmless on rollback.
- Changing the health check, retry or soak rules.
- Republishing v0.3.9. It is retired from the channels, and v0.3.10 supersedes it. Its GitHub notes get a short warning line.

## Anti-goals

- A fix that only works when the server already runs the new code. The contract must hold for an **old** server reading a **new** release, which is exactly the failing case.
- A rollback that deletes a file the previous release also ships, or a file it did not create.

## Approaches

The approaches considered, as presented on 18/11:

- **A, contract.** The release declares its package and the packager reads it.
- **B, order.** Satellites wait for the server to install the same tag.
- **C, A + B.**

**The owner chose C** (18/11). A fixes the cause. B is a second barrier: even with a bad contract, the package is built by code that knows the release. It costs about 10 minutes of satellite delay per release.

## Design

### 1. Package contract in `ota/release.json` (A)

- **New key `package.satellite`:**
  - `paths`: the repo paths to archive (today's `SATELLITE_PATHS`);
  - `system`: the repo files or folders whose files go into `system/` (today's `SYSTEM_SOURCES`, including `satellite/audio/*`).
- **Packager (`packages.py`):**
  - It reads `package.satellite` from the release's own `release.json` when the key exists. This needs a two-pass archive: first `ota/release.json` alone, then the declared paths.
  - It falls back to the built-in lists for releases without the key (pre-contract tags, rollback targets).
  - The built-in required set (`REQUIRED_MODERN`, `ota/release.json`, `vault/manifest.json`) is always added on top.
- **Loud failure:** every declared `system` entry must exist in the tree. Otherwise the build raises `ProvisioningError("source_missing")`, and the package is never written.
- **One source of truth:** the built-in lists in `packages.py` are kept only as the fallback, with a comment saying the release is the source.
  - A test asserts that the built-in lists equal the `release.json` of the tree.
  - Another asserts that every name in the satellite's `friday-ota-apply` `SYSTEM` map that exists in the repo is covered by `package.satellite.system`.

### 2. Satellites wait for the server, and packages carry who built them (B)

- **Satellite offer:** `offer()` returns `waiting_server` unless `server_ota.installed_tag` is at least the offered tag (`ch.version_key`).
  - This sits next to the existing protocol gate (`order.satellite_ready`).
  - The server follows the most advanced channel among the devices (`server_channel`), so the server always reaches a satellite's target first.
  - If the server install fails and rolls back, satellites stay where they are. The existing failure notice covers it.
  - **[owner, 18/11] No bypass.** A satellite never goes past the gate, not even on a manual install, because a bypass would bring back a package built by an old server. Instead:
    - a manual satellite install request (`install_requested`) also sets the server's `install_requested` when the server is behind;
    - a satellite held at `waiting_server` for more than 1 hour is listed in the server sensor `ota_waiting_server` (state = count; attributes `satellites` = {id: {tag, since, why}}, `why` = auto off, image build, retries exhausted, heavy window). [owner, 18/11] The warning follows the house infrastructure alerts: a Home Assistant push to the owner in civil hours, outside the repo; the agent sends nothing.
- **Builder version:**
  - `meta.json` records `built_by`: the server agent's own `FRIDAY_VERSION` tag.
  - The server-side install still needs `archive.tar` for its rollback, so an older agent may build `archive.tar` but not `satellite.tar.gz`.
  - When the satellite package or the image is requested and `built_by` is older than the tag (or missing on a tag ≥ v0.3.10), the satellite package is rebuilt before use.
  - This is allowed because no satellite can have been offered it (gate above).
  - A package already offered is never replaced: immutability holds once a device may hold its sha256. **[my call]** `offer()` records an explicit `offered` marker in `meta.json` (tag, first offer time) the first time it returns a tag. Device events are not used because `offer()` writes none.
  - **[my call]** A server agent older than the tag refuses to build `satellite.tar.gz` (`server_older`). This covers a manual `friday.build_image` with a newer version and `package_for_update` too. A manual image of a tag the server has not installed yet waits for the server.
- **Image build:** it follows the same rule through the same `_package` path.

### 3. Whole rollback on the satellite (`agents/satellite/friday-ota-apply`)

- **Snapshot before install:**
  - **[my call]** Before the migrations (they write the same places as `_install_system`), write a snapshot to `/var/lib/friday/ota/system-backup/<tag>/`.
  - For each destination the install will write: if it exists, copy it (mode kept); if not, list it in `created.json`.
  - Sounds stay out of the snapshot: they are data, merged and never deleted (unchanged).
- **On rollback:** `_back` (health failure, manual rollback, interrupted recover) restores the snapshot, removes the `created` files, runs `systemctl daemon-reload` and `udevadm control --reload-rules`, then switches `current` and restarts as today.
- **[my call] Interrupted activation:** `recover()` restores the snapshot whenever one exists for the interrupted tag, including the "cut before the switch" case, where system files may already be new.
- **Cleanup:** a successful activation keeps only the snapshot of the last activation, and `prune` removes older ones.
- **Limit (stated, accepted):** the activation runs the `friday-ota-apply` installed on the Pi, not the one in the package.
  - MK1 has v0.3.9's (installed at 07:28), so the activation **to** v0.3.10 still rolls back the old way.
  - The snapshot protects activations from v0.3.11 on.
  - v0.3.10 itself is covered by B and by its own tests.

### 4. Migrations fail when they cannot do their job

- **0004:** `inst` returns 1 when its source is missing and `FRIDAY_RELEASE_DIR` is set. Satellites that have not run it yet, such as a card flashed with an old image, then fail loudly instead of recording it as done.
- **New 0005_audio_files.sh:**
  - It installs the same audio files from the release `system/`, idempotently. They live in `/usr/local/sbin`, `/etc/friday-satelite/asound`, udev, the sendspin drop-in and `friday-silence.service`.
  - It fails if any is missing.
  - It exists for MK1, where 0004 is recorded as done with nothing installed. v0.3.9's apply would install the files anyway through its `SYSTEM` map, but 0005 makes that independent of which apply runs.

### 5. Release v0.3.10

- **Contents:** v0.3.9 (#19–#22) plus this fix, bumped to `0.3.10` in the manifest. Patch number: semver-style `vX.Y.Z`, no breaking change.
- **Channel:** my call, the owner can override. Pre-release (alpha) first, as the OTA merge gate expects, with MK1 as the alpha satellite.
- **v0.3.9 notes:** one warning line at the top saying "Do not install. Superseded by v0.3.10 (the satellite package was incomplete)". The release is immutable, but its notes are editable.

## First rollout (v0.3.9 agent on the VM) [my call]

The VM runs the v0.3.9 server agent, which has neither gate B nor `built_by`.
- **Package:** it builds and offers v0.3.10 to MK1 right away, before the server updates. That package is complete, because the v0.3.9 built-in lists already include `satellite/audio`.
- **Gate B:** gate B starts working only from v0.3.11. This rollout is accepted as is: no `auto_install` toggling, no manual steps.
- **No rebuild:** the v0.3.10 agent then finds a package without `built_by`. If it has the `offered` marker, or if any device has v0.3.10 staged, installed or rolled back in the events, it is kept, never rebuilt.

## How MK1 recovers

**Through OTA:**
- The server installs v0.3.10 first (gate B). Its agent packs v0.3.10 from the contract.
- MK1's agent is connected and its v0.3.9 apply installs the v0.3.10 system files, the audio files included. 0005 runs too.
- The new unit then finds `friday-audio-select`, the program starts and health passes.

**Before that,** the owner may restore voice by hand with the v0.3.8 unit (command given on 18/11). The OTA path works either way.

## Tests (all in `scripts/test-all.sh`)

- **Packager:**
  - with a tree whose `release.json` declares `satellite/audio/*`, the package has them in `system/`;
  - with a declared file missing, the build raises and writes nothing;
  - without the key, the fallback lists are used (pre-contract tag).
- **Old server, new release:** pack the current tree with the built-in lists replaced by v0.3.8's. With the contract, the audio files are still in `system/`.
- **Coherence:** the built-in lists equal `release.json`, and the apply `SYSTEM` names are covered.
- **Orchestrator:**
  - the offer is `waiting_server` while `server_ota.installed_tag` is older;
  - a manual satellite request sets the server's `install_requested`; the satellite is listed by `waiting_server()` after 1 h of `waiting_server`, never pushed by the agent;
  - `built_by` older than the tag rebuilds the satellite package;
  - an offered package is never rebuilt.
- **Apply (fake root):**
  - activation, then a health failure: a pre-existing unit is restored byte for byte, a created helper is removed, and `daemon-reload` is called;
  - `prune` keeps the last snapshot.
- **Migrations:** 0004 and 0005 exit non-zero with a missing source and install everything with a full one.

## Left to the builder

- Exact JSON shape inside `package.satellite`, as long as it matches the two lists above.
- How `built_by` is read (the agent's `FRIDAY_VERSION` file).
- Snapshot file naming and where the `created` list lives inside the snapshot folder.

## Live checks (alpha)

- [ ] VM: `packages/v0.3.10/satellite.tar.gz` (built by the v0.3.9 agent) has the audio files in `system/`, and the v0.3.10 agent does not rebuild it.
- [ ] Gate B and `built_by` are proven on v0.3.11: `waiting_server` until the server installs, then `built_by` = v0.3.11.
- [ ] MK1 updates to v0.3.10 by itself: voice program running, wake word, music, volume, mute.
- [ ] v0.3.10 image builds, and a card flashed with it on the XVF3800 Pi speaks its onboarding.
- [ ] Forced rollback test on MK1 is deferred to v0.3.11, the first activation run by the new apply.
