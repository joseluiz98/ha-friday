# Fine Satellite Volume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Tasks 1-6 are written for a small model: the code below is exact. Copy it; do not redesign. If a step does not behave as written, report BLOCKED with the output instead of improvising.

**Goal:** Music Assistant (MA) volume works again on MK1 and MK2, and every percent 1-100 has its own loudness.

**Architecture:** House: one `input_number` mirror per satellite, bound both ways to the integration's volume entity. Satellite: the board's own level (Jabra: 12 steps of 4 dB; XVF3800: 61 raw levels of 1 dB) sits at or above the asked loudness and a new ALSA softvol `Fine` attenuates the rest.

**Tech Stack:** Home Assistant YAML, Python 3 stdlib, ALSA `asound.conf`, bash.

**Spec:** `docs/superpowers/specs/2026-11-27-fine-volume-design.md`

## Global Constraints

- Repo `/config/dev/ha-friday`, branch `feat/fine-volume`. Task 1 edits files under `/config` outside the repo.
- Git in English, one commit per task, never push. Commit messages end with the two attribution lines given in the dispatch.
- Never read or print `satellites.json`, tokens, `local.env`, `secrets.yaml` or a Pi's `config.json`. Never put IPs or satellite ids in repo files (a hook refuses the commit).
- Do not run `ssh`, `deploy.sh`, `ha`, or any command that changes the house or a satellite. Tasks only edit files and run tests.
- Program tests: `cd satellite/program && python3 -m unittest -q <modules>` (the full suite takes ~90 s; baseline 1211 OK, 10 skipped). Agent and repo tests: `bash scripts/test.sh <paths>`.
- Volume is never a mute. `Music` stays the ducking control. Sendspin's own volume stays at 100.

## Review Focus

1. MA slider dragged with music: never jumps back, never overshoots (Task 1 conditions, Task 3 `test_never_louder_than_old_or_new`).
2. Program restart or satellite offline: mirror keeps its value (Task 1 `test_unavailable_number_is_ignored`).
3. Nightly HA restart: the restored mirror never overrides the satellite (Task 1 `test_restore_is_not_sent`).
4. Rollback, volume changed on the old program, update again: the newer change wins (Task 5 `test_rollback_change_wins`).
5. Board replugged or program restarted: `Fine` and the board level are rewritten together (Task 4 `iniciar` → `forcar`).

---

### Task 1: House mirrors, files only

**Files (outside the repo, no commit):**
- Rewrite: `/config/packages/friday_satellite_volume.yaml`
- Rewrite: `/config/tools/friday_music/test_satellite_volume_package.py`

- [ ] **Step 1:** copy both files to `<file>.bak.20261127_fine_volume`. Open nothing else.
- [ ] **Step 2:** read `/config/docs/claude/fine-volume-house-values.md`. In the two files, write the real 6-hex-digit values where this task shows `<MK1_SHORT>` and `<MK2_SHORT>`.
- [ ] **Step 3: test file** (complete content):

```python
"""Teste estático do package friday_satellite_volume.yaml (espelhos de volume, spec 2026-11-27 fine-volume).
cd /config/tools/friday_music && python3 -m unittest -q"""
import unittest
import yaml

CAMINHO = "/config/packages/friday_satellite_volume.yaml"
PAIRS = {"office": ("input_number.friday_satellite_volume", "number.friday_sat_<MK1_SHORT>_volume"),
         "living_room": ("input_number.friday_satellite_living_room_volume", "number.friday_sat_<MK2_SHORT>_volume")}


class TestVolumeMirrors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(CAMINHO) as f:
            cls.texto = f.read()
        cls.d = yaml.safe_load(cls.texto)
        cls.auto = {a["id"]: a for a in cls.d["automation"]}

    def pair(self, room):
        mirror, number = PAIRS[room]
        return (mirror, number, self.auto[f"friday_volume_{room}_to_mirror"],
                self.auto[f"friday_volume_{room}_to_satellite"])

    def cond(self, a):
        return str(a["conditions"][0]["value_template"])

    def test_mirrors_are_percent_0_100(self):
        for mirror, _ in PAIRS.values():
            n = self.d["input_number"][mirror.split(".")[1]]
            self.assertEqual((n["min"], n["max"], n["step"]), (0, 100, 1))

    def test_no_secret_ip_webhook_or_rest_command(self):
        for proibido in ("!secret", "192.168.", "webhook", "rest_command"):
            self.assertNotIn(proibido, self.texto)

    def test_four_automations_bind_both_ways(self):
        self.assertEqual(len(self.auto), 4)
        for room in PAIRS:
            mirror, number, to_mirror, to_sat = self.pair(room)
            self.assertEqual(to_mirror["triggers"][0]["entity_id"], number)
            self.assertIn("input_number.set_value", str(to_mirror["actions"]))
            self.assertIn(mirror, str(to_mirror["actions"]))
            self.assertEqual(to_sat["triggers"][0]["entity_id"], mirror)
            self.assertIn("number.set_value", str(to_sat["actions"]))
            self.assertIn(number, str(to_sat["actions"]))

    def test_to_mirror_settles_1s_and_reconciles_3s(self):
        for room in PAIRS:
            mirror, number, to_mirror, _ = self.pair(room)
            t = {x["id"]: x for x in to_mirror["triggers"]}
            self.assertEqual((t["settled"]["entity_id"], t["settled"]["for"]), (number, {"seconds": 1}))
            self.assertEqual((t["reconcile"]["entity_id"], t["reconcile"]["for"]), (mirror, {"seconds": 3}))

    def test_stale_value_cannot_pull_a_drag_back(self):
        for room in PAIRS:
            mirror, _, to_mirror, _ = self.pair(room)
            c = self.cond(to_mirror)
            self.assertIn("trigger.id == 'reconcile'", c)
            self.assertIn(f"states.{mirror}.last_changed", c)
            self.assertIn(">= 1", c)

    def test_both_write_only_when_values_differ(self):
        for room in PAIRS:
            mirror, number, a, b = self.pair(room)
            for x in (a, b):
                self.assertIn(mirror, self.cond(x))
                self.assertIn(number, self.cond(x))
                self.assertIn("| float(-1)) != (m | float(-1))", self.cond(x))

    def test_restore_is_not_sent(self):
        for room in PAIRS:
            c = self.cond(self.pair(room)[3])
            self.assertIn("trigger.from_state is not none", c)
            self.assertIn("trigger.from_state.state not in ['unknown', 'unavailable']", c)

    def test_unavailable_number_is_ignored(self):
        for room in PAIRS:
            for x in self.pair(room)[2:]:
                self.assertIn("n not in ['unknown', 'unavailable']", self.cond(x))

    def test_latest_wins(self):
        self.assertTrue(all(a["mode"] == "restart" for a in self.auto.values()))

    def test_no_cross_wiring(self):
        for room, other in (("office", "living_room"), ("living_room", "office")):
            for x in self.pair(room)[2:]:
                texto = yaml.safe_dump(x)
                for entidade in PAIRS[other]:
                    self.assertNotRegex(texto, entidade.replace(".", r"\.") + r"(?![a-z0-9_])")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 4:** run `cd /config/tools/friday_music && python3 -m unittest -q test_satellite_volume_package`. Expected: FAIL.
- [ ] **Step 5: package** (complete content):

```yaml
# Espelhos de volume dos satélites (spec ha-friday 2026-11-27-fine-volume). O Music Assistant usa o input_number de
# cada satélite como "Volume control" do player. Cada espelho é ligado nos dois sentidos à entidade de volume da
# integração F.R.I.D.A.Y. Sem senha e sem IP.
#   satélite -> espelho: o número parado 1 s; e uma segunda conferência quando o espelho fica parado 3 s.
#   espelho -> satélite: mudança do espelho diferente do número. A restauração no arranque do HA não vai.
# O satélite é a verdade. Com o número indisponível nada é enviado e o espelho guarda o último valor.
# Teste: cd /config/tools/friday_music && python3 -m unittest -q

input_number:
  friday_satellite_volume:
    name: Volume do satélite do escritório
    min: 0
    max: 100
    step: 1
    unit_of_measurement: "%"
    mode: slider
    icon: mdi:speaker
  friday_satellite_living_room_volume:
    name: Volume do satélite da sala
    min: 0
    max: 100
    step: 1
    unit_of_measurement: "%"
    mode: slider
    icon: mdi:speaker

automation:
  - id: friday_volume_office_to_mirror
    alias: Satélite escritório – volume para o espelho
    mode: restart
    triggers:
      - trigger: state
        id: settled
        entity_id: number.friday_sat_<MK1_SHORT>_volume
        to: null
        for:
          seconds: 1
      - trigger: state
        id: reconcile
        entity_id: input_number.friday_satellite_volume
        to: null
        for:
          seconds: 3
    conditions:
      - condition: template
        value_template: >-
          {% set n = states('number.friday_sat_<MK1_SHORT>_volume') %}
          {% set m = states('input_number.friday_satellite_volume') %}
          {{ n not in ['unknown', 'unavailable'] and (n | float(-1)) != (m | float(-1))
             and (trigger.id == 'reconcile'
                  or as_timestamp(now()) - as_timestamp(states.input_number.friday_satellite_volume.last_changed) >= 1) }}
    actions:
      - action: input_number.set_value
        target:
          entity_id: input_number.friday_satellite_volume
        data:
          value: "{{ states('number.friday_sat_<MK1_SHORT>_volume') | float }}"

  - id: friday_volume_office_to_satellite
    alias: Satélite escritório – volume para o satélite
    mode: restart
    triggers:
      - trigger: state
        entity_id: input_number.friday_satellite_volume
        to: null
    conditions:
      - condition: template
        value_template: >-
          {% set n = states('number.friday_sat_<MK1_SHORT>_volume') %}
          {% set m = states('input_number.friday_satellite_volume') %}
          {{ trigger.from_state is not none
             and trigger.from_state.state not in ['unknown', 'unavailable']
             and m not in ['unknown', 'unavailable']
             and n not in ['unknown', 'unavailable']
             and (n | float(-1)) != (m | float(-1)) }}
    actions:
      - delay:
          milliseconds: 150
      - action: number.set_value
        target:
          entity_id: number.friday_sat_<MK1_SHORT>_volume
        data:
          value: "{{ states('input_number.friday_satellite_volume') | float | round(0) | int }}"

  - id: friday_volume_living_room_to_mirror
    alias: Satélite sala – volume para o espelho
    mode: restart
    triggers:
      - trigger: state
        id: settled
        entity_id: number.friday_sat_<MK2_SHORT>_volume
        to: null
        for:
          seconds: 1
      - trigger: state
        id: reconcile
        entity_id: input_number.friday_satellite_living_room_volume
        to: null
        for:
          seconds: 3
    conditions:
      - condition: template
        value_template: >-
          {% set n = states('number.friday_sat_<MK2_SHORT>_volume') %}
          {% set m = states('input_number.friday_satellite_living_room_volume') %}
          {{ n not in ['unknown', 'unavailable'] and (n | float(-1)) != (m | float(-1))
             and (trigger.id == 'reconcile'
                  or as_timestamp(now()) - as_timestamp(states.input_number.friday_satellite_living_room_volume.last_changed) >= 1) }}
    actions:
      - action: input_number.set_value
        target:
          entity_id: input_number.friday_satellite_living_room_volume
        data:
          value: "{{ states('number.friday_sat_<MK2_SHORT>_volume') | float }}"

  - id: friday_volume_living_room_to_satellite
    alias: Satélite sala – volume para o satélite
    mode: restart
    triggers:
      - trigger: state
        entity_id: input_number.friday_satellite_living_room_volume
        to: null
    conditions:
      - condition: template
        value_template: >-
          {% set n = states('number.friday_sat_<MK2_SHORT>_volume') %}
          {% set m = states('input_number.friday_satellite_living_room_volume') %}
          {{ trigger.from_state is not none
             and trigger.from_state.state not in ['unknown', 'unavailable']
             and m not in ['unknown', 'unavailable']
             and n not in ['unknown', 'unavailable']
             and (n | float(-1)) != (m | float(-1)) }}
    actions:
      - delay:
          milliseconds: 150
      - action: number.set_value
        target:
          entity_id: number.friday_sat_<MK2_SHORT>_volume
        data:
          value: "{{ states('input_number.friday_satellite_living_room_volume') | float | round(0) | int }}"
```

- [ ] **Step 6:** run the test again. Expected: OK. Report DONE. Do not load anything in Home Assistant.

---

### Task 2: Volume scale

**Files:** Create `satellite/program/escala.py`, `satellite/program/test_escala.py`.

- [ ] **Step 1: `test_escala.py`:**

```python
"""cd satellite/program && python3 -m unittest -q test_escala"""
import unittest
import escala as e

PLACAS = ("jabra", "xvf3800")


class TestEscala(unittest.TestCase):
    def test_grid(self):
        self.assertEqual([e.grade(k) for k in range(12)], [0, 9, 18, 27, 36, 45, 55, 64, 73, 82, 91, 100])

    def test_grid_percents_keep_todays_loudness(self):
        for placa in PLACAS:
            for k in range(12):
                p = e.grade(k)
                self.assertEqual((e.posicao_de_pct(p), e.alvo_db(p, placa), e.trim_db(p, placa)),
                                 (k, e.ANCORAS[placa][k], 0))

    def test_loudness_strictly_rises(self):
        for placa in PLACAS:
            vals = [e.NIVEIS[placa][e.nivel_de_pct(p, placa)] + e.trim_db(p, placa) for p in range(101)]
            self.assertTrue(all(b - a >= 0.1 for a, b in zip(vals, vals[1:])), placa)

    def test_jabra_levels_are_the_positions(self):
        self.assertTrue(all(e.nivel_de_pct(p, "jabra") == e.posicao_de_pct(p) for p in range(101)))
        self.assertTrue(all(-4.0 < e.trim_db(p, "jabra") <= 0 for p in range(101)))

    def test_xvf_uses_its_own_1db_levels(self):
        self.assertEqual(e.NIVEIS["xvf3800"], tuple(range(-60, 1)))
        self.assertTrue(all(-1.0 < e.trim_db(p, "xvf3800") <= 0 for p in range(101)))
        self.assertGreaterEqual(len({e.nivel_de_pct(p, "xvf3800") for p in range(101)}), 30)

    def test_percent_survives_db(self):
        for placa in PLACAS:
            for p in range(101):
                self.assertEqual(e.pct_de_db(e.alvo_db(p, placa), placa), p)

    def test_floor_and_tables(self):
        self.assertEqual((e.ANCORAS["jabra"][0], e.ANCORAS["xvf3800"][0]), (-36, -34))
        self.assertEqual(e.VOLUME_XVF, [26, 30, 33, 37, 40, 43, 47, 50, 53, 57, 58, 60])

    def test_bad_percent(self):
        for ruim in (-1, 101, True, 50.0, "50", None):
            with self.assertRaises(ValueError):
                e.posicao_de_pct(ruim)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2:** run it. Expected: FAIL, no module `escala`.
- [ ] **Step 3: `escala.py`:**

```python
"""Escala fina de volume (spec 2026-11-27). Lógica pura, sem hardware.

O volume é um percentual inteiro 0–100. A CURVA de altura (ANCORAS) liga 12 percentuais de grade (a posição 0–11: a
escala falada e o LED do Jabra) a um valor em dB. Os NIVEIS são o que cada placa tem de verdade: 12 degraus de 4 dB no
Jabra, 61 níveis de 1 dB no XVF3800 (índice = valor cru do controle). A placa fica no nível mais baixo que alcança o
alvo e o softvol `Fine` atenua a diferença (trim, sempre <= 0)."""

MAX_POSICAO = 11
# XVF3800: valor cru de cada posição na curva de altura (a posição 0 era 0, quase mudo; 26 desde 27/11)
VOLUME_XVF = [26, 30, 33, 37, 40, 43, 47, 50, 53, 57, 58, 60]

ANCORAS = {"jabra": tuple(-36 + 4 * k for k in range(12)), "xvf3800": tuple(v - 60 for v in VOLUME_XVF)}
NIVEIS = {"jabra": ANCORAS["jabra"], "xvf3800": tuple(range(-60, 1))}


def grade(k: int) -> int:
    """Percentual de grade da posição k."""
    return round(k * 100 / MAX_POSICAO)


GRADE = tuple(grade(k) for k in range(MAX_POSICAO + 1))


def _valido(pct) -> None:
    if not isinstance(pct, int) or isinstance(pct, bool) or not 0 <= pct <= 100:
        raise ValueError("percentual de 0 a 100")


def posicao_de_pct(pct: int) -> int:
    """Menor posição cuja grade alcança o percentual."""
    _valido(pct)
    return next(k for k in range(MAX_POSICAO + 1) if GRADE[k] >= pct)


def alvo_db(pct: int, placa: str) -> float:
    k = posicao_de_pct(pct)
    a = ANCORAS[placa]
    if GRADE[k] == pct:
        return a[k]
    return a[k - 1] + (a[k] - a[k - 1]) * (pct - GRADE[k - 1]) / (GRADE[k] - GRADE[k - 1])


def nivel_de_pct(pct: int, placa: str) -> int:
    alvo = alvo_db(pct, placa) - 1e-9  # folga de ponto flutuante
    return next(i for i, db in enumerate(NIVEIS[placa]) if db >= alvo)


def trim_db(pct: int, placa: str) -> float:
    return min(0.0, alvo_db(pct, placa) - NIVEIS[placa][nivel_de_pct(pct, placa)])


def pct_de_db(db: float, placa: str) -> int:
    """O percentual cuja altura é a mais próxima de `db` (já limitado a 0..100)."""
    return min(range(101), key=lambda p: abs(alvo_db(p, placa) - db))
```

- [ ] **Step 4:** run `python3 -m unittest -q test_escala`. Expected: OK.
- [ ] **Step 5:** commit both files: `feat(satellite): fine volume scale`.

---

### Task 3: Fine mixer and board layer

**Files:** Create `satellite/program/mixer_fino.py`, `satellite/program/test_mixer_fino.py`. Modify `satellite/program/hardware.py`, `placa.py`, `test_placa.py`.

**Interfaces produced:** `mixer_fino.MixerFino(hw, fino, placa, agendar, atraso_s=0.4)`; `hardware.FineMixer(card="USB", control="Fine")`; `placa.montar()` also returns `"fine_mixer"` and `"escala"`; `placa.MixerXvf.escrever/ler` use raw levels 0..60. Test helper `test_mixer_fino.mixer_falso(escritas, placa="jabra")`.

- [ ] **Step 1: `test_mixer_fino.py`:**

```python
"""cd satellite/program && python3 -m unittest -q test_mixer_fino"""
import unittest

import escala
import hardware
from mixer_fino import MixerFino


class Hw:
    def __init__(self, log):
        self.log = log

    def escrever(self, n):
        self.log.append(("hw", n))

    def ler(self):
        return 7


class Fino:
    def __init__(self, log):
        self.log = log

    def write(self, db):
        self.log.append(("fine", db))


class Agenda:
    def __init__(self):
        self.pendentes = []

    def __call__(self, atraso, fn):
        item = {"fn": fn, "atraso": atraso, "vivo": True}
        self.pendentes.append(item)

        class H:
            def cancel(_):
                item["vivo"] = False
        return H()

    def vivos(self):
        return [i for i in self.pendentes if i["vivo"]]

    def disparar(self):
        for i in self.vivos():
            i["vivo"] = False
            i["fn"]()


def mixer_falso(escritas, placa="jabra"):
    """MixerFino sem atraso; `escritas` recebe só os níveis gravados na placa (usado por outros testes)."""
    class SoNivel:
        def escrever(self, n):
            escritas.append(n)

        def ler(self):
            return escritas[-1] if escritas else 0

    class Nada:
        def write(self, db):
            pass
    return MixerFino(SoNivel(), Nada(), placa, agendar=lambda s, fn: None, atraso_s=0)


def novo(pct, placa="jabra"):
    log, agenda = [], Agenda()
    m = MixerFino(Hw(log), Fino(log), placa, agendar=agenda)
    m.forcar(pct)
    log.clear()
    return m, log, agenda


def t(p, placa="jabra"):
    return escala.trim_db(p, placa)


class TestMixerFino(unittest.TestCase):
    def test_same_step_writes_only_the_trim(self):
        m, log, ag = novo(73)
        m.escrever(70)
        self.assertEqual(log, [("fine", t(70))])
        self.assertEqual(ag.vivos(), [])

    def test_step_up_lowers_trim_now_and_board_later(self):
        m, log, ag = novo(73)
        m.escrever(75)
        self.assertEqual(log, [("fine", t(75))])
        self.assertEqual([i["atraso"] for i in ag.vivos()], [0.4])
        ag.disparar()
        self.assertEqual(log[-1], ("hw", 9))

    def test_step_down_moves_board_first(self):
        m, log, ag = novo(75)
        m.escrever(70)
        self.assertEqual(log, [("hw", 8), ("fine", t(70))])
        self.assertEqual(ag.vivos(), [])

    def test_latest_wins_while_pending(self):
        m, log, ag = novo(73)
        for p in (75, 85, 70):
            m.escrever(p)
        ag.disparar()
        self.assertNotIn(("hw", 9), log)
        self.assertNotIn(("hw", 10), log)

    def test_only_a_lower_trim_restarts_the_delay(self):
        m, log, ag = novo(73)
        m.escrever(81)  # pending, trim near 0
        m.escrever(75)  # same target level, lower trim: restart
        self.assertEqual(len(ag.pendentes), 2)
        m.escrever(80)  # higher trim: keep the timer
        self.assertEqual(len(ag.pendentes), 2)
        self.assertEqual(len(ag.vivos()), 1)
        ag.disparar()
        self.assertEqual(log[-1], ("hw", 9))

    def test_never_louder_than_old_or_new(self):
        for placa in ("jabra", "xvf3800"):
            for a in range(0, 101, 3):
                for b in range(0, 101, 7):
                    m, log, ag = novo(a, placa)
                    hw, trim = escala.nivel_de_pct(a, placa), t(a, placa)
                    teto = max(escala.alvo_db(a, placa), escala.alvo_db(b, placa)) + 1e-9
                    m.escrever(b)
                    ag.disparar()
                    for quem, v in log:
                        hw, trim = (v, trim) if quem == "hw" else (hw, v)
                        self.assertLessEqual(escala.NIVEIS[placa][hw] + trim, teto, (placa, a, b, log))
                    self.assertAlmostEqual(escala.NIVEIS[placa][hw] + trim, escala.alvo_db(b, placa))

    def test_xvf_moves_its_raw_level(self):
        for pct, cru in ((100, 60), (91, 58), (73, 53)):
            m, _, _ = novo(pct, "xvf3800")
            self.assertEqual((m.nivel, m.trim), (cru, 0))

    def test_sincronizar_keeps_trim_and_cancels_pending(self):
        m, log, ag = novo(73)
        m.escrever(75)
        m.sincronizar(7)
        self.assertEqual((m.nivel, m.trim, log[-1], ag.vivos()), (7, t(75), ("hw", 7), []))
        self.assertAlmostEqual(m.db(), escala.NIVEIS["jabra"][7] + t(75))

    def test_forcar_writes_a_neighbour_first(self):
        log = []
        m = MixerFino(Hw(log), Fino(log), "jabra", agendar=Agenda())
        m.forcar(36)
        self.assertEqual([v for q, v in log if q == "hw"], [3, 4])
        log.clear()
        m.forcar(0)
        self.assertEqual([v for q, v in log if q == "hw"], [1, 0])

    def test_reaplicar_writes_the_board_even_if_unchanged(self):
        m, log, _ = novo(73)
        m.reaplicar(73)
        self.assertIn(("hw", 8), log)

    def test_fine_failure_is_logged_not_raised(self):
        class Ruim:
            def write(self, db):
                raise OSError("sem controle")
        ditos = []
        m = MixerFino(Hw([]), Ruim(), "jabra", agendar=Agenda(), log=ditos.append)
        m.forcar(36)
        self.assertTrue(any("fine_error" in d for d in ditos))

    def test_ler_reads_the_board(self):
        self.assertEqual(novo(73)[0].ler(), 7)


class TestFineMixer(unittest.TestCase):
    def test_raw_value(self):
        chamadas = []
        original = hardware.subprocess.run
        hardware.subprocess.run = lambda argv, **kw: chamadas.append(argv)
        try:
            f = hardware.FineMixer(card="Array")
            for db in (0.0, -1.0, -4.0, -99.0):
                f.write(db)
        finally:
            hardware.subprocess.run = original
        self.assertEqual([c[-1] for c in chamadas], ["120", "100", "40", "1"])
        self.assertEqual(chamadas[0][:6], ["amixer", "-q", "-c", "Array", "sset", "Fine"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: `mixer_fino.py`:**

```python
"""Placa + softvol `Fine` como um só volume (spec 2026-11-27). Lógica pura: quem escreve na placa (`hw`), no Fine
(`fino`) e quem agenda (`agendar(segundos, fn) -> objeto com cancel()`) são injetados.

A placa muda na hora; o Fine só se ouve depois do buffer (~0,4 s). Para nunca soar mais alto que o volume antigo e o
novo: ao SUBIR de nível, grava primeiro o Fine (mais baixo) e a placa depois do atraso; ao DESCER, a placa primeiro.
Só o valor mais novo vale: nada entra em fila."""
import json

import escala


class MixerFino:
    def __init__(self, hw, fino, placa, agendar, atraso_s=0.4, log=print):
        self._hw, self._fino, self.placa = hw, fino, placa
        self._agendar, self.atraso_s, self._log = agendar, atraso_s, log
        self.nivel = None  # último nível pedido
        self.trim = 0.0
        self._na_placa = None  # nível já gravado na placa
        self._pendente = None

    def _gravar_fino(self, db):
        try:
            self._fino.write(db)
        except Exception as e:  # sem o Fine o volume fica no degrau da placa; nunca derruba o programa
            self._log(json.dumps({"fine_error": f"{type(e).__name__}: {e}"}))

    def _gravar_placa(self, nivel):
        self._hw.escrever(nivel)
        self._na_placa = nivel

    def _cancelar(self):
        if self._pendente is not None:
            self._pendente.cancel()
            self._pendente = None

    def _disparar(self):
        self._pendente = None
        self._gravar_placa(self.nivel)

    def escrever(self, pct, forcar_placa=False):
        nivel, trim = escala.nivel_de_pct(pct, self.placa), escala.trim_db(pct, self.placa)
        trim_antes = self.trim
        self.nivel, self.trim = nivel, trim
        if self._na_placa is not None and nivel > self._na_placa:
            self._gravar_fino(trim)
            if self.atraso_s <= 0:
                self._cancelar()
                self._gravar_placa(nivel)
            elif self._pendente is None or trim < trim_antes:  # o atraso conta do último Fine que BAIXOU
                self._cancelar()
                self._pendente = self._agendar(self.atraso_s, self._disparar)
            return
        self._cancelar()
        if nivel != self._na_placa or forcar_placa:
            self._gravar_placa(nivel)
        self._gravar_fino(trim)

    def reaplicar(self, pct):
        """Grava Fine e placa mesmo sem mudança (a contagem do Jabra escreve direto na placa)."""
        self.escrever(pct, forcar_placa=True)

    def forcar(self, pct):
        """Partida: o driver USB não envia um valor igual ao último que gravou; escreve um vizinho antes."""
        self._cancelar()
        nivel = escala.nivel_de_pct(pct, self.placa)
        self.nivel, self.trim = nivel, escala.trim_db(pct, self.placa)
        self._gravar_fino(self.trim)
        self._gravar_placa(nivel - 1 if nivel > 0 else 1)
        self._gravar_placa(nivel)

    def sincronizar(self, nivel):
        """Botão físico: o aparelho já mudou de nível; regrava o mesmo nível. O trim fica."""
        self._cancelar()
        self.nivel = nivel
        self._gravar_placa(nivel)

    def db(self) -> float:
        return escala.NIVEIS[self.placa][self.nivel] + self.trim

    def ler(self) -> int:
        return self._hw.ler()
```

- [ ] **Step 3: `hardware.py`** — add after class `MusicMixer`:

```python
class FineMixer:
    """Controle `Fine` (softvol do asound.conf, spec 2026-11-27): -6..0 dB em 0..120 (0,05 dB). Nunca 0, que no softvol
    é mudo. `prime` toca 0,1 s de silêncio no PCM voice para o controle existir."""
    def __init__(self, card="USB", control="Fine"):
        self.card, self.control = card, control

    def write(self, db: float):
        cru = max(1, min(120, round((db + 6.0) * 20)))
        subprocess.run(["amixer", "-q", "-c", self.card, "sset", self.control, str(cru)], timeout=5, check=True)

    def prime(self):
        subprocess.run(["aplay", "-q", "-D", "voice", "-t", "raw", "-f", "S16_LE", "-r", "48000", "-c", "2"],
                       input=b"\0" * (48000 // 10 * 4), timeout=5)
```

- [ ] **Step 4: `placa.py`:**
  - Delete the two comment lines and the `VOLUME_XVF = [...]` line near the top; add `from escala import VOLUME_XVF  # curva de altura do XVF; os níveis de hardware são os 61 crus` next to `import hardware`.
  - Replace the whole methods `ler` and `escrever` of `MixerXvf` with:

```python
    def ler(self) -> int:
        """Nível cru 0–60 (1 dB cada)."""
        r = self._executar(["amixer", "-c", self.card, "sget", self.controle], capture_output=True, text=True,
                           timeout=5)
        m = re.search(r"Playback (\d+) \[", r.stdout or "")
        if not m:
            raise RuntimeError(f"amixer sem a posição do {self.controle}")
        return int(m.group(1))

    def escrever(self, nivel: int):
        valor = max(0, min(60, int(nivel)))
        self._executar(["amixer", "-q", "-c", self.card, "sset", self.controle, f"{valor},{valor}"], timeout=5)
```
  - Update the class docstring line that says positions 0–11 to say raw levels 0–60. `fixar_mestre` keeps `str(VOLUME_XVF[-1])`.
  - In `montar`, the XVF dict gains `"fine_mixer": hardware.FineMixer(card="Array"), "escala": "xvf3800",`; the Jabra dict gains `"fine_mixer": hardware.FineMixer(), "escala": "jabra",`.

- [ ] **Step 5: `test_placa.py`** — replace the class `TestVolumeXvf` with:

```python
class TestVolumeXvf(unittest.TestCase):
    def test_writes_the_raw_level(self):
        for cru in (0, 26, 43, 45, 60):
            ex = Exec()
            placa.MixerXvf(executar=ex).escrever(cru)
            self.assertEqual(ex.chamadas, [["amixer", "-q", "-c", "Array", "sset", "PCM", f"{cru},{cru}"]])

    def test_write_clamps(self):
        ex = Exec()
        m = placa.MixerXvf(executar=ex)
        m.escrever(99)
        m.escrever(-3)
        self.assertEqual([c[-1] for c in ex.chamadas], ["60,60", "0,0"])

    def test_reads_the_raw_level(self):
        for cru in (0, 41, 45, 59, 60):
            saida = f"Simple mixer control 'PCM',0\n  Front Left: Playback {cru} [67%] [-20.00dB]\n"
            self.assertEqual(placa.MixerXvf(executar=Exec(saida)).ler(), cru)

    def test_read_without_position_raises(self):
        with self.assertRaises(RuntimeError):
            placa.MixerXvf(executar=Exec("nada")).ler()

    def test_read_uses_card_array_pcm(self):
        ex = Exec("  Front Left: Playback 40 [67%]\n")
        placa.MixerXvf(executar=ex).ler()
        self.assertEqual(ex.chamadas[0], ["amixer", "-c", "Array", "sget", "PCM"])
```
  and in `TestMontar` add to `test_jabra_assembly_is_todays`: `self.assertEqual((c["fine_mixer"].card, c["fine_mixer"].control, c["escala"]), ("USB", "Fine", "jabra"))`; to `test_xvf_assembly`: `self.assertEqual((c["fine_mixer"].card, c["escala"]), ("Array", "xvf3800"))`.

- [ ] **Step 6:** `python3 -m unittest -q test_escala test_mixer_fino test_placa test_hardware`. Expected: OK.
- [ ] **Step 7:** commit: `feat(satellite): board level plus Fine softvol as one volume`.

---

### Task 4: Volume control in percent

**Files:** Modify `satellite/program/volume.py`, `comandos.py`, `test_volume.py`, `test_comandos.py`. (`test_satelite_ligacao` fails until Task 5; do not touch it here.)

**Interfaces consumed:** Task 2 `escala`, Task 3 `MixerFino` (`.placa`, `.nivel`, `.escrever`, `.forcar`, `.sincronizar`, `.db`) and `test_mixer_fino.mixer_falso`.

- [ ] **Step 1: `volume.py`:**
  - Docstring: replace the first paragraph's meaning: the volume is a percent 0–100; positions 0–11 are the spoken scale and the Jabra LEDs; the board and `Fine` are written by `mixer_fino.MixerFino`.
  - Add `import escala`; keep `MAX_POSICAO`, `ESPERA_GRAVACAO_S`, `_inteiro`, `posicao_de_numero` and `posicao_de_percentual` (used by `volume_inicial.py`).
  - Replace `descrever` and the whole class with:

```python
def descrever(pct: int) -> dict:
    """O volume em termos que a Friday entende: a posição (LEDs), o número da escala falada (None na posição 10, que
    não tem número próprio) e o percentual exato."""
    k = escala.posicao_de_pct(pct)
    numero = k if k <= 9 else (10 if k == MAX_POSICAO else None)
    return {"posicao": k, "volume": numero, "percentual": pct}


class ControleVolume:
    def __init__(self, mixer, gravar, pct_inicial, alertas_numero=6):
        self._mixer = mixer  # mixer_fino.MixerFino
        self._gravar = gravar  # fn(pct): grava o volume no config.json
        self.normal = pct_inicial  # volume da casa em % (None = ainda não se sabe)
        self.atual = pct_inicial  # o que está valendo no aparelho agora
        self.alertas_numero = alertas_numero  # escala falada 1–10 (config `volume_alertas`)
        self.em_alerta = False
        self._pendente = False
        self._ultima_mudanca = None  # instante da última mudança; None = gravar no próximo tique
        self._rampa = None  # (início, duração_s, posição alvo): alarme crescente (spec 2026-10-21 §11, alarm_ramp_s)

    @property
    def posicao(self):
        return None if self.atual is None else escala.posicao_de_pct(self.atual)

    def iniciar(self):
        """Na partida: aplica o volume gravado."""
        if self.atual is not None:
            self._mixer.forcar(self.atual)

    def _mudou_normal(self, pct, agora=None):
        self.normal = pct
        self._pendente, self._ultima_mudanca = True, agora

    def definir(self, pct, agora=None) -> int:
        """Voz ou PUT /config. Vale também durante um alerta: passa a ser o volume normal. Com `agora` (arraste no
        Music Assistant) a gravação espera as mudanças pararem."""
        if not _inteiro(pct) or not 0 <= pct <= 100:
            raise ValueError("percentual de 0 a 100")
        self.atual = pct
        self._rampa = None
        self._mixer.escrever(pct)
        self._mudou_normal(pct, agora)
        return pct

    def passo(self, delta: int) -> int:
        """"Mais alto"/"mais baixo" pela voz: uma posição, mantendo a distância até o degrau; entre as posições 1 e 11.
        "Mais baixo" nunca sobe e "mais alto" nunca desce."""
        base = self.atual if self.atual is not None else escala.grade(1)
        placa = self._mixer.placa
        k = escala.posicao_de_pct(base)
        novo = min(MAX_POSICAO, max(1, k + delta))
        folga = escala.alvo_db(base, placa) - escala.ANCORAS[placa][k]
        pct = escala.pct_de_db(escala.ANCORAS[placa][novo] + folga, placa)
        return self.definir(max(pct, base) if delta > 0 else min(pct, base))

    def apertou(self, delta: int, agora: float):
        """Botão +/− do Jabra (só o Jabra tem): o aparelho já mudou um degrau; regrava para sincronizar o driver."""
        if self.atual is None:
            return
        antes = self._mixer.nivel
        nivel = min(len(escala.NIVEIS[self._mixer.placa]) - 1, max(0, antes + delta))
        self._mixer.sincronizar(nivel)
        if nivel != antes:
            self.atual = escala.pct_de_db(self._mixer.db(), self._mixer.placa)
        if not self.em_alerta:
            self._mudou_normal(self.atual, agora)

    def _tique_rampa(self, agora: float):
        inicio, duracao, alvo = self._rampa
        frac = min(1.0, max(0.0, (agora - inicio) / duracao))
        pct = escala.grade(1 + int((alvo - 1) * frac))
        if pct != self.atual:
            self.atual = pct
            self._mixer.escrever(pct)
        if frac >= 1.0:
            self._rampa = None

    def tique(self, agora: float):
        if self._rampa is not None and self.em_alerta:
            self._tique_rampa(agora)
        if not self._pendente or self.normal is None:
            return
        if self._ultima_mudanca is not None and agora - self._ultima_mudanca < ESPERA_GRAVACAO_S:
            return
        self._gravar(self.normal)
        self._pendente = False

    def entrar_alerta(self, rampa_s: float = 0, agora=None):
        """`rampa_s` > 0 (e `agora`): alarme crescente — começa na posição 1 e sobe até o volume de alertas em
        `rampa_s` segundos (os degraus saem do tique). 0 = o comportamento de sempre."""
        if self.em_alerta:
            return
        self.em_alerta = True
        alvo = posicao_de_numero(self.alertas_numero)
        if rampa_s and rampa_s > 0 and agora is not None and alvo > 1:
            self._rampa = (agora, float(rampa_s), alvo)
            self.atual = escala.grade(1)
        else:
            self.atual = escala.grade(alvo)
        self._mixer.escrever(self.atual)

    def sair_alerta(self):
        if not self.em_alerta:
            return
        self.em_alerta = False
        self._rampa = None
        if self.normal is not None:
            self.atual = self.normal
            self._mixer.escrever(self.normal)
```

- [ ] **Step 2: `comandos.py`** — in `_volume`, replace the three `ctl` calls so that `numero` calls `ctl.definir(escala.grade(v.posicao_de_numero(dados["numero"])))` and `percentual` calls:

```python
    elif "percentual" in dados:
        p = dados["percentual"]
        if isinstance(p, bool) or not isinstance(p, (int, float)) or not 0 <= p <= 100:
            raise ValueError("percentual de 0 a 100")
        k = ctl.definir(int(round(p)))
```
  Add `import escala`. `ajuste` and the final `return v.descrever(k)` stay.

- [ ] **Step 3: `test_comandos.py`:** add `from test_mixer_fino import mixer_falso`. In `Base.setUp`: `self.vol = ControleVolume(mixer_falso(self.escritas), lambda k: None, 36, 6)` followed by `self.vol.iniciar()` and `self.escritas.clear()`. In every other `ControleVolume(lambda k: None, lambda k: None, 4, 6)` use `ControleVolume(mixer_falso([]), lambda k: None, 36, 6)` and call `.iniciar()` after it. Add to `TestVolume`:

```python
    def test_percentual_exato(self):
        self.assertEqual(self.run_(acao="volume", percentual=70)[0]["percentual"], 70)

    def test_numero_vira_o_percentual_de_grade(self):
        self.assertEqual(self.run_(acao="volume", numero=7)[0]["percentual"], 64)
```

- [ ] **Step 4: `test_volume.py`:** keep `TestConversao` but replace `test_descricao_da_posicao` with:

```python
    def test_descricao_do_percentual(self):
        self.assertEqual(v.descrever(36), {"posicao": 4, "volume": 4, "percentual": 36})
        self.assertEqual(v.descrever(100), {"posicao": 11, "volume": 10, "percentual": 100})
        self.assertEqual(v.descrever(91), {"posicao": 10, "volume": None, "percentual": 91})
        self.assertEqual(v.descrever(70), {"posicao": 8, "volume": 8, "percentual": 70})
```
  Delete class `TestAvisoDeMudanca`. Replace `Falso` and `controle` with the helper below (note: `inicial` stays a POSITION in the helper so existing tests read the same; `escritas` are board levels, equal to positions on the Jabra):

```python
class Falso:
    def __init__(self):
        self.escritas, self.gravados = [], []


def controle(inicial=4, alertas=6):
    f = Falso()
    c = v.ControleVolume(mixer_falso(f.escritas), f.gravados.append, None if inicial is None else e.grade(inicial),
                         alertas)
    if inicial is not None:
        c.iniciar()
        f.escritas.clear()
    return c, f
```
  with `import escala as e` and `from test_mixer_fino import mixer_falso`. Then translate the existing tests with these rules and nothing else: a volume passed to `definir` is `e.grade(k)`; assertions on `c.atual` / `c.normal` compare with `c.posicao` (for `atual`) or `e.posicao_de_pct(c.normal)`; `f.gravados` now holds percents, so compare with `[e.grade(k)]`; `f.escritas` assertions keep their numbers. Specific cases:
  - `test_iniciar_forca_a_escrita_com_um_vizinho` and `test_iniciar_no_zero_usa_o_vizinho_de_cima`: build with `v.ControleVolume(mixer_falso(f.escritas), f.gravados.append, e.grade(4), 6)` (resp. `e.grade(0)`), call `iniciar()`, expect `[3, 4]` (resp. `[1, 0]`).
  - `test_definir_fora_recusa`: values `(-1, 101, "3", None, 5.0)`.
  - `test_aperto_respeita_0_e_11`: also assert `c.atual` stays `0` and `100`.
  - `test_definir_escreve_e_grava_como_normal`: `self.assertEqual(c.definir(e.grade(7)), e.grade(7))`.

  Add:

```python
class TestVolumeFino(unittest.TestCase):
    def test_percent_is_kept_exactly(self):
        c, _ = controle(4)
        c.definir(70)
        self.assertEqual((c.normal, c.posicao), (70, 8))

    def test_step_keeps_the_offset(self):
        for placa in ("jabra", "xvf3800"):
            c = v.ControleVolume(mixer_falso([], placa), lambda p: None, 70)
            c.iniciar()
            folga = e.alvo_db(70, placa) - e.ANCORAS[placa][8]
            self.assertEqual(c.passo(+1), e.pct_de_db(e.ANCORAS[placa][9] + folga, placa))

    def test_step_never_moves_the_wrong_way(self):
        c, _ = controle(4)
        c.definir(5)
        self.assertEqual(c.passo(-1), 5)

    def test_button_recomputes_percent(self):
        c, _ = controle(4)
        c.definir(70)
        trim = e.trim_db(70, "jabra")
        c.apertou(-1, 0.0)
        self.assertEqual(c.atual, e.pct_de_db(e.NIVEIS["jabra"][7] + trim, "jabra"))

    def test_restore_after_alert_returns_exact_percent(self):
        c, _ = controle(4)
        c.definir(70)
        c.entrar_alerta()
        c.sair_alerta()
        self.assertEqual(c.atual, 70)

    def test_dragging_saves_only_after_it_stops(self):
        c, f = controle(4)
        for i, p in enumerate(range(50, 61)):
            c.definir(p, agora=i * 0.05)
        c.tique(1.0)
        self.assertEqual(f.gravados, [])
        c.tique(10.0)
        self.assertEqual(f.gravados, [60])
```

- [ ] **Step 5:** `python3 -m unittest -q test_escala test_mixer_fino test_volume test_comandos test_volume_inicial test_ring`. Expected: OK.
- [ ] **Step 6:** commit: `feat(satellite): volume control in exact percent`.

---

### Task 5: Config and program wiring

**Files:** Modify `satellite/program/configuracao.py`, `friday_satelite.py`, `test_configuracao.py`, `test_satelite_ligacao.py`.

- [ ] **Step 1: tests in `test_configuracao.py`**, add at the end (before `if __name__`):

```python
class TestVolumeFino(unittest.TestCase):
    def _carregar(self, **gravado):
        import json, shutil, tempfile, os
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, "config.json")
        with open(p, "w") as f:
            json.dump(dict(gravado, senha_porta_local="x" * 32), f)
        cfg = c.carregar(p, os.path.join(d, "house.json"))
        return cfg.get("volume_pct"), cfg.get("volume")

    def test_new_satellite_has_no_volume(self):
        self.assertEqual(self._carregar(), (None, None))

    def test_old_config_keeps_the_loudness(self):
        self.assertEqual(self._carregar(volume=9), (82, 9))

    def test_consistent_pair_keeps_the_percent(self):
        self.assertEqual(self._carregar(volume_pct=70, volume=8), (70, 8))

    def test_rollback_change_wins(self):
        self.assertEqual(self._carregar(volume_pct=70, volume=3), (27, 3))

    def test_leftover_webhook_key_still_loads(self):
        self.assertEqual(self._carregar(volume_webhook_url="http://x/api/webhook/y", volume=3), (27, 3))
        self.assertNotIn("volume_webhook_url", c.PADROES)

    def test_changes_keep_the_pair_consistent(self):
        atual = dict(c.PADROES, senha_porta_local="x" * 32, volume=8, volume_pct=70)
        n = c.validar_mudancas(atual, {"volume_pct": 75})
        self.assertEqual((n["volume_pct"], n["volume"]), (75, 9))
        n = c.validar_mudancas(atual, {"volume": 3})
        self.assertEqual((n["volume_pct"], n["volume"]), (27, 3))
        n = c.validar_mudancas(atual, {"nao_perturbe": True})
        self.assertEqual((n["volume_pct"], n["volume"]), (70, 8))
        for ruim in (101, -1, 5.5, True):
            with self.assertRaises(ValueError):
                c.validar_mudancas(atual, {"volume_pct": ruim})
```
  If the module is imported under another name than `c`, or `unittest` is missing, follow the file's existing imports.

- [ ] **Step 2: `configuracao.py`:**
  - `import escala`.
  - In `PADROES`: add `"volume_pct": None,` right after `"volume": None, ` and rewrite the comment above to say `volume_pct` is the volume (0–100) and `volume` is its position 0–11 (kept for rollback). Delete the `volume_webhook_url` entry and its two comment lines.
  - In `_REGRAS`: add `"volume_pct": ((int,), 0, 100),`.
  - Add before `ler_ou_criar_id`:

```python
def pct_efetivo(cfg: dict):
    """O volume em %. `volume_pct` vale enquanto combina com `volume`; se não combina, um programa antigo (rollback)
    mudou `volume`, que é a mudança mais nova. None = nenhum dos dois."""
    pct, pos = cfg.get("volume_pct"), cfg.get("volume")
    if pct is None:
        return None if pos is None else escala.grade(pos)
    if pos is not None and escala.posicao_de_pct(pct) != pos:
        return escala.grade(pos)
    return pct


def _par_volume(cfg: dict, pct) -> None:
    if pct is not None:
        cfg["volume_pct"], cfg["volume"] = pct, escala.posicao_de_pct(pct)
```
  - In `carregar`, immediately after the `if os.path.exists(caminho):` block ends (before the `senha_porta_local` check): `_par_volume(cfg, pct_efetivo(cfg))`.
  - In `validar_mudancas`, right before the final `return novo`:

```python
    if mudancas.get("volume_pct") is not None:
        _par_volume(novo, novo["volume_pct"])
    elif mudancas.get("volume") is not None:
        _par_volume(novo, escala.grade(novo["volume"]))
```
- [ ] **Step 3:** `python3 -m unittest -q test_configuracao`. Expected: OK.

- [ ] **Step 4: `friday_satelite.py`** (find each by text, not line number):
  1. Imports: add `import escala` and `from mixer_fino import MixerFino`.
  2. In `class Satelite`, before `def __init__`: `ATRASO_FINO_S = 0.4  # o Fine se ouve ~0,4 s depois (buffer); medido na bancada`
  3. Replace the block that starts at the line `fabrica = self.cfg["volume"] is None` and ends at the line `self._gravar_volume(inicial)` (inside `__init__`) with:

```python
        fine = camada["fine_mixer"]
        try:  # o softvol `Fine` só nasce no primeiro uso do PCM
            fine.prime()
        except Exception as e:
            print(json.dumps({"fine_prime_error": f"{type(e).__name__}: {e}"}), flush=True)
        self.mixer_fino = MixerFino(self.mixer, fine, camada["escala"], agendar=self.loop.call_later,
                                    atraso_s=self.ATRASO_FINO_S)
        fabrica = self.cfg["volume_pct"] is None  # 06/11: Mark novo não fica no volume que o aparelho guardou
        if fabrica:
            print(json.dumps({"volume_inicial": volume_inicial.aplicar()}), flush=True)
            nivel = self._ler_mixer()
            inicial = None if nivel is None else escala.pct_de_db(escala.NIVEIS[camada["escala"]][nivel],
                                                                  camada["escala"])
        else:
            inicial = self.cfg["volume_pct"]
        self.volume = vol.ControleVolume(self.mixer_fino, self._gravar_volume, inicial, self.cfg["volume_alertas"])
        if fabrica and inicial is not None:
            self._gravar_volume(inicial)
```
  4. `_gravar_volume`: rename the parameter to `pct` and pass `{"volume_pct": pct}`.
  5. `estado_publico`: replace `volume=self.volume.atual,` with `volume=self.volume.posicao, volume_pct=self.volume.normal,`.
  6. Delete the whole methods `_avisar_volume` and `_enviar_volume`.
  7. The two `emissor.emitir("config", {"mudancas": {"volume": self.volume.normal}, ...` calls (origins `voz` and `botao`) use `{"volume_pct": self.volume.normal}`. In `apertou_volume` the log becomes `{"botao_volume": delta, "posicao": self.volume.posicao, "pct": self.volume.atual}`.
  8. In the method whose docstring starts "O anel e o volume voltam ao que eram": replace `self.mixer.escrever(self.volume.atual)` with `self.mixer_fino.reaplicar(self.volume.atual)`.
  9. In `ao_mudar_config`: `k not in ("senha_porta_local", "volume_webhook_url")` becomes `k != "senha_porta_local"`; the `mudou_volume` line becomes `mudou_volume = novo.get("volume_pct") is not None and novo.get("volume_pct") != self.volume.normal`; and `self.volume.definir(novo["volume"])` becomes `self.volume.definir(novo["volume_pct"], time.monotonic())  # arraste no MA: grava quando parar`.
  10. Keep `ring.make_ring(..., posicao_fn=self.mixer.escrever, ...)` as it is. Run `grep -n "volume\.atual\|_volume_aviso\|volume_webhook\|\['volume'\]\|\[\"volume\"\]" friday_satelite.py` and report every remaining hit with a one-line reason (the `sons` key `"volume"` is the beep sound and stays).

- [ ] **Step 5: `test_satelite_ligacao.py`:**
  - Add a fake next to `MusicMixer`: `class FineMixer:` with `def __init__(self, card="USB", control="Fine"): self.card = card`, `def write(self, db): pass`, `def prime(self): pass`; set `hw.FineMixer = FineMixer`.
  - In `Base.asyncSetUp`, before creating `fs.Satelite`: `self.p_atraso = mock.patch.object(fs.Satelite, "ATRASO_FINO_S", 0)` and start it; stop it in `asyncTearDown`.
  - `{"mudancas": {"volume": 7}, "origem": "voz"}` becomes `{"mudancas": {"volume_pct": 64}, "origem": "voz"}`.
  - `self.assertEqual(self.s.volume.atual, 6)` after the alarm becomes `self.assertEqual(self.s.volume.posicao, 6)`.
  - In `test_put_config_muda_volume_e_alertas`: `novo = dict(self.s.cfg, volume=9, volume_pct=82, volume_alertas=8)`.
  - Fix any other failure only by this position→percent translation; if something else fails, report BLOCKED with the output.
- [ ] **Step 6:** full suite `python3 -m unittest -q 2>&1 | tail -5`. Expected: `OK (skipped=10)`.
- [ ] **Step 7:** commit: `feat(satellite): exact volume percent in config and program`.

---

### Task 6: Agent, ALSA chain, migration 0009, image check

**Files:** Modify `agents/satellite/friday_satellite_agent/capabilities.py`, `__main__.py`, `tests/agents/test_satellite_capabilities.py`, `satellite/audio/jabra.conf`, `satellite/audio/xvf3800.conf`, `tests/agents/test_audio_confs.py`, `satellite/image/verify_image.sh`. Create `satellite/migrations/0009_fine_volume.sh` (mode 755), `tests/agents/test_migration_0009.py`.

- [ ] **Step 1: agent tests.** In `test_satellite_capabilities.py` replace `test_volume_percent_position_roundtrip` with:

```python
def test_volume_state_is_the_exact_percent():
    cfg = dict(CFG, volume=8, volume_pct=70)
    assert cap.Mapper().state(cfg, WW, None)["volume"] == 70
    assert cap.Mapper().state(cfg, WW, {"estado": "parada", "volume_pct": 75})["volume"] == 75  # live wins
    assert cap.Mapper().state(dict(CFG, volume=8), WW, None)["volume"] == 73  # old program
    assert cap.Mapper().state(dict(CFG, volume=None), WW, {"estado": "parada", "volume": 11})["volume"] == 100


def test_ha_volume_echo_with_both_keys_is_ha():
    t = cap.Tracker(clock=lambda: 100.0)
    t.mark_ha({"volume_pct": 70})
    echo = {"mudancas": {"volume_pct": 70, "volume": 8}, "origem": "porta"}
    assert t.on_event("config", echo)["origin"] == "ha"


async def test_serialized_never_lets_a_slow_older_run_finish_last():
    seen = []

    async def work(delay, value):
        await asyncio.sleep(delay)
        seen.append(value)

    run = cap.serialized(work)
    await asyncio.gather(run(0.05, "old"), run(0, "new"))
    assert seen == ["old", "new"]
```
  add `import asyncio`; in `test_volume_set_and_unknown` the first assert becomes `assert m.to_program(CFG, "volume", 70.0) == {"config": {"volume_pct": 70}}`. Run `bash scripts/test.sh tests/agents/test_satellite_capabilities.py`. Expected: FAIL.

- [ ] **Step 2: `capabilities.py`:**
  - Delete `position_from_percent`.
  - In `Mapper.state`, replace the lines `pos = cfg.get("volume")` / `if pos is None and status:` / `pos = status.get("volume")` with:

```python
        pct = status.get("volume_pct") if status else None  # the live value: the file lags a button press by 3 s
        if pct is None:
            pct = cfg.get("volume_pct")
        if pct is None:
            pos = cfg.get("volume")
            if pos is None and status:
                pos = status.get("volume")
            pct = None if pos is None else percent_from_position(pos)
```
    and replace `if pos is not None:` / `st["volume"] = percent_from_position(pos)` with `if pct is not None:` / `st["volume"] = pct`.
  - In `to_program`: `return {"config": {"volume_pct": int(round(float(value)))}}` for key `volume`.
  - At the top of `Tracker.mark_ha`: `keys = set(keys)` then `if "volume_pct" in keys: keys.add("volume")  # the program echoes the pair`.
  - Add a module function (and `import asyncio` if missing):

```python
def serialized(fn):
    """One run at a time, in call order: a read that started before a change can never publish after it."""
    lock = asyncio.Lock()

    async def run(*args, **kwargs):
        async with lock:
            return await fn(*args, **kwargs)
    return run
```
- [ ] **Step 3: `__main__.py`:** right after the end of the nested `async def refresh(origin: str)` and before `async def do_set`, add `refresh = cap.serialized(refresh)  # poll, events and HA sets never interleave`. Check that no code inside `refresh` calls `refresh` again (it would deadlock); if one does, report BLOCKED.
- [ ] **Step 4:** `bash scripts/test.sh tests/agents/test_satellite_capabilities.py tests/agents/test_entity_placement.py tests/agents/test_satellite_board.py`. Expected: PASS. Commit: `feat(satellite-agent): exact volume percent, no stale refresh`.

- [ ] **Step 5: ALSA tests.** In `tests/agents/test_audio_confs.py`: `COMMON` gains `"pcm.fine_vol", 'name "Fine"'`; delete `JABRA_SHA256`, its comment, `test_jabra_conf_unchanged` and `import hashlib`; fix the module docstring; add:

```python
def _block(text, name):
    import re
    found = re.search(r"pcm\.%s \{(.*?)\n\}" % name, text, re.S)
    assert found, name
    return found.group(1)


def test_fine_softvol_is_the_only_client_of_the_dmix():
    for conf, card in (("jabra.conf", "USB"), ("xvf3800.conf", "Array")):
        text = (AUDIO / conf).read_text(encoding="utf-8")
        body = _block(text, "fine_vol")
        for needle in ("type softvol", 'slave.pcm "mixer_out"', 'name "Fine"', f"card {card}", "min_dB -6.0",
                       "max_dB 0.0", "resolution 121"):
            assert needle in body, f"{conf} fine_vol lacks {needle}"
        assert text.count('slave.pcm "mixer_out"') == 1, conf


def test_voice_and_music_pass_through_fine():
    jabra = (AUDIO / "jabra.conf").read_text(encoding="utf-8")
    for pcm in ("voice", "music_vol"):
        assert 'slave.pcm "fine_vol"' in _block(jabra, pcm), pcm
    xvf = (AUDIO / "xvf3800.conf").read_text(encoding="utf-8")
    assert 'slave.pcm "fine_vol"' in _block(xvf, "mono_out")
    for pcm in ("voice", "music_vol"):
        assert 'slave.pcm "mono_out"' in _block(xvf, pcm), pcm
```
- [ ] **Step 6: confs.** In BOTH files add this block right after the `pcm.mixer_out { ... }` block (card `USB` in `jabra.conf`, card `Array` in `xvf3800.conf`):

```
# Ajuste fino do volume da casa (spec 2026-11-27): voz e música passam por aqui. A placa fica no próprio nível e o
# `Fine` atenua a fração (0..120 = -6..0 dB). O programa grava; o ducking continua só no `Music`.
pcm.fine_vol {
    type softvol
    slave.pcm "mixer_out"
    control {
        name "Fine"
        card USB
    }
    min_dB -6.0
    max_dB 0.0
    resolution 121
}
```
  `jabra.conf`: in `pcm.voice` and in `pcm.music_vol`, `slave.pcm "mixer_out"` becomes `slave.pcm "fine_vol"`. `xvf3800.conf`: only in `pcm.mono_out`, `slave.pcm "mixer_out"` becomes `slave.pcm "fine_vol"`. Change nothing else.

- [ ] **Step 7: `tests/agents/test_migration_0009.py`:**

```python
"""Migration 0009 (27/11): installs the ALSA files with the `Fine` softvol. Activation runs migrations before it
installs system files, so 0009 installs them itself and then restarts what holds the audio output open."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIG = ROOT / "satellite" / "migrations" / "0009_fine_volume.sh"
FILES = ("jabra.conf", "xvf3800.conf")


def run(root, rel=None):
    env = {"FRIDAY_OTA_ROOT": str(root), "PATH": "/usr/bin:/bin"}
    if rel is not None:
        env["FRIDAY_RELEASE_DIR"] = str(rel)
    return subprocess.run(["bash", str(MIG)], env=env, capture_output=True, text=True)


def release(tmp_path, names=FILES):
    rel = tmp_path / "release"
    (rel / "system").mkdir(parents=True)
    for n in names:
        (rel / "system" / n).write_text(n)
    return rel


def test_0009_installs_both_confs(tmp_path):
    root = tmp_path / "root"
    r = run(root, release(tmp_path))
    assert r.returncode == 0, r.stderr
    for n in FILES:
        dest = root / "etc/friday-audio" / n
        assert dest.read_text() == n and oct(dest.stat().st_mode & 0o777) == "0o644"


def test_0009_is_idempotent(tmp_path):
    root, rel = tmp_path / "root", release(tmp_path)
    assert run(root, rel).returncode == 0
    assert run(root, rel).returncode == 0


def test_0009_missing_source_touches_nothing(tmp_path):
    root = tmp_path / "root"
    r = run(root, release(tmp_path, ["jabra.conf"]))
    assert r.returncode != 0 and "xvf3800.conf" in r.stderr
    assert not (root / "etc/friday-audio").exists()


def test_0009_needs_the_release_dir(tmp_path):
    assert run(tmp_path / "root").returncode != 0


def test_0009_is_executable_and_never_restarts_under_a_test_root():
    assert os.access(MIG, os.X_OK)
    assert '[ -z "$R" ]' in MIG.read_text()
```
- [ ] **Step 8: `satellite/migrations/0009_fine_volume.sh`:**

```bash
#!/usr/bin/env bash
# 0009 (27/11): installs the ALSA board files with the `Fine` softvol (fine volume). Activation runs migrations BEFORE
# it installs system files, so this one installs them itself, then restarts what keeps the audio output open with the
# old chain (the player and the silence feeder; activation restarts only the program and the agent).
# The activation snapshot already covers both files, so a rollback restores them. Idempotent. Runs as root.
set -uo pipefail
R=${FRIDAY_OTA_ROOT:-}
if [ -z "${FRIDAY_RELEASE_DIR:-}" ]; then echo "0009: FRIDAY_RELEASE_DIR not set" >&2; exit 1; fi
S=$FRIDAY_RELEASE_DIR/system
for f in jabra.conf xvf3800.conf; do  # all sources checked before anything is touched
  if [ ! -f "$S/$f" ]; then echo "0009: missing $f in the release system files" >&2; exit 1; fi
done
inst() {  # source mode dest
  mkdir -p "$(dirname "$R/$3")" && cp "$S/$1" "$R/$3.new" && chmod "$2" "$R/$3.new" && mv -f "$R/$3.new" "$R/$3"
}
mkdir -p "$R/etc/friday-audio" && chmod 0755 "$R/etc/friday-audio" || exit 1
inst jabra.conf 0644 etc/friday-audio/jabra.conf || exit 1
inst xvf3800.conf 0644 etc/friday-audio/xvf3800.conf || exit 1
if [ -z "$R" ]; then
  systemctl try-restart sendspin-cli friday-silence >/dev/null 2>&1 || true
fi
echo "0009: Fine volume audio files installed"
```
  `chmod 755` it.
- [ ] **Step 9: `satellite/image/verify_image.sh`:** after the line that checks `/etc/asound.conf is not a symlink`, add:

```bash
for c in jabra xvf3800; do
  $SUDO grep -q '^pcm.fine_vol' "$MNT/etc/friday-audio/$c.conf" || bad "$c.conf lacks the Fine softvol (fine volume)"
done
```
- [ ] **Step 10:** `bash scripts/test.sh tests/agents` (PASS), `(cd satellite/image && python3 -m unittest -q tests.test_payload_check tests.test_make_manifest tests.test_vault_payload tests.test_build_download)` and `bash ota/test_self_update.sh` (OK). Commit: `feat(satellite): Fine ALSA control, migration 0009, image check`.

---

## Controller steps (not for the implementer agents)

### Task 7: Load the mirrors and prove them (house)
- [ ] Back up `secrets.yaml` (`cp -p`), remove the two lines `friday_satellite_volume_webhook` and `friday_satellite_office_port_bearer` without printing the file; `check_config` valid; reload `input_number`, `automation`, `rest_command`; two helpers exist, four automations `on`.
- [ ] Live proof on each satellite, only while its player is not playing, restoring the first value: mirror → satellite, satellite → mirror, burst of 10 values with no ping-pong, program restart keeps the mirror and the board level, no errors in the log.
- [ ] MA setting for MK2 is the owner's (UI): report it. Update `friday-producao.md`, the new-satellite runbook §6 and the memory note.

### Task 8: Bench proof of the ALSA chain (both Pis, one at a time)
- [ ] Only while the satellite is idle with no timers and nothing playing. Use a temporary copy of the new conf (`ALSA_CONFIG_PATH` or a temporary `/etc/asound.conf` link restored at the end).
- [ ] `aplay -D voice` and 48 kHz S16 stereo on `music_vol` both play; `Fine` exists (record its initial value); 120 vs 40 changes both paths by about 4 dB measured with the board microphone; on the XVF3800 single raw steps near 30, 45, 60 change about 1 dB.
- [ ] Delay between a `Fine` write and the audible change, compared with 0.4 s; crossing a step both ways shows no 100 ms window more than 1 dB above the louder end.
- [ ] Restore services and the original link. If the chain fails, stop and report.

### Task 9: Docs, full suite, final review, PR
- [ ] `docs/satellite-settings.md` volume row; release notes (skill `writing-friday-release-notes`; version per `docs/releasing.md`) and `manifest.json` version.
- [ ] `bash scripts/test-all.sh` → `ALL GREEN`.
- [ ] Whole-branch review, one fix wave.
- [ ] Pull `origin/master`, rebase, push, open a **draft** PR with a "Merge gate" section = the alpha proof: both satellites updated and 0009 recorded; 73% sounds as before; MA slider changes sound at 70 → 72 → 75 and stays; drag with music has no jump or blip; Jabra buttons and voice "volume 7"/"mais baixo" move the slider; alarm restores the exact percent; program restart and board replug with music change nothing; Sendspin `volume = 100`; 1 h of music without new dropouts; MK2 wake word with music no worse. Check every CI job. No merge, tag or release.
