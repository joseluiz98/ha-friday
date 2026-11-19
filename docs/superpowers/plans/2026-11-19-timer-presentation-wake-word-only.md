# Timer and presentation: only an active wake word interrupts — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the v0.3.7 "recado" gate (mic closed while she speaks, only an active wake word cuts her) to the timer conversation and to every speech of the presentation, and make the gate start at connect.

**Architecture:** Pure state-machine change in `satellite/program/maestro.py` (`Maestro`), reusing `_fechar_mic_do_recado`, `_recado`, `_recado_fim` and `RECADO_PAUSA_S`. The program (`friday_satelite.py`) already executes `("microfone", bool)` and `("parar_fala", None)`; it only gets a wiring test.

**Tech Stack:** Python 3, `unittest` (satellite program tests run locally on the HA box).

**Spec:** `docs/superpowers/specs/2026-11-19-timer-presentation-wake-word-only-design.md` (extends `2026-11-16-delivery-barge-in-wakeword-design.md`).

## Global Constraints

- Worktree `/config/dev/ha-friday-wt-quiet`, branch `feat/satellite-notifications-wakeword-only` (from `origin/master` ab21c50).
- Run satellite tests only with a private TMPDIR: `cd satellite/program && T=/root/.claude/jobs/47cf0b42/tmp/tq && mkdir -p $T && TMPDIR=$T python3 -m unittest -q <modules>; rm -rf $T`.
- Baseline: `test_maestro test_maestro_alertas test_satelite_ligacao test_maestro_wake_word` = 327 OK.
- Code comments in Portuguese like the surrounding code, tagged `19/11`; git messages in English; never `--no-verify`; commits end with the session's Co-Authored-By / Claude-Session lines.
- `RECADO_PAUSA_S` stays 1.5. "Active wake word" = any word the Pi reports as `palavra` (shadow words never reach `evento("palavra")`; unchanged).
- No change to conversations opened by `palavra`/`botao`, to the musical alarm, wake-word test or enrollment.
- `palavra_agiria()` must keep mirroring `_trata` for `palavra` (`test_palavra_agiria_matches_evento`).

## Review Focus

1. Her speech never starts (Live sends nothing): the gate must not leave the conversation stuck; the existing `espera_inicial` deadline still closes it (test in Task 1).
2. Room speech before her first word in a timer conversation must not count as "answered" (test in Task 2).
3. A wake word cutting the timer speech must count as answered, so the alarm does not come back (test in Task 2).
4. Presentation: the person answers, she replies — the gate must close again on each new speech, including after a pause > 1.5 s inside one reply (test in Task 3).
5. Wake word arriving while the presentation mic is already open (she is waiting) must behave as today (no `parar_fala`) (test in Task 3).

---

### Task 1: The gate starts at connect for every Pi-opened conversation

**Files:**
- Modify: `satellite/program/maestro.py` (`_fechar_mic_do_recado`, ~line 606)
- Test: `satellite/program/test_maestro.py` (class `TestRecadoSoAPalavraInterrompe`), `satellite/program/test_satelite_ligacao.py` (class `TestPararFala`)

**Interfaces:**
- Produces: `_fechar_mic_do_recado(acoes: list) -> list` — as today, plus: when `acoes` contains a `("conectar", x)` action and no `("microfone", …)` action, insert `("microfone", False)` immediately after the `conectar`. Tasks 2 and 3 rely on this.

- [ ] **Step 1: Write the failing tests**

In `TestRecadoSoAPalavraInterrompe`:
```python
def test_pi_delivery_mic_closed_from_connect(self):  # 19/11
    self.m.mudo = False
    _, acoes = self.m.campainha("e1abcdef", 0.0)
    i = [a[0] for a in acoes].index("conectar")
    self.assertEqual(acoes[i + 1], ("microfone", False))

def test_delivery_without_her_speech_still_closes_by_deadline(self):  # 19/11, Review Focus 1
    self.m.mudo = False
    self.m.campainha("e1abcdef", 0.0)
    self.m.evento("conectou", 0.5)
    acoes = self.m.evento("tique", 0.5 + self.m.a.resposta_max_s + 0.1)
    self.assertIn("desconectar", [a[0] for a in acoes])
```
In `test_satelite_ligacao.py`, `TestPararFala`:
```python
async def test_pi_delivery_connects_with_the_mic_closed(self):  # 19/11
    self.s.maestro.mudo = False
    _, acoes = self.s.maestro.campainha("e1abcdef", 0.0)
    for tipo, arg in acoes:
        self.s._agir(tipo, arg)
    self.assertFalse(self.s.microfone_aberto)
```
(If `_agir` of `som`/`volume_alerta` needs a fake not in `Falsos`, apply only `conectar` and `microfone` from the list, in order.)

- [ ] **Step 2: Run, expect FAIL** — the two connect tests fail (`microfone` missing / `microfone_aberto` True); the deadline test may already pass (it is a guard).

- [ ] **Step 3: Implement** the insertion in `_fechar_mic_do_recado` (comment: `19/11: conectar abre o microfone; o recado já começa fechado`).

- [ ] **Step 4: Run** the four baseline modules. Expected: all OK (329+). Existing tests that assert the exact action list of a Pi delivery (`test_maestro_alertas.TestEntrega`, `test_pi_delivery_light_says_speaking`) may need `("microfone", False)` added after `conectar`: update the expectation, nothing else.

- [ ] **Step 5: Commit** `fix(satellite): Pi-opened conversations connect with the microphone closed`

### Task 2: Timer conversation is gated; only answers heard after the mic opens count

**Files:**
- Modify: `satellite/program/maestro.py` (`_abrir_timer` ~277; the "timer atendido" check ~458)
- Test: `satellite/program/test_maestro_alertas.py` (new class `TestTimerSoAPalavraInterrompe(Base)`; update `TestAlarme`, `TestAlarmeMusical` expectations)

**Interfaces:**
- Consumes: `_fechar_mic_do_recado` (Task 1).
- Produces: `_abrir_timer(agora) -> list` returns `[("alarme","pausar")] + _fechar_mic_do_recado(_abrir_propria(...))`: light `falando`, `("microfone", False)` after `conectar`, `_recado = True`.

- [ ] **Step 1: Write the failing tests** (use `Base.conversa_do_timer`; set `self.m.mudo = False` in `setUp`):
```python
def test_timer_opens_speaking_with_mic_closed(self):
    acoes = self.conversa_do_timer()
    self.assertEqual(nomes(acoes), ["alarme:pausar", "luz:falando", "conectar", "microfone:False"])

def test_room_speech_does_not_cut_the_timer(self):
    self.conversa_do_timer(); self.m.evento("ela_comecou", 3.0)
    a = self.m.evento("fala_comecou", 3.5) + self.m.evento("fala_parou", 4.0)
    self.assertNotIn("parar_fala", nomes(a)); self.assertNotIn("microfone:True", nomes(a))

def test_active_word_cuts_the_timer_and_counts_as_answered(self):  # Review Focus 3
    self.conversa_do_timer(); self.m.evento("ela_comecou", 3.0)
    self.assertIn("parar_fala", nomes(self.m.evento("palavra", 3.5, {"wake_word": "friday"})))
    acoes = self.m.evento("encerrar", 6.0)
    self.assertEqual(achar(acoes, "registrar_timer")["como_parou"], "atendido")

def test_room_speech_while_she_speaks_is_not_an_answer(self):  # Review Focus 2
    self.conversa_do_timer()
    self.m.evento("fala_comecou", 2.8); self.m.evento("fala_parou", 2.9)   # before her first word
    self.m.evento("ela_comecou", 3.0)
    self.m.evento("fala_comecou", 3.5); self.m.evento("fala_parou", 4.0)   # while she speaks
    self.m.evento("ela_parou", 5.0); self.m.evento("tique", 6.6)           # mic opens
    acoes = self.m.evento("encerrar", 7.0)
    self.assertNotIn("alarme:parar", nomes(acoes)); self.assertEqual(self.m.estado, "alarme")

def test_mic_opens_after_the_timer_speech(self):
    self.conversa_do_timer(); self.m.evento("ela_comecou", 3.0); self.m.evento("ela_parou", 5.0)
    self.assertIn("microfone:True", nomes(self.m.evento("tique", 6.6)))
```

- [ ] **Step 2: Run** `test_maestro_alertas`. Expected: the new tests FAIL.

- [ ] **Step 3: Implement**
  - `_abrir_timer`: wrap `_abrir_propria(...)` in `_fechar_mic_do_recado`.
  - The atendido check (`if self._alarme and self._da_conversa("timer") and nome in ("fala_comecou", "botao")`): `fala_comecou` counts only when `not self._recado`; a `palavra` that cuts the recado (the branch `if self._recado and e in ABERTO and self.mudo is False` under `nome == "palavra"`) also sets `self._alarme["atendido"] = True` when it is the timer conversation.

- [ ] **Step 4: Run** the four modules. Update, with a one-line `# 19/11` note each, the existing expectations that assumed an open mic:
  - `["alarme:pausar", "luz:ouvindo", "conectar"]` → `["alarme:pausar", "luz:falando", "conectar", "microfone:False"]` (TestAlarme line ~40, TestAlarmeMusical ~246/253/264).
  - `test_alguem_fala_na_conversa_do_timer_e_o_alarme_para`: add `self.m.evento("tique", 6.6)` after `ela_parou` and move the speech to 7.0/8.0 (the answer must come after the mic opens).
  - `test_quem_fala_e_depois_manda_parar_para_o_alarme`: insert `ela_comecou 3.0`, `ela_parou 3.5`, `tique 5.1` before the speech (speech before her first word no longer counts — spec).
  Expected: all OK.

- [ ] **Step 5: Commit** `feat(satellite): timer speech is gated; only an active wake word interrupts`

### Task 3: Presentation gates every speech of hers

**Files:**
- Modify: `satellite/program/maestro.py` (`abrir_apresentacao` branch ~429; `ela_comecou` branch ~537)
- Test: `satellite/program/test_maestro.py` (class `TestApresentacao`)

**Interfaces:**
- Consumes: `_fechar_mic_do_recado` (Task 1); `_da_conversa("apresentacao")`.

- [ ] **Step 1: Write the failing tests** (in `TestApresentacao`, helper `abrir()` = `evento("abrir_apresentacao", 0.0, {"areas": []})` + `evento("conectou", 0.5)`):
```python
def test_every_speech_of_hers_closes_the_mic(self):
    self.abrir()
    self.assertIn(("microfone", False), self.m.evento("ela_comecou", 1.0))
    self.m.evento("ela_parou", 3.0)
    self.assertIn(("microfone", True), self.m.evento("tique", 4.6))
    self.m.evento("fala_comecou", 5.0); self.m.evento("fala_parou", 6.0)    # the person answers
    self.assertIn(("microfone", False), self.m.evento("ela_comecou", 7.0))   # her next question: closed again

def test_room_speech_does_not_cut_the_presentation(self):
    self.abrir(); self.m.evento("ela_comecou", 1.0)
    a = self.m.evento("fala_comecou", 1.5) + self.m.evento("fala_parou", 2.0)
    self.assertNotIn(("parar_fala", None), a)

def test_active_word_cuts_the_presentation(self):
    self.abrir(); self.m.evento("ela_comecou", 1.0)
    self.assertIn(("parar_fala", None), self.m.evento("palavra", 1.5, {"wake_word": "friday"}))

def test_pause_inside_one_reply_keeps_the_mic_closed(self):  # Review Focus 4
    self.abrir(); self.m.evento("ela_comecou", 1.0); self.m.evento("ela_parou", 2.0)
    self.m.evento("ela_comecou", 2.5)
    self.assertNotIn(("microfone", True), self.m.evento("tique", 4.4))

def test_word_while_she_waits_behaves_as_today(self):  # Review Focus 5
    self.abrir(); self.m.evento("ela_comecou", 1.0); self.m.evento("ela_parou", 2.0); self.m.evento("tique", 3.6)
    self.assertEqual(self.m.evento("palavra", 4.0, {"wake_word": "friday"}), [])
```
Update `test_abre_conversa_propria_sem_som_de_lembrete` to expect `[("luz", "falando"), ("conectar", {"presentation": {"areas": []}}), ("microfone", False)]`.

- [ ] **Step 2: Run** `test_maestro`. Expected: new tests FAIL.

- [ ] **Step 3: Implement**
  - `abrir_apresentacao`: return `self._fechar_mic_do_recado(self._abrir_propria(...))`.
  - `ela_comecou`: alongside `if e == AGUARDANDO: self._recado = True`, also set `self._recado = True` when `e in ABERTO and self._da_conversa("apresentacao")` (comment `19/11: na apresentação toda fala dela é recado`).

- [ ] **Step 4: Run** the four modules. Expected: all OK.

- [ ] **Step 5: Commit** `feat(satellite): the presentation gates every speech of hers`

### Task 4: Pointer from the v0.3.7 spec (house docs, outside the repo)

- [ ] Append to `/config/docs/superpowers/specs/2026-11-16-delivery-barge-in-wakeword-design.md` (back it up first as `.bak.20261119`): `## Extensão (19/11)` + one line: "Timer, apresentação (toda fala dela) e o portão desde o `conectar`: ha-friday `docs/superpowers/specs/2026-11-19-timer-presentation-wake-word-only-design.md`." Not a repo commit; release notes are written at release time, not here.
