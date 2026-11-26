# Two-beam listening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In a satellite conversation, only the caller's speech opens, holds and ends a turn, so TV speech heard
through the XVF3800 never reaches Gemini, never starts a follow-up and never keeps the conversation open. This must not
add false activations.

**Architecture:**
- **Pi (XVF3800 satellites).** During a conversation the board's two fixed beams point at the caller (beam A) and at
  the TV (beam T). A pure detector classifies every 80 ms frame (caller / tv / unclear / silence) from Silero plus the
  per-beam speech energy ratio, and feeds the existing `DetectorFala`.
- **Turns.** The Pi drives the turns: the gateway disables Gemini's server VAD and uses the existing `PiSpeechTurns`.
- **Identity (shadow only in this plan).** A speaker-embedding check runs on the VM server agent and the gateway
  records its verdicts.

**Tech Stack:** Python 3.11 on the Pi 3 (stdlib, numpy, pyusb via the vendor `xvf_host.py`); pipecat in the `friday`
container (Python 3.11); server agent (aiohttp, Python 3.13) with `sherpa-onnx==2.0.3`; Home Assistant integration
`custom_components/friday`.

**Spec:** `docs/superpowers/specs/2026-11-26-two-beam-listening-design.md` (approved by the owner on 26/11/2026). Read
it before any task. Section names below (D1–D9) refer to it.

## Global Constraints

- **Worktree and branch.** Work in `/config/dev/ha-friday-wt-twobeam`, branch `feat/two-beam-listening`. Commit
  messages in English, ending with the session trailers the controller gives you. Never `--no-verify`. Never push
  (the controller does).
- **Never `SAVE_CONFIGURATION`** on the XVF3800. Every fixed-beam state is released at program start (already in
  `xvf_link.apply_startup`; Task 1 extends it with the elevation reset).
- **The idle wake word must not change.** Outside a conversation the board stays in factory mode: fixed beams off,
  `OP_L 8 0`, `OP_R 7 3`. No new code path may change the wake-word channel or thresholds while idle.
- **Pi 3: no new model and no new thread that does heavy work.** New USB reads go through `XvfLink` and must be bounded
  (Task 1).
- **Jabra satellites and the web client (`/client/`) behave exactly as today.** Whispered conversations keep their
  path (1.5 s preroll, whisper TTS).
- **Settings with the owner's decisions (spec, "Owner decisions of 26/11").** Program config keys, English, with
  defaults:
  - `two_beam_listening: True`
  - `tv_azimuth_deg: None` (0–359.9; None = no TV)
  - `tv_elevation_deg: 0` (0–80)
  - `caller_margin_db: 3.0` and `tv_margin_db: 6.0` (0–20). Asymmetric on purpose: taking the caller for TV costs
    more than the reverse, until the Phase 0 telemetry sets them.
  - `caller_silence_s: 1.5` (0.5–5)
  - `turn_max_s: 60` (10–300)
- **Azimuths are radians in the board's `AEC_AZIMUTH_VALUES` frame.** Degrees shown to people are
  `degrees(rad) % 360` in that same frame: the existing log "trava de feixe: travada em X°" uses it.
- **Tests.** Satellite program: `cd satellite/program && python3 -m unittest -q`. Agents and integration:
  `bash scripts/test.sh` (runs on the VM). Gateway: the docker step of `scripts/test-all.sh`. Before the final task the
  whole `scripts/test-all.sh` must print `ALL GREEN`.
- **The repo is public:** no house values (IPs, names, tokens) in code, tests or docs. Use `sat-xxxxxx`-style fake ids.
- **Naming:** new Pi code may use Portuguese names, like its neighbours; new public keys, routes, NDJSON fields and HA
  entities are English.

## Traceability (owner, 26/11: key events only, never per frame)

Goal: after promotion, any odd conversation on MK1 or MK2 can be explained from the Pi journal
(`journalctl -u friday-satelite`), the gateway log (`docker logs friday`) and the conversation NDJSON, without
inflating any of them.

**Format and budget rules (every task):**
- **Pi:** one JSON object per line, `print(json.dumps({...}), flush=True)`, as the program already does. The top-level
  key names the event. Values are compact; floats are rounded to 1 decimal (deg, dB, s).
- **Gateway:** one `logger.info` / `logger.warning` line, prefixed `two-beam:`.
- **Never per frame or per poll.** At most one line per key event below, and one summary per conversation. A repeating
  failure (USB, scoring) is logged once per episode with its count at recovery, like `XvfLink` does today.

| Where | Event (top-level key) | When | Fields |
|---|---|---|---|
| Pi | `two_beam_config` | program start, and after a config change that touches these keys | `enabled`, `permitted` + `why_not` (`board`, `only_word_off`, `off`), `tv_azimuth_deg`, `tv_elevation_deg`, `caller_margin_db`, `tv_margin_db`, `caller_silence_s`, `turn_max_s` |
| Pi | `two_beam_mode` | each conversation, at connect | `mode` (`pi` or `v046`), `reason` (`locked`, `locked_tv_excluded`, `lock_failed`, `board_busy` (the TV calibration owns the board), `no_wake_word`, `whisper`, `not_permitted`), `beam_a_deg`, `beam_t_deg`, `tv_elev_deg` |
| Pi | `two_beam_reaim` | each re-aim | `cause` (`follow_up` or `wake_word`), `from_deg`, `to_deg`, `polls` |
| Pi | `two_beam_energy` | the detector enters pass-through because energy went stale, and again when it recovers | `state` (`stale` or `ok`), `failed_reads`, `stale_s` |
| Pi | `two_beam_turn_ceiling` | the 60 s ceiling split a turn | `turn_s`, `frames` (per class) |
| Pi | `two_beam_dropped_candidate` | a run of ≥ 0.5 s of `tv` frames with Silero up, while the mic was open, no turn was in progress, and no `caller` frame came in the next 1 s. This is the case where a caller quieter than the TV would be lost | `s`, `ratio_db_median`, `state` |
| Pi | `two_beam_summary` | each two-beam conversation, at close (one line) | `turns`, `by` (`pause` / `caller_voice` / `ceiling` counts), `frames` (per class), `tv_runs`, `dropped_candidates`, `reaims`, `energy_stale_s`, `failed_reads`, `lock_s`, `release` (`closed`, `ceiling90`, `error`, `startup`) |
| Pi | `tv_direction` | calibration start, and its result (two lines) | start: `historico_deg`; result: the routine's dict, plus `energy_by_elevation` (list of 6 rounded means) |
| Gateway | `two-beam: session` | conversation start with `pi_turns` | sat id, preroll first/later |
| Gateway | `two-beam: no answer` | 10 s after a Pi-driven `activity_end` with no model output and no tool call for that turn. Without this line such turns leave no NDJSON line at all | sat id, the turn's `twoBeam` payload |
| Gateway | `two-beam: window without audio` | the existing `PiSpeechTurns` stop timeout | adds the sat id |
| Gateway | `two-beam: speaker gate unavailable` | once per process, when the server route answers `available: false` or fails 3 times in a row | reason |
| NDJSON | `turnMode`, `twoBeam`, `speakerGate` | per turn (Tasks 9 and 11) | as defined there |
| Server agent | `speaker gate:` | enroll CLI summary; route errors rate-limited to one per minute | person → windows; error class |

The report tool (Task 12) reads `two_beam_summary`, `two_beam_mode`, `two_beam_dropped_candidate` and
`two_beam_turn_ceiling` from a journal export, so these lines are the release judgement's input, not just debugging.

## Review Focus

1. **The board resets or a USB read fails mid-conversation.** Expected: within 1 s the detector falls back to
   pass-through (today's behavior), the turn still ends at the caller's pause, and nothing hangs. Test in Task 3
   (`test_stale_energy_falls_back_to_passthrough`).
2. **"Friday, liga a luz" said in one breath.** The speech is already running when the connection opens. Expected: the
   first turn starts at `conectou` with nothing lost (1.5 s preroll on the first turn). Tests in Task 5a
   (`test_pi_turns_connect_with_speech_running_starts_turn`) and Task 9 (`test_first_turn_preroll_then_short`).
3. **TV off while a TV direction is stored.** eT ≈ 0, so every speech frame is `caller`. Expected: normal conversation;
   a continuous non-TV sound is cut only by the 60 s turn ceiling, which splits the turn without closing the
   conversation. Tests in Task 3 (`test_tv_silent_means_caller`, `test_turn_ceiling_splits_turn`).
4. **The program crashes with two beams locked, or the TV calibration is interrupted.** Expected: at program start and
   at every release, fixed beams off, elevations `[0, 0]`, outputs `8 0` / `7 3`. Tests in Task 2
   (`test_release_resets_elevation`) and Task 6 (`test_calibration_always_ends_released`).
5. **Calibration pressed while a conversation is on, or a wake word arrives mid-calibration.** Expected:
   refused while not idle; aborted on the wake word with the beams released; no value stored. Tests in Task 6
   (`test_calibration_refused_when_not_idle`, `test_calibration_abort_releases`).

## Execution order (owner, 26/11: parallel)

Tasks that touch disjoint files run in parallel, each in its own git worktree, branched from `feat/two-beam-listening`
with its own `FRIDAY_TEST_DIR`. The controller reviews each one and merges it into `feat/two-beam-listening`.

| Wave | Tasks | Why this order |
|---|---|---|
| 1 | 1, 2, 3, 4, 5a, 9, 10 | disjoint files; interfaces fixed in this plan |
| 2 | 7, 8, 12 | agent, integration, tools; also disjoint |
| 3 | 5b | wires Tasks 1–4 and 5a into `friday_satelite.py` |
| 4 | 6, 11 | 6 needs 2 and 5b (same files); 11 needs 9 and 10 |
| 5 | 13 | the whole branch |

---

### Task 1: Bounded register reads in `XvfLink`

**Files:**
- Modify: `satellite/program/xvf_link.py`. Also `apply_startup` writes `AEC_FIXEDBEAMSELEVATION_VALUES [0, 0]` right
  after it releases the fixed beams. It lives here, not in Task 2, so the two tasks never edit the same file.
- Test: `satellite/program/test_xvf_link.py` (adds `test_startup_resets_elevation`)

**Interfaces:**
- Produces: `XvfLink.read_bounded(nome: str, max_retries: int = 5, timeout_ms: int = 300) -> list | None`, plus
  `XvfLink.falhas_leitura: int`, a running count of failed bounded reads that the program uses for traceability.
  - Same return type as `read`: a list of decoded values, or None on any failure, busy lock or unknown name.
  - Never raises.

- [ ] **Step 1: Write failing tests** in `test_xvf_link.py`, with a fake vendor device that exposes `.dev.ctrl_transfer`
  and a `PARAMETERS` table:
  - `test_read_bounded_decodes_floats`: a 4-float payload preceded by status byte 0 returns the 4 floats.
  - `test_read_bounded_gives_up_after_max_retries`: status 64 forever gives None after exactly 1 + `max_retries`
    transfers.
  - `test_read_bounded_unknown_name_none`.
  - `test_read_bounded_usb_error_reopens`: `ctrl_transfer` raises. Expect None and the next call re-creates the device
    through the factory.
- [ ] **Step 2: Run** `cd satellite/program && python3 -m unittest -q test_xvf_link`. Expected: FAIL, no attribute
  `read_bounded`.
- [ ] **Step 3: Implement** `read_bounded`.
  - Use the same lock and reopen rules as `_chamar`.
  - The transfer is `CTRL_IN | CTRL_TYPE_VENDOR | CTRL_RECIPIENT_DEVICE`, request 0, `wValue = 0x80 | cmdid`,
    `wIndex = resid`, length `count * size + 1`.
  - Decode by type (`float`/`radians` → `<f`, `int32` → `<i`, `uint16` → `<H`, `uint8` → `<B`).
  - Status 0 = ok; 64 = sleep 10 ms and retry; anything else = None.
  - Keep a reference to the vendor module's `PARAMETERS` in `abrir_vendor` (return it alongside the device, or store
    it on the device object). The reference implementation is
    `tools/satellite_measurements/xvf_telemetry.py`, `Board.read`.
- [ ] **Step 4: Run** the same command. Expected: PASS, and the whole suite stays OK.
- [ ] **Step 5: Commit** `feat(satellite): bounded XVF3800 register reads`.

### Task 2: Two-beam lock in `doa.py`

**Files:**
- Modify: `satellite/program/doa.py` (the `apply_startup` change is in Task 1)
- Test: `satellite/program/test_doa.py`

**Interfaces:**
- Consumes: the `XvfLink` `write`/`read` API, unchanged.
- Produces:
  - `Direcao.decidir(agora, janela_s=JANELA_DECISAO_S, excluir=None)`. `excluir` = `(theta_rad, meia_largura_graus)`.
    Samples inside that sector are dropped before the existing checks.
  - `TravaFeixe.travar(theta_a: float, theta_t: float | None = None, elev_t: float = 0.0) -> bool`.
    - Writes `AEC_FIXEDBEAMSAZIMUTH_VALUES [θa, θt or θa]`.
    - When θt is given, writes `AEC_FIXEDBEAMSELEVATION_VALUES [0.0, elev_t]`.
    - Then `AEC_FIXEDBEAMSONOFF 1`, then `OP_R 7 0` and `OP_L 6 0`, with today's read-back fallback.
    - Stores `self.theta_a` and `self.theta_t`.
  - `TravaFeixe.reapontar(theta_a: float, causa: str = "follow_up", polls: int = 0) -> bool`. Rewrites only the
    azimuth pair `[θa, current θt]` and logs `two_beam_reaim` (`cause`, `from_deg`, `to_deg`, `polls`) through the `log`
    callable it already receives.
  - `TravaFeixe.renovar() -> None`. Re-arms the 90 s ceiling from now.
  - `TravaFeixe.soltar(motivo: str = "closed")`. `motivo` is one of `closed`, `ceiling90`, `error` or `startup`;
    `tique()` passes `ceiling90`. The existing lock and release lines also gain `beam_t_deg` when a TV beam is set.
    `TravaFeixe` records `self.travada_desde` and `self.motivo_soltura`, the release reason, for the summary.
  - `Regra.ultima_decisao: str`. Set by every `palavra()` call to `locked`, `locked_tv_excluded` or `lock_failed`; it
    feeds `two_beam_mode.reason`.
  - `TravaFeixe.soltar()` also writes `AEC_FIXEDBEAMSELEVATION_VALUES [0.0, 0.0]`.
  - `class Reaponta` with `alimentar(t: float, az_livre: float | None, e_livre: float | None)`,
    `decidir(agora: float, theta_a: float, theta_t: float | None) -> float | None` and `zerar()`. The rule is spec D4:
    - of the polls in the last 0.6 s, at least 3 of the last 4 have `e_livre > 0`;
    - those are within ±20° of their circular mean;
    - the mean is ≥ 30° from θa, and ≥ 30° from θt when θt is not None.
  - `Regra.__init__(direcao, trava, ligada, dois_feixes=lambda: False, tv=lambda: None)`.
    - `dois_feixes()` = is two-beam listening on for this conversation's satellite.
    - `tv()` = `(theta_t_rad, elev_t_rad)` or None.
  - `Regra.palavra(agora) -> float | None` (degrees for the ring, as today):
    - **Two-beam on:** decide; on None, retry with `excluir=(theta_t, 30)` when a TV is known. Lock with
      `travar(theta, theta_t, elev_t)`, or `travar(theta)` with no TV. Then `self.dois_feixes_ativo = True`.
    - **Two-beam on ignores the "Beam lock" switch (`ligada()`).** The beams are part of the feature (spec D8). An
      extra test pins it: `test_two_beam_locks_even_with_beam_lock_off`.
    - **Two-beam off:** today's behavior.
  - `Regra.resposta_acabou()` releases only when `dois_feixes_ativo` is False (whole-conversation lock, owner Q1).
  - `Regra.conversa_fechou()` releases and sets `dois_feixes_ativo = False`.
  - `registrar_direcao(caminho: str, theta: float, agora: float) -> None` appends `{"t": agora, "deg": …}` to an
    NDJSON file, pruning lines older than 7 days.
  - `mediana_direcoes(caminho: str, agora: float) -> float | None` returns the circular median in radians, or None
    with fewer than 10 lines.
  - The path is `/home/jose/satelite/estado/caller_directions.ndjson`, passed in by the program (Task 5b). Never
    hard-code it in tests.

- [ ] **Step 1: Write failing tests** in `test_doa.py`, extending the existing `Link` fake:
  - `test_two_beam_lock_writes_both_azimuths_and_elevation`: `travar(rad(140), rad(10), rad(45))` writes the azimuth
    pair `[rad(140), rad(10)]`, the elevations `[0.0, rad(45)]`, ON 1, `OP_R [7,0]`, `OP_L [6,0]`, in that order.
  - `test_release_resets_elevation`: `soltar()` after a two-beam lock writes ON 0, elevations `[0.0, 0.0]`,
    `OP_R [7,3]`, `OP_L [8,0]`.
  - `test_decide_retries_without_tv_sector`: samples alternate between 10° and 140°. Plain `decidir` gives None;
    `excluir=(rad(10), 30)` gives ≈140°.
  - `test_whole_conversation_lock_survives_first_answer`: with `dois_feixes` true, `resposta_acabou()` keeps it locked,
    and `conversa_fechou()` releases it.
  - `test_reaponta_needs_three_of_four_far_from_beams`: 4 polls at 230° with energy gives 230° when beam A is at 140°
    and T at 10°. Polls at 150° give None (too close to A).
  - `test_ceiling_rearmed_by_renovar`: with a fake clock, lock at t=0, `renovar()` at t=80, `tique()` at t=100 still
    locked, `tique()` at t=171 released.
  - `test_direction_history_prunes_and_medians`: written in a tempdir under `TMPDIR`; the test must leave nothing
    behind.
- [ ] **Step 2: Run** `cd satellite/program && python3 -m unittest -q test_doa`. Expected: the new tests
  FAIL.
- [ ] **Step 3: Implement** the interfaces above. Update existing assertions in `test_doa.py` only where the new
  elevation write is now expected on release.
- [ ] **Step 4: Run** the whole satellite suite. Expected: OK.
- [ ] **Step 5: Commit** `feat(satellite): two fixed beams (caller and TV) for the whole conversation`.

### Task 3: Caller-speech detector and turn ceiling (pure)

**Files:**
- Modify: `satellite/program/detectores.py`
- Test: `satellite/program/test_detectores.py`

**Interfaces:**
- Produces:
  - `class EnergiaFeixes` (thread-safe):
    - `atualizar(t: float, valores: list[float] | None)`: the 4 `AEC_SPENERGY_VALUES`; None = read failed.
    - `ultimas() -> tuple[tuple[float, float, float] | None, float | None]`: `((eA, eT, eF), t)`.
  - `class DetectorQuemChamou(tv_configurada: bool, margem_quem_chamou_db=3.0, margem_tv_db=6.0,
    silencio_quem_chamou_s=1.5, pausa_s=0.8, limiar=0.5, validade_s=1.0)`:
    - `alimentar(prob: float, energias, t_energias, agora: float) -> tuple[float, str]` returns the effective
      probability for `DetectorFala` and the class: `"caller" | "tv" | "unclear" | "silence"`.
    - `cortou_por_voz: bool`: true when the last zeroed frame came from the caller-silence rule.
    - `estatisticas() -> dict`: `{"frames": {"caller": n, "tv": n, "unclear": n}, "ratio_db_median": float | None}`.
    - `zerar_estatisticas()`.
    - `em_passagem: bool` and `motivo_passagem: str | None` (`no_tv` or `stale`). The program prints `two_beam_energy`
      on each change of `motivo_passagem` to or from `stale`.
    - `trecho_tv() -> dict | None`: returns once, after a run of consecutive `tv` frames of at least 0.5 s ends,
      `{"s": duration, "ratio_db_median": …}`; else None. The stats also count `tv_runs`. Without this, a caller
      quieter than the TV would vanish from every metric (anti-goal: cutting the caller).
  - `DetectorFala` gains `max_quadros: int | None = None` (constructor keyword and attribute) and
    `parou_por_teto: bool`.
- Rules (spec D3/D6, owner Q6):
  - **Pass-through** (`tv_configurada` False, energies None, or older than `validade_s`): a frame with
    `prob ≥ limiar` is `unclear` with the same prob; below is `silence` with prob 0.
  - **Otherwise,** `r = 10·log10(eA/eT)` when both > 0:
    - `caller` if eT ≤ 0 or r ≥ `margem_quem_chamou_db` (prob → 1.0);
    - `tv` if eA ≤ 0 < eT or r ≤ −`margem_tv_db` (prob → 0);
    - else `unclear` (prob unchanged).
  - **Caller-silence rule.** A segment starts at the first `prob ≥ limiar` frame after at least `pausa_s` of silence
    frames. Inside a segment that had a `caller` frame, once `agora − t_last_caller ≥ silencio_quem_chamou_s`,
    `unclear` frames get prob 0 and set `cortou_por_voz`. The class stays `unclear` in the stats.
  - **`DetectorFala` with `max_quadros`.** After that many consecutive frames in the `falando` state it emits
    `"fala_parou"` and sets `parou_por_teto = True`. The normal start rule then applies again, so the turn splits.

- [ ] **Step 1: Write failing tests:**
  - `test_caller_frame_when_margin_met`: eA=4, eT=1 (6 dB) gives `(1.0, "caller")`.
  - `test_tv_frame_when_tv_dominates`: eA=1, eT=4.5 (−6.5 dB) gives `(0.0, "tv")`; eA=0, eT=1 gives `"tv"`; eA=1,
    eT=3 (−4.8 dB) gives `"unclear"`.
  - `test_unclear_between_margins`: eA=1.5, eT=1 (1.8 dB) gives the same prob and `"unclear"`.
  - `test_tv_silent_means_caller`: eT=0, eA=0.5, prob 0.9 gives `"caller"`.
  - `test_passthrough_without_tv_direction`: `tv_configurada=False`, prob 0.9 gives `(0.9, "unclear")`.
  - `test_stale_energy_falls_back_to_passthrough`: energies 1.2 s old give `"unclear"` with the prob unchanged.
  - `test_caller_silence_zeroes_unclear_tail`: caller frames for 1 s, then unclear frames. After 1.5 s from the last
    caller frame, prob 0 and `cortou_por_voz` is True. A pause of ≥ 0.8 s of silence resets the segment.
  - `test_turn_ceiling_splits_turn`: `DetectorFala(max_quadros=750)` fed 1.0 for 760 frames gives `"fala_comecou"`,
    then `"fala_parou"` with `parou_por_teto`, then `"fala_comecou"` again within 2 frames.
  - `test_stats_count_classes_and_median_ratio`.
  - `test_tv_run_reported_once_after_half_second`.
- [ ] **Step 2: Run** `python3 -m unittest -q test_detectores`. Expected: FAIL (names missing).
- [ ] **Step 3: Implement.** Keep both classes free of I/O and threads, except the lock inside `EnergiaFeixes`.
- [ ] **Step 4: Run** the whole satellite suite. Expected: OK.
- [ ] **Step 5: Commit** `feat(satellite): caller-speech detector and per-turn ceiling`.

### Task 4: Config keys and connect body

**Files:**
- Modify: `satellite/program/configuracao.py`
- Test: `satellite/program/test_configuracao.py`

**Interfaces:**
- Produces:
  - `PADROES` gains the 7 keys of Global Constraints.
  - `_REGRAS` gains:
    - `two_beam_listening` (bool)
    - `tv_azimuth_deg` ((int, float, NoneType), 0, 359.9)
    - `tv_elevation_deg` ((int, float), 0, 80)
    - `caller_margin_db` and `tv_margin_db` ((int, float), 0, 20)
    - `caller_silence_s` ((int, float), 0.5, 5)
    - `turn_max_s` ((int, float), 10, 300)
  - `dados_conexao(sid, cfg, extras)` copies `extras["pi_turns"] is True` into the body as `"pi_turns": True`. It never
    sends the key otherwise.
  - `dois_feixes_permitido(cfg: dict, placa: str) -> bool`: True only if all of these hold:
    - `placa == "xvf3800"`;
    - `cfg["two_beam_listening"]` is not False;
    - `cfg["only_wake_word_interrupts"]` is not False.

- [ ] **Step 1: Write failing tests:**
  - `test_two_beam_defaults`.
  - `test_tv_azimuth_none_allowed_and_range_checked`: 360 is refused, None is accepted.
  - `test_pi_turns_in_connect_body_only_when_true`.
  - `test_two_beam_needs_xvf_and_only_word_interrupts`.
- [ ] **Step 2: Run** `python3 -m unittest -q test_configuracao`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the whole satellite suite. Expected: OK.
- [ ] **Step 5: Commit** `feat(satellite): two-beam listening settings`.

### Task 5a: Maestro: Pi-driven turns (pure)

**Files:**
- Modify: `satellite/program/maestro.py`
- Test: `satellite/program/test_maestro.py`

**Interfaces:**
- Produces:
  - `Maestro.turnos_pi(ativo: bool) -> None`: the program calls it right after deciding the mode at `conectar`.
    `_zerar` resets it to False.
  - **Every place that checks `self.whisper` to send speech events also fires under `pi_turns`.** Grep `self.whisper`
    in `maestro.py`: the `_aviso` helper (`(self.whisper or self.pi_turns) and e == OUVINDO`), the `conectou` branch,
    and the inherited `speech_started` in `_reabrir`.
  - **Check every state in which the program streams microphone audio** (`microfone_aberto or guardando_pausa`). Each
    must send `speech_started` / `speech_stopped` under `pi_turns`.
  - **On `conectou` with `pi_turns` and not `whisper`:** `[("avisar_bot", "speech_started")]` if `_alguem_falando`,
    else `[]`.
  - **New event `"fala_quem_chamou"`:** in `ABERTO` with `_alguem_falando`, it sets `_fala_desde = agora` (caller speech
    never ages into `fala_longa`) and returns `[]`.

- [ ] **Step 1: Write failing tests** in `test_maestro.py`:
  - `test_pi_turns_sends_speech_events`: with `turnos_pi(True)`, `fala_comecou` / `fala_parou` in OUVINDO produce
    `avisar_bot` speech_started / speech_stopped.
  - `test_pi_turns_connect_with_speech_running_starts_turn`.
  - `test_pi_turns_off_by_default_no_events`: today's non-whisper conversation sends none.
  - `test_pi_turns_repeat_during_her_pause_opens_turn`: speech begun during her 1.5 s pause sends `speech_started` when
    the mic reopens (the 21/11 anti-goal).
  - `test_pi_turns_word_cuts_her_then_request_opens_turn`: the wake word cuts her answer, then the new request sends
    `speech_started`.
  - `test_caller_speech_never_hits_speech_max`: `speech_max_s=15`, `fala_comecou` at 0, `fala_quem_chamou` every 1 s
    until 40 s, and no `fala_longa`.
  - `test_unclear_speech_still_hits_speech_max`: no `fala_quem_chamou`, so `fala_longa` at 15 s, as today.
- [ ] **Step 2: Run** `cd satellite/program && python3 -m unittest -q test_maestro`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the whole satellite suite. Expected: OK.
- [ ] **Step 5: Commit** `feat(satellite): the maestro drives turns from the Pi in two-beam conversations`.

### Task 5b: Program wiring (the feature, end to end on the Pi)

**Files:**
- Modify: `satellite/program/friday_satelite.py` (the maestro part is Task 5a)
- Test: `satellite/program/test_satelite_ligacao.py` (or the existing wiring test
  that fakes the program; follow its pattern)

**Interfaces:**
- Consumes: Tasks 1–4 and 5a.
- Produces (program):
  - **At `conectar`, when `arg` has `wake_word`, not `whisper`, and `dois_feixes_permitido`:** call
    `doa_regra.palavra(...)`. If it locked, `pi_turns = True`:
    - pass `{"pi_turns": True}` in the extras to `dados_conexao`;
    - call `maestro.turnos_pi(True)`;
    - set `self.dois_feixes = True`;
    - `registrar_direcao(...)`.

    Otherwise this conversation runs exactly as v0.4.6.
  - **Per conversation, the program builds** `DetectorQuemChamou(tv_configurada=cfg["tv_azimuth_deg"] is not None,
    margem_quem_chamou_db=cfg["caller_margin_db"], margem_tv_db=cfg["tv_margin_db"],
    silencio_quem_chamou_s=cfg["caller_silence_s"], pausa_s=cfg["end_of_speech_pause_s"])`.
    - `Regra`'s `tv()` returns `(radians(tv_azimuth_deg), radians(tv_elevation_deg))`, or None.
    - Capture-thread events reach the maestro the way `fala` does today: `self.loop.call_soon_threadsafe(self.evento,
      ...)`.
  - **The DoA poll thread (`doa.ler_continuo`).** While `self.dois_feixes` is set, it also reads `AEC_SPENERGY_VALUES`
    and `AEC_AZIMUTH_VALUES` with `read_bounded` every poll. It feeds `EnergiaFeixes.atualizar` and
    `Reaponta.alimentar(t, az[2], e[2])`. Change the function's signature to accept optional `energia` and `reaponta`
    objects and an `ativo` callable.
  - **Capture loop.** While `self.dois_feixes`:
    - `prob_fala = detector.alimentar(voz, *energia.ultimas(), agora)[0]` before `df.alimentar`;
    - `df.max_quadros = round(cfg["turn_max_s"] / 0.08)`;
    - on every `caller` frame, at most once per second, post the maestro event `"fala_quem_chamou"`.

    With it off: `df.max_quadros = None` and no detector, which is today's behavior.
  - **`avisar_bot` payloads.**
    - `speech_started` carries `{"tv_active": bool}`: eT > 0 in any frame of the last 1 s.
    - `speech_stopped` carries the detector stats, plus `"by"` (`"ceiling"` if `df.parou_por_teto`, `"caller_voice"`
      if `cortou_por_voz`, else `"pause"`), plus `"beam_a_deg"` and `"beam_t_deg"` (None without a TV). Stats are
      reset after each `speech_stopped`.
    - Extend `_agir("avisar_bot", ...)` to send `self.conexao.enviar_mensagem(tipo, dados)` with that dict instead of
      `{}`. The maestro still returns only the type; the program attaches the payload.
  - **Re-aim** (owner Q1). Between `fim_da_resposta` and the next `"fala_comecou"`, the poll thread asks
    `reaponta.decidir(now, trava.theta_a, trava.theta_t)`. A result is applied with `trava.reapontar(θ)` and shown on
    the ring with `anel.fixar_direcao(degrees)`. `Reaponta.zerar()` runs at each `fala_comecou`. Every caller
    `fala_comecou` also calls `trava.renovar()`.
  - **On `parada`:** `doa_regra.conversa_fechou()`, `self.dois_feixes = False`, detector stats reset.
  - **A wake word inside a two-beam conversation** (the word cutting her answer) re-aims beam A to that word's
    direction (`Direcao.decidir` with the TV exclusion, then `trava.reapontar`), without a new connection.
  - **Traceability (see the Traceability table).**
    - Print `two_beam_config` at start and on config change, and `two_beam_mode` at each connect.
    - Print `two_beam_energy` when the detector flips to or from pass-through because of stale energy.
    - Print `two_beam_turn_ceiling` when `df.parou_por_teto`.
    - Print `two_beam_dropped_candidate` from `detector.trecho_tv()` only under its rule (mic open, no turn in progress,
      no `caller` frame in the next 1 s). Every other TV run is only counted.
    - Print one `two_beam_summary` at `parada`.
    - Put the accumulation in a small pure class, `ResumoDoisFeixes` in `detectores.py`, fed by the program, with its
      own tests: `test_summary_counts_turns_by_reason`, `test_dropped_candidate_rule`.
    - Add to the wiring test `test_one_summary_line_per_conversation`: a 3-turn fake conversation prints exactly one
      `two_beam_summary` and no per-frame lines.

- [ ] **Step 1: Write failing tests.** Program wiring test, with a fake link, a fake connection and the maestro:
    - `test_two_beam_mode_decided_at_connect`: lock ok gives `pi_turns` in request_data and in the maestro.
    - `test_lock_failure_runs_as_today`: no `pi_turns`, no detector.
    - `test_speech_stopped_payload_has_stats_and_reason`.
- [ ] **Step 2: Run** the satellite suite. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.** Keep `friday_satelite.py` changes inside the existing methods (`_agir`, `captura`,
  `_doa_evento`, `_travar_na_palavra`); pull any new logic into `doa.py` or `detectores.py` helpers so it stays
  testable.
- [ ] **Step 4: Run** the whole satellite suite. Expected: OK.
- [ ] **Step 5: Commit** `feat(satellite): Pi-driven turns follow the caller's voice in two-beam conversations`.

### Task 6: "Learn TV direction" calibration (Pi)

**Files:**
- Modify: `satellite/program/doa.py` (pure routine), `satellite/program/comandos.py`,
  `satellite/program/friday_satelite.py`
- Test: `satellite/program/test_doa.py`, `satellite/program/test_comandos.py`

**Interfaces:**
- Produces:
  - `aprender_tv(link, agora, dormir, parar, historico_rad: float | None) -> dict` in `doa.py`, blocking. It is run in
    a thread by the program.
    - `agora()` and `dormir(s)` are injected; `parar()` returns True to abort.
    - Returns `{"result": "stored" | "refused", "reason": str | None, "azimuth_deg": float | None,
      "elevation_deg": int | None, "spread_deg": float | None, "frames": int}`.
  - The steps are spec D5:
    1. **Azimuth (20 s).** With `AEC_FIXEDBEAMSONOFF 0`, poll every 0.125 s with `read_bounded`, collecting
       `AEC_AZIMUTH_VALUES[2]` where `AEC_SPENERGY_VALUES[2] > 0`. Take the circular median; spread = 90th percentile of
       the circular distance to it.
    2. **Refusals.** `"too_few_frames"` if < 40 frames; `"spread"` if spread > 30°; `"near_caller"` if
       `historico_rad` is not None and within 45°.
    3. **Elevation.** For φ in 0, 15, …, 75: write azimuths `[θ, θ]`, elevations `[φ, φ]`, ON 1. Wait 3 s, polling
       `AEC_SPENERGY_VALUES[0]`, and keep the φ with the highest mean.
    4. **Cleanup (always, in `finally`).** ON 0, elevations `[0, 0]`, `OP_R 7 3`, `OP_L 8 0`.
    5. **On `parar()`:** result `"refused"`, reason `"aborted"`.
  - `comandos.executar` action `"learn_tv_direction"`. It raises `ValueError` unless `estado_maestro == "parada"`,
    then returns `{}` with the effect `("aprender_tv", None)`.
  - **Program effect.**
    - Run `aprender_tv` in a daemon thread, with `historico_rad = mediana_direcoes(...)`.
    - `parar` returns True as soon as the maestro leaves `parada`.
    - On `"stored"`: `cfg tv_azimuth_deg` / `tv_elevation_deg` via `cfgmod.validar_mudancas` + `cfgmod.gravar`, then
      `emissor.emitir("config", {"mudancas": {...}, "origem": "calibracao"})`.
    - Always: `emissor.emitir("tv_direction", result)`.
    - While it runs, `Regra.palavra` must not lock (the board is busy): add a `Regra.ocupada` flag the routine sets.
    - Journal: one `tv_direction` line at start (`historico_deg`) and one with the result, including
      `energy_by_elevation` (the 6 means, rounded). The routine returns that list so the program can print it.

- [ ] **Step 1: Write failing tests:**
  - `test_calibration_stores_median_and_best_elevation`: a fake link gives 200 polls around 12° with energy, and the
    elevation energy peaks at 45. Expect `stored`, 12, 45.
  - `test_calibration_refuses_spread` and `test_calibration_refuses_near_caller`.
  - `test_calibration_abort_releases`: `parar` turns True at the 3rd elevation, and the last writes are the cleanup
    sequence.
  - `test_calibration_always_ends_released`: a link write raises mid-sweep, and the cleanup still runs.
  - `test_calibration_refused_when_not_idle` (comandos).
- [ ] **Step 2: Run** the satellite suite. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the whole satellite suite. Expected: OK.
- [ ] **Step 5: Commit** `feat(satellite): learn the TV direction from the board`.

### Task 7: Satellite agent: entities, mapping, button, event

**Files:**
- Modify: `agents/satellite/friday_satellite_agent/capabilities.py`, `agents/satellite/friday_satellite_agent/__main__.py`
  (press routing and event forwarding)
- Test: the existing agent tests (`agents/satellite/tests/`; follow the tests of `beam_lock` and `ring_preview`)

**Interfaces:**
- Consumes: program config keys (Task 4), command `learn_tv_direction` (Task 6), program event `tv_direction`.
- Produces HA keys. Add them under `if board == "xvf3800"`, all `category="config"` except the event:
  - `two_beam_listening`: switch, `hidden=True`, icon `mdi:account-voice`.
  - `tv_azimuth`: number −1..359, step 1, unit "°", `hidden=True`. −1 = no TV; maps to `tv_azimuth_deg` None.
  - `tv_elevation`: number 0..80, step 5, unit "°", `hidden=True`.
  - `caller_margin` and `tv_margin`: number 0..20, step 0.5, unit "dB", `hidden=True`.
  - `caller_silence`: number 0.5..5, step 0.5, unit "s", `hidden=True`.
  - `turn_ceiling`: number 10..300, step 5, unit "s", `hidden=True`.
  - `learn_tv_direction`: button, **visible**, icon `mdi:television`.
  - `tv_direction_learn`: event, `event_types=["stored", "refused"]`, `category="diagnostic"`, icon `mdi:television`.
    Attributes are the routine's result dict with English keys.
- Mapping:
  - `to_program` maps each key to its program key. `tv_azimuth` −1 becomes None; `turn_ceiling`, `tv_azimuth` and
    `tv_elevation` are added to `INT_KEYS` for the int conversion.
  - The state reads them back, the same way.
  - `do_press("learn_tv_direction")` sends `program.command({"acao": "learn_tv_direction"})` and returns its result.

- [ ] **Step 1: Write failing tests:**
  - descriptors present only for `board="xvf3800"`;
  - `tv_azimuth` −1 ↔ None round trip;
  - press forwards the command;
  - the program event `tv_direction` becomes an HA event with `stored` / `refused`.
- [ ] **Step 2: Run** `bash scripts/test.sh`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `bash scripts/test.sh`. Expected: OK.
- [ ] **Step 5: Commit** `feat(agent): two-beam listening settings, TV direction button and event`.

### Task 8: Integration: Advanced dialog, texts, docs

**Files:**
- Modify:
  - `custom_components/friday/settings.py`: add the 7 HA keys of Task 7 to `ADVANCED_FIXED`, after
    `continuous_speech_limit`.
  - `custom_components/friday/__init__.py`: the key list at line ~88.
  - `custom_components/friday/strings.json`, `translations/en.json`, `translations/pt-BR.json`.
  - `docs/satellite-settings.md`.
- Test: the integration tests that check strings/translations consistency and the Advanced form (follow
  `continuous_speech_limit`).

Labels (HA style, English in `en.json` and `strings.json`; the pt-BR file keeps English labels, as today):
- "Two-beam listening"
- "TV direction"
- "TV elevation"
- "Caller margin"
- "TV margin"
- "Caller silence"
- "Turn ceiling"
- Button "Learn TV direction"
- Event "TV direction learn"

Short help texts. Exact copy: one sentence each, at most ~200 characters, like the 25/11 `continuous_speech_limit`
fix. Long explanations go to `docs/satellite-settings.md`.
- `two_beam_listening`: en "In a conversation, listens only to the person who called her: TV speech does not reach
  her or keep the conversation open. Needs Only the wake word interrupts." pt-BR "Na conversa, ouve só quem a chamou:
  a fala da TV não chega a ela nem segura a conversa. Exige Only the wake word interrupts."
- `tv_azimuth`: en "Direction of the TV as the board sees it, in degrees. -1 = no TV. Learn TV direction fills it in."
  pt-BR "Direção da TV vista pela placa, em graus. -1 = sem TV. O botão Learn TV direction preenche."
- `tv_elevation`: en "How high the TV is above the board, in degrees." pt-BR "Quanto a TV fica acima da placa, em
  graus."
- `caller_margin` / `tv_margin`: en "How much louder (dB) the caller's beam must be than the TV beam to count as the
  caller / as the TV." pt-BR "Quanto (dB) o feixe de quem chamou precisa superar o da TV para contar como quem chamou /
  como TV."
- `caller_silence`: en "Ends the turn after this long without the caller's voice, even if other sound goes on." pt-BR
  "Encerra o turno após esse tempo sem a voz de quem chamou, mesmo com outro som."
- `turn_ceiling`: en "Longest single turn; longer speech is split in two. The conversation goes on." pt-BR "Turno mais
  longo; fala maior é dividida em dois. A conversa continua."

In `docs/satellite-settings.md`, add a section "Two-beam listening", in the style of the existing one. Cover:
- what it does;
- when it falls back to v0.4.6 (no lock, no wake word, whisper, Jabra);
- the Learn button and its refusals;
- what the margins mean;
- the 60 s turn ceiling.

- [ ] **Step 1:** Add the failing expectations to the integration tests (the new keys in the Advanced form; strings
  present in all three files).
- [ ] **Step 2: Run** `bash scripts/test.sh`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `bash scripts/test.sh`. Expected: OK.
- [ ] **Step 5: Commit** `feat(integration): two-beam listening in the Advanced settings`.

### Task 9: Gateway: Pi-driven turns for `pi_turns` conversations, NDJSON fields

**Files:**
- Modify: `voice_gateway/satelite_modo.py`, `voice_gateway/bot.py`, `voice_gateway/whisper_mode.py`,
  `voice_gateway/registro_conversas.py`
- Test: `voice_gateway/test_satelite_modo.py`, `voice_gateway/test_whisper_mode.py`,
  `voice_gateway/test_registro_conversas.py`

**Interfaces:**
- Consumes: `request_data["pi_turns"]` (Task 4) and the message payloads of Task 5b.
- Produces:
  - `satelite_modo.pi_turns(body) -> bool`: satellite origin and `body.get("pi_turns") is True`, like `whisper(body)`.
  - **`bot.py`:** `turnos_pi_ativos = sussurro or (eh_satelite and sm.pi_turns(corpo))`. When true:
    - `GeminiVADParams(disabled=True)`;
    - `user_audio_preroll_secs=1.5`;
    - `PiSpeechTurns` created as for whispers.

    The whisper TTS stays only for `sussurro`. The `end_of_speech_pause_s` server-VAD setting is ignored when
    `pi_turns` (the Pi applies it).
  - **`PiSpeechTurns(..., preroll=None)`.** `preroll` = `(llm, primeiro_s, depois_s)`. After the first `stopped()` it
    sets `llm._user_audio_preroll_secs = depois_s` (0.5 for `pi_turns`; None for whispers, which keep 1.5). Add
    `test_pipecat_preroll_attribute_exists`: import `GeminiLiveLLMService` and check that `_user_audio_preroll_secs` is
    assigned in its `__init__` source. It guards a pipecat upgrade.
  - **Message handling.** Pass `msg.data` of `speech_started` / `speech_stopped` to `turnos.dois_feixes(data)` (new).
  - **`Turnos`:**
    - `dois_feixes(d: dict)` keeps the last `speech_stopped` payload of the turn and ORs `tv_active`;
    - `_linha(...)` gets a `two_beam=None` parameter and writes `"twoBeam"` when present, and `"turnMode": "pi"` for
      pi_turns conversations (`Turnos(..., pi_turns=False)`).

- **Traceability.**
  - Log `two-beam: session` at session build.
  - A small pure watcher `SemResposta(prazo_s=10)` in `whisper_mode.py`, fed with `stopped()`, model-output and
    tool-call events, logs `two-beam: no answer` once per turn. Test: `test_no_answer_logged_once_after_10s`.
  - Add the sat id to the existing stop-timeout warning.

- [ ] **Step 1: Write failing tests:**
  - `test_pi_turns_only_from_satellite_and_true`;
  - `test_first_turn_preroll_then_short`: a fake llm object; after the first stop, `_user_audio_preroll_secs == 0.5`;
  - `test_whisper_keeps_long_preroll`;
  - `test_two_beam_fields_in_line`;
  - `test_pipecat_preroll_attribute_exists`.
- [ ] **Step 2: Run** the gateway suite (docker step of `scripts/test-all.sh`; or run that single `ssh … docker run …`
  line). Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the gateway suite. Expected: OK.
- [ ] **Step 5: Commit** `feat(voice-gateway): Pi-driven turns for two-beam conversations`.

### Task 10: Speaker gate on the server agent (shadow backend)

**Files:**
- Create: `agents/server/friday_server_agent/speaker_gate.py`, `tools/speaker_gate/fetch_model.sh`
- Modify: `agents/server/requirements.lock` (add `numpy==2.4.6`, `sherpa-onnx==2.0.3`, `sherpa-onnx-core==2.0.3`),
  `agents/server/friday_server_agent/__main__.py` (register routes next to `negmod.add_routes`)
- Test: `agents/server/tests/test_speaker_gate.py`. Fake the extractor; the real model is never needed in CI.
  `scripts/test.sh` runs these tests in `python:3.14` with `requirements_test.lock`, where `sherpa-onnx` may not
  install: import `sherpa_onnx` (and `numpy`) only inside the default extractor factory, never at module top.
  `agents/server/install.sh` installs `agents/server/requirements.lock` (its `cd` lands in `agents/`).

**Interfaces:**
- Produces:
  - **Constants:** `MODEL = "/srv/friday/state/speaker-gate/eres2net_base_16k.onnx"`,
    `ENROLL_DIR = "/srv/friday/state/speaker-gate/enroll"`, `WIN_S = 1.5`, `HOP_S = 0.5`. Overridable by env
    `FRIDAY_SPEAKER_GATE_DIR`.
  - **`class SpeakerGate(extractor_factory=None)`:** lazy model load. If the model file is missing, `available` is
    False and every call returns `{"available": False}`.
  - **`enroll(sat_id: str, voices_root: str) -> dict[str, int]`.**
    - For each person folder in `<voices_root>/<sat_id>/vozes/`, use every `.wav` (16 kHz mono) except folders named
      `jabra-speak-410`: enrollment must be the satellite's XVF audio (spec D7).
    - Mean of L2-normalised window embeddings, written as JSON `{"dim", "vector"}` to
      `<ENROLL_DIR>/<sat_id>/<person>.json`.
    - Returns windows per person.
  - **`score(sat_id: str, pcm16: bytes) -> dict`.** 16 kHz mono int16, 1.5–3 s. Returns `{"available": True,
    "scores": {person: cosine}, "best": person | None, "score": float | None}`. No enrollment gives `best` None.
  - **CLI:** `python3 -m friday_server_agent.speaker_gate enroll --sat <id> [--voices-root
    /srv/wakeword-data/incoming]`.
  - **Route** `POST /v1/speaker_gate/score`, body `{"sat_id", "pcm16_b64"}`.
    - Only from `127.0.0.1` (check `request.remote`); 403 otherwise.
    - 400 on bad input (sat id must match the existing `SAT_ID` regex; audio 0.5–3 s).
    - The work runs in `asyncio.to_thread`.
  - **Logs:** the enroll CLI prints one summary line (person → windows). Route errors are logged as
    `speaker gate: <ErrorClass>` at most once per minute.
  - **`tools/speaker_gate/fetch_model.sh`.**
    - Downloads `https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx`
      to `MODEL`.
    - Verifies sha256 `f1ee61be04000b50392a63442e5dd9feaea7e81dddd494d3647cf1c3d88a1498`; on mismatch it deletes the
      file and exits 1.
    - Idempotent.

- [ ] **Step 1: Write failing tests:**
  - `test_score_unavailable_without_model`;
  - `test_enroll_skips_jabra_folders_and_writes_mean`: a fake extractor returns fixed vectors per file;
  - `test_score_picks_best_person`;
  - `test_route_refuses_non_localhost`;
  - `test_route_rejects_bad_sat_id_and_length`.
- [ ] **Step 2: Run** `bash scripts/test.sh`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `bash scripts/test.sh`. Expected: OK.
- [ ] **Step 5: Commit** `feat(server-agent): speaker gate backend (shadow)`.

### Task 11: Gateway speaker-gate client (shadow only)

**Files:**
- Create: `voice_gateway/speaker_gate_client.py`
- Modify: `voice_gateway/whisper_mode.py` (`PiSpeechTurns` hands the window's audio to an optional sink),
  `voice_gateway/bot.py`, `voice_gateway/registro_conversas.py`
- Test: `voice_gateway/test_speaker_gate_client.py`

**Interfaces:**
- Consumes: Task 10's route at `http://127.0.0.1:8801/v1/speaker_gate/score`, and Task 9's `PiSpeechTurns` /
  `Turnos`.
- Produces:
  - `class SombraPortao(sat_id, post, limiar=0.44)`. `post(body) -> dict` is an injected async callable. 0.44 comes from
    the 26/11 study (threshold at 5% TV accepted, single window).
    - `audio(pcm: bytes)`: buffers; every 1.5 s of buffered window audio, it schedules one non-blocking score call
      (timeout 1 s; a failure is ignored and counted).
    - `fim_do_turno() -> dict | None`: the turn's record `{"windows": [scores…], "best": …, "callerMatch": …,
      "wouldEnd": bool, "errors": n}`.
  - **Rules (spec D7, shadow):**
    - **The first turn of the conversation** sets `callerMatch` = the best person when any window scores ≥ `limiar`.
    - **Later turns** are scored only when the Pi's `speech_started` had `tv_active` true.
    - **`wouldEnd`** is True when `callerMatch` is set and 2 consecutive windows of the turn score below `limiar` for
      `callerMatch`.
    - **It never acts:** no frame is sent, nothing is cut.
  - **Traceability:** log `two-beam: speaker gate unavailable` once per process (on `available: false`, or after 3
    consecutive failures). Test: `test_unavailable_logged_once`.
  - **Wiring.** `PiSpeechTurns(..., sink=SombraPortao.audio)` while `_falando`. `bot.py` creates it only for
    `pi_turns` conversations. `Turnos.portao_voz(d)` writes `"speakerGate"` in the line.

- [ ] **Step 1: Write failing tests:**
  - `test_first_turn_sets_caller_match`;
  - `test_would_end_after_two_low_windows`;
  - `test_never_scores_without_tv_active_after_first_turn`;
  - `test_post_failure_counted_not_raised`;
  - `test_speaker_gate_field_in_line`.
- [ ] **Step 2: Run** the gateway suite. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the gateway suite. Expected: OK.
- [ ] **Step 5: Commit** `feat(voice-gateway): speaker gate verdicts recorded in shadow`.

### Task 12: Measurement tools (telemetry report and controlled session)

**Files:**
- Add (already written, uncommitted): `tools/satellite_measurements/xvf_telemetry.py`
- Create: `tools/satellite_measurements/xvf_telemetry_report.py`, `tools/satellite_measurements/two_beam_session.py`
- Test: `tools/satellite_measurements/test_xvf_telemetry_report.py`, run by `scripts/test.sh` if that script picks up
  `tools/`. Check, and add it to the tools step if not.

**Interfaces:**
- **`xvf_telemetry_report.py --telemetry DIR --tv-history FILE.json [--conversations DIR] --out FILE.json`.** Pure
  functions, read-only. Inputs: the telemetry NDJSON from `xvf_telemetry.py`, an HA history JSON
  (`/api/history/period` output, one entity) and, optionally, the conversation NDJSON. It reports:
  - `tv`: median TV azimuth (deg) and 90th-percentile spread. Source: free-running `az[2]` on records with
    `fx == 0`, `e[2] > 0` and the TV on.
  - `callers`: median and spread of `az[0]` on records with `fx == 1`.
  - `separation_deg`: the circular distance between the two.
  - `autoselect_follows_free_in_fixed`: on `fx == 1` records, the fraction where `az[3]` is within 5° of `az[2]` and
    more than 20° from `az[0]`.
  - `energy`: distribution of 10·log10(e[0]/e[1]) on `fx == 1` records with both > 0.
  - Each figure comes with its sample count.
  - **`--journal FILE`** (a `journalctl -o cat` export): conversations per `two_beam_mode.reason`; turn ends per `by`;
    `two_beam_turn_ceiling` count; `two_beam_dropped_candidate` count and their median ratio; re-aims per
    conversation; total `energy_stale_s`; releases per reason. Test: `test_journal_summary_counts`.
- **`two_beam_session.py`.** The controlled session (spec D9), run on the Pi with the satellite program stopped.
  - Interactive prompts in Portuguese.
  - Steps:
    1. TV alone, 60 s: azimuth.
    2. Elevation sweep, as in `aprender_tv`.
    3. Owner speaking from the sofa without TV, 60 s.
    4. The same with the TV on, with beam A on the owner and beam T on the TV.
  - It records `arecord -D hw:CARD=Array -f S16_LE -r 48000 -c 2` WAVs per step and logs the registers with
    `xvf_telemetry.Board`.
  - It always ends with the cleanup sequence of Task 6.
  - It writes into a folder given by `--out`.
  - No unit test, beyond a `--dry-run` that prints the steps; the cleanup is shared code from `doa.py`.

- [ ] **Step 1: Write failing tests** for the report:
  - `test_tv_azimuth_from_free_beam_when_tv_on`;
  - `test_autoselect_follow_fraction`;
  - `test_ignores_records_without_energy`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** the report and the session script.
- [ ] **Step 4: Run.** Expected: OK.
- [ ] **Step 5: Commit** `feat(tools): two-beam telemetry report and controlled session`, adding `xvf_telemetry.py`
  too.

### Task 13: Full suite, spec cross-check, PR

**Files:** none new.

- [ ] **Step 1:** `bash scripts/test-all.sh` from the worktree. Expected: `ALL GREEN`. Paste the tail into the report.
- [ ] **Step 2:** Re-read the spec's Goals, Anti-goals and D1–D9. List any requirement without a commit, and fix it or
  report it to the controller.
- [ ] **Step 3:** `git fetch origin && git rebase origin/master` (memory: pull master before the PR), then rerun
  `scripts/test-all.sh`.
- [ ] **Step 4:** Report to the controller. The controller opens the PR as **draft**, with a "Merge gate". The live
  test on the MK1 alpha and on the MK2 is pending, and the release notes are written at release time with the
  `writing-friday-release-notes` skill.
