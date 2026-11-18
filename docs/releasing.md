# Versioning and releases

F.R.I.D.A.Y. follows [Semantic Versioning 2.0.0](https://semver.org/). One version covers the whole repository:
the Home Assistant integration, the satellite program, agents and image, and the server services move together,
because OTA updates them in sync.

## The public API

SemVer needs a public API to decide what is breaking. Here it is **what the owner has to do by hand after an update**:

- re-flash an SD card or re-provision a device;
- change a configuration (Home Assistant, `config.json`, `local.env`) for things to keep working;
- lose a setting or a stored value;
- lose an entity, service or event that automations use (a rename counts as a removal);
- an `UPDATE_PROTOCOL` change that leaves devices on older stable releases unable to update
  (see `COMPAT_STABLE_RELEASES` in `agents/common/friday_agent_core/ota/constants.py`).

The version number does not replace the compatibility contracts that already exist. `UPDATE_PROTOCOL`, the numbered
satellite migrations (`satellite/migrations/NNNN_*.sh`) and the agent protocol (`docs/protocol.md`) keep their own
numbers. The release version only says what the owner can expect.

## Which number to bump

From 1.0.0 on:

| Bump | When |
|---|---|
| MAJOR | anything from the public API list above |
| MINOR | a new capability: entity, setting, service, supported hardware, or a migration that runs on its own |
| PATCH | fixes only, with no new entity or setting |

Before 1.0.0 (now), the leftmost non-zero number takes the major's role, like Cargo:

| Bump | When |
|---|---|
| `0.MINOR` | a breaking change (from the list above) |
| `0.x.PATCH` | everything else: new capabilities and fixes |

1.0.0 is a milestone, not a date: it ships after the owner's final test of the integration against its three
anti-goals (plan 2026-10-29, Task 20 Step 4). From then on the 1.0.0+ table applies strictly.

## Tags and channels

- Tags are exactly `vMAJOR.MINOR.PATCH` (regex `^v\d+\.\d+\.\d+$`). The OTA code ignores anything else, so a tag
  without the `v` or with a suffix is never installed.
- **No SemVer pre-release suffix** (`-alpha.1`, `-rc.1`). The channel comes from the GitHub release flag:
  a pre-release goes to the alpha channel, and the **same tag** is promoted to stable after the alpha soak
  (`SOAK_S`, 48 h) or by hand on GitHub. A suffix would force a second tag on promotion and lose the soak record,
  which is kept per tag.
- `custom_components/friday/manifest.json` carries the same version without the `v`. CI fails on a tag push whose
  name does not match it (`tests/test_versioning.py`), and a red check run keeps OTA from offering that release.
- Merge and tag only after the owner's OK.

## Release notes

Release notes follow the Home Assistant style, in this order:

1. **Breaking changes**: what the owner must do, step by step. Always present when the major (or `0.MINOR`)
   is bumped; omitted otherwise.
2. **New features**.
3. **Fixes**.
4. **Promotion gate (stable)**, on a pre-release: what has to be proven in alpha before promotion.

## Why not CalVer

Home Assistant uses CalVer (`YYYY.M.PATCH`) because it ships on a monthly train with a beta week and a six-month
deprecation policy; its release notes carry the breaking-change information that the number does not. This project
ships continuously (ten releases in four days in November 2026) and the owner's question about any update is "do I
have to do something?", which the SemVer major answers. The OTA regex would accept a CalVer tag, so a switch stays
possible later; the opposite is not (2026 is greater than any major this project will reach).
