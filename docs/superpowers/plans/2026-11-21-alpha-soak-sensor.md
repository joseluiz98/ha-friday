# Alpha promotion sensor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Home Assistant shows, on the Server device, when the current alpha release will be promoted to stable,
how long it has soaked, and whether something stops it (failed, superseded, not installed yet).

**Architecture:** The server agent already holds everything in its OTA store (`soak`, `promotions`, `releases`).
A pure function on the orchestrator turns that into one entity value plus attributes; the agent declares a new
timestamp sensor descriptor and pushes it with the other OTA entities. The integration builds entities from
descriptors, so it needs no code change.

**Tech Stack:** Python 3.13, the server agent (`agents/server`), pytest.

**Spec:** owner's approval on 21/11 of this proposal: "no aparelho Server, um sensor de horário 'Promoção prevista
em' (o HA mostra 'em 1 dia'), com atributos: versão, início, horas restantes e estado (contando / falhou /
superseded / promovida)". The plan's Global Constraints carry it.

## Global Constraints
- Entity key `ota_alpha_promotion`, name `Alpha promotion`, platform `sensor`, `device_class="timestamp"`,
  icon `mdi:timer-sand`, diagnostic category (same `**diag` as `ota_waiting_server`), on the Server device.
- State = the time the tracked alpha will be promoted (`started_at + SOAK_S`, ISO 8601 UTC with `+00:00`), or
  `None` (HA shows "unknown") when the tracked alpha has no running clock (not installed yet, failed) or there is no
  alpha to track.
- **Tracked alpha:** the newest (by `ch.version_key`) eligible pre-release (`_eligible()`, `prerelease=True`) whose
  tag is not in `promotions`. If none, the newest tag in `promotions` (so a just-promoted release stays visible
  with its result). If neither, nothing.
- Attributes (exact keys): `tag`, `status`, `started_at`, `promotes_at`, `soaked_hours`, `newer_stable`, `alphas`.
  - `status` ∈ `waiting_install` (no `started_at`), `soaking`, `ready` (48 h passed, waiting for the next 10-min
    promotion cycle or a GitHub retry), `failed`, `promoted`, `superseded`.
  - `started_at`/`promotes_at`: ISO strings or `None`. `soaked_hours`: hours from `started_at` to now (or to the
    promotion time once promoted), rounded to 0.1, `None` without a clock. It is the only value that changes with
    time; see the push-rate constraint.
  - `newer_stable`: `True` when a stable release newer than the tag already exists (it will end `superseded`, the
    rule decided on 19/11), else `False`.
  - `alphas`: list of `{tag, status, promotes_at}` for every eligible pre-release not in `promotions`, newest first
    (two alphas in series each keep their own clock).
- The owner's proposal said "horas restantes"; HA already renders a timestamp as "in 1 day", so the remaining time
  is the state itself and the attribute is `soaked_hours` (elapsed). Ruling, flag it in the PR.
- **Push rate:** `soaked_hours` is only recomputed when `ota_state()` runs (already every OTA cycle); it must not add
  a timer of its own.
- No change to promotion logic, to the store format or to the integration code. Version 0.4.2 (new entity = PATCH,
  `docs/releasing.md`); release notes file `docs/release-notes/v0.4.2.md`.
- Git messages and code in English; never `--no-verify`; tests' temp dirs under the job dir, never `/tmp`
  (`--basetemp`).

## Review Focus
1. A promoted tag whose stored `prerelease` flag is still `True` until the next GitHub poll: it must show
   `promoted`, not `soaking`/`ready` (`promotions` wins over the flag).
2. Two alphas in series (A installed, B published and installed later): tracked = B, `alphas` lists both with their
   own `promotes_at`; after A is promoted/superseded it leaves `alphas`.
3. A soak record with `failed=True`: state `None`, status `failed`, never a promotion time.
4. A release published but never installed on an alpha satellite (MK1 skipped it): `waiting_install`, state `None`.
5. An empty store (fresh VM, GitHub not polled yet): everything `None`, `alphas == []`, no exception.

---

### Task 1: `Orchestrator.alpha_promotion()`

**Files:**
- Modify: `agents/server/friday_server_agent/ota/orchestrator.py` (new method next to `waiting_server`)
- Test: `tests/agents/test_ota_orchestrator.py`

**Interfaces:**
- Consumes: `self.store.soak()` → `{tag: SoakRecord(started_at, failed)}`, `self.store.data["promotions"]`
  (`{tag: {"at": float, "result": "promoted"|"superseded"}}`), `self.store.release_infos()`, `self._eligible()`,
  `ch.version_key`, `ch.eligible`, `SOAK_S`, `self._clock()`.
- Produces: `Orchestrator.alpha_promotion(self) -> tuple[str | None, dict]` — `(state, attributes)` per Global
  Constraints. Takes `self.store.lock`.

- [ ] **Step 1: Write the failing tests** (use the file's existing `rel()` helper and the way other tests build an
  `Orchestrator` with a fake clock; set `SOAK_S` through the imported constant, never a literal 172800):
  - `test_alpha_promotion_soaking`: pre-release `v1.1.0` with `soak_start("v1.1.0", t0)`, clock `t0 + 3600` →
    state `iso(t0 + SOAK_S)`, `status == "soaking"`, `soaked_hours == 1.0`, `newer_stable is False`.
  - `test_alpha_promotion_ready_after_soak`: clock `t0 + SOAK_S + 60` → `status == "ready"`, state unchanged.
  - `test_alpha_promotion_waiting_install` (Review Focus 4): no soak record → state `None`,
    `status == "waiting_install"`, `started_at is None`, `soaked_hours is None`.
  - `test_alpha_promotion_failed` (Review Focus 3): `soak_fail` → state `None`, `status == "failed"`.
  - `test_alpha_promotion_promoted_wins_over_stale_flag` (Review Focus 1): release still `prerelease=True`,
    `promotions["v1.1.0"] = {"at": t1, "result": "promoted"}`, no other alpha → `tag == "v1.1.0"`,
    `status == "promoted"`, `soaked_hours == round((t1 - t0)/3600, 1)`, `alphas == []`.
  - `test_alpha_promotion_two_in_series` (Review Focus 2): `v1.1.0` started t0, `v1.2.0` started t0+7200 → tracked
    `v1.2.0`; `[a["tag"] for a in attrs["alphas"]] == ["v1.2.0", "v1.1.0"]`, each `promotes_at` from its own start.
  - `test_alpha_promotion_newer_stable`: stable `v1.2.0` published, alpha `v1.1.0` soaking → `newer_stable is True`.
  - `test_alpha_promotion_empty_store` (Review Focus 5): `(None, {... all None ..., "alphas": []})`, no exception.
- [ ] **Step 2:** `python3 -m pytest -q tests/agents/test_ota_orchestrator.py -k alpha_promotion --basetemp=<job>/pt`
  Expected: FAIL (`AttributeError: ... alpha_promotion`).
- [ ] **Step 3:** Implement `alpha_promotion` per the Interfaces block; ISO via
  `datetime.fromtimestamp(ts, timezone.utc).isoformat()`.
- [ ] **Step 4:** Same command. Expected: PASS; then the whole file PASS.
- [ ] **Step 5:** Commit `feat(ota): compute the alpha promotion clock for Home Assistant`.

### Task 2: Descriptor and push

**Files:**
- Modify: `agents/server/friday_server_agent/collectors.py` (descriptor list, after `ota_waiting_server`)
- Modify: `agents/server/friday_server_agent/__main__.py` (`ota_state()`)
- Test: `tests/agents/test_server_collectors.py`, plus the test that covers `ota_state` if one exists
  (grep `ota_waiting_server` in `tests/`; extend the same test)

**Interfaces:** Consumes `Orchestrator.alpha_promotion()`. Produces state keys `ota_alpha_promotion` and
`ota_alpha_promotion_attributes`.

- [ ] **Step 1: Tests:** `test_alpha_promotion_descriptor` — the descriptor list holds key `ota_alpha_promotion`
  with `platform == "sensor"`, `device_class == "timestamp"`, `icon == "mdi:timer-sand"`, the diagnostic category,
  name `"Alpha promotion"`; and the `ota_state` test asserts both keys are present and come from
  `alpha_promotion()` (fake orchestrator).
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Add `s("ota_alpha_promotion", "Alpha promotion", device_class="timestamp", icon="mdi:timer-sand", **diag)`
  and, in `ota_state()`, `"ota_alpha_promotion": value, "ota_alpha_promotion_attributes": attrs` from one call.
- [ ] **Step 4:** `python3 -m pytest -q tests/agents --basetemp=<job>/pt` and
  `python3 -m pytest -q tests/integration --basetemp=<job>/pt` (the integration builds the sensor from the
  descriptor: if an integration test pins the descriptor list or entity count, update it). Expected: PASS.
- [ ] **Step 5:** Commit `feat(ota): Alpha promotion sensor on the server device`.

### Task 3: Version, notes, house doc pointer

**Files:** `custom_components/friday/manifest.json` (`"version": "0.4.2"`), `docs/release-notes/v0.4.2.md`
(alpha header and structure copied from `v0.4.1.md`; New features: the sensor, its statuses; Changes by area:
server agent only, integration no code change; Promotion gate: on the alpha, the sensor shows v0.4.2 `soaking` with
the right time after the MK1 installs it, and `promoted` after 48 h).

- [ ] **Step 1:** `python3 -m pytest -q tests/test_versioning.py --basetemp=<job>/pt`. Expected: PASS.
- [ ] **Step 2:** `scripts/test-all.sh` (redirect to a file, read the tail). Expected: all steps OK.
- [ ] **Step 3:** Commit `chore: manifest 0.4.2 and release notes`. Do not push; the controller reviews, pushes
  and opens the PR.
