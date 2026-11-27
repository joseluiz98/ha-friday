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
