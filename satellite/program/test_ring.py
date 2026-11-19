"""Anel de LEDs: estados -> aparência, dois drivers (spec 2026-11-05 §11–§12).
cd /homeassistant/tools/satelite/programa && python3 -m unittest test_ring -v"""
import unittest
from unittest import mock

import ring


class Falso:
    """Transporte falso: grava (nome, valores)."""
    def __init__(self, falha=False):
        self.chamadas, self.falha = [], falha

    def write(self, nome, valores):
        if self.falha:
            raise OSError("usb")
        self.chamadas.append((nome, list(valores)))


class Jabra:
    def __init__(self):
        self.nomes, self.mudos = [], []

    def mostrar(self, nome, mudo=False):
        self.nomes.append(nome)
        self.mudos.append(mudo)


def W(nome, *v):
    return (nome, list(v))


class TimerFalso:
    def __init__(self, atraso, fn, lista):
        self.atraso, self.fn, self.cancelado, self.iniciado = atraso, fn, False, False
        lista.append(self)

    def start(self): self.iniciado = True
    def cancel(self): self.cancelado = True


class TestXvf(unittest.TestCase):
    def setUp(self):
        # 17/11: estes testes descrevem a composição com a geometria de fábrica (LED 0 = 0°, mudo só no LED 0);
        # a calibração da bancada tem teste próprio em test_xvf_link.TestRingCalibration
        for nome, valor in (("LED_OFFSET", 0), ("MUTE_LEDS", (0,))):
            p = mock.patch.object(ring, nome, valor); p.start(); self.addCleanup(p.stop)
        self.t = Falso()
        self.timers = []
        self.r = ring.Xvf3800Ring(self.t, timer_factory=lambda a, f: TimerFalso(a, f, self.timers))

    def mostrar(self, estado, direcao=None):
        self.t.chamadas.clear()
        self.r.show(estado, direcao)
        return self.t.chamadas

    def test_off(self):
        self.assertEqual(self.mostrar("off"), [W("LED_EFFECT", 0)])

    def test_listen_sem_direcao_usa_doa_do_firmware(self):
        self.assertEqual(self.mostrar("listen"), [W("LED_DOA_COLOR", 0x000010, 0x0000FF), W("LED_EFFECT", 4)])

    def test_speak(self):
        self.assertEqual(self.mostrar("speak"), [W("LED_COLOR", 0x0000FF), W("LED_EFFECT", 3)])

    def test_think(self):
        self.assertEqual(self.mostrar("think"), [W("LED_COLOR", 0x0000FF), W("LED_SPEED", ring.SPEED_FAST),
                                                  W("LED_BRIGHTNESS", ring.BRIGHTNESS), W("LED_EFFECT", 1)])

    def test_respirando(self):
        casos = {"alarm": (0xFF8000, ring.SPEED_FAST),
                 "setup": (0xFF8000, ring.SPEED_SLOW), "updating": (0xFFFFFF, ring.SPEED_SLOW),
                 "error": (0xFF0000, ring.SPEED_SLOW)}
        for estado, (cor, vel) in casos.items():
            with self.subTest(estado):
                self.assertEqual(self.mostrar(estado), [W("LED_COLOR", cor), W("LED_SPEED", vel),
                                                        W("LED_BRIGHTNESS", ring.BRIGHTNESS), W("LED_EFFECT", 1)])

    def test_erro_dura_10s_e_volta_ao_estado_anterior(self):
        self.r.show("speak")
        self.mostrar("error")
        self.assertEqual(self.timers[-1].atraso, ring.ERROR_HOLD_S)
        self.assertEqual(ring.ERROR_HOLD_S, 10.0)
        self.assertTrue(self.timers[-1].iniciado)
        self.t.chamadas.clear()
        self.timers[-1].fn()
        self.assertEqual(self.t.chamadas, [W("LED_COLOR", 0x0000FF), W("LED_EFFECT", 3)])

    def test_erro_sem_estado_anterior_volta_a_apagar(self):
        self.r.show("error")
        self.t.chamadas.clear()
        self.timers[-1].fn()
        self.assertEqual(self.t.chamadas, [W("LED_EFFECT", 0)])

    def test_estado_novo_durante_o_erro_cancela_a_volta(self):
        self.r.show("speak")
        self.r.show("error")
        t = self.timers[-1]
        self.r.show("muted")
        self.assertTrue(t.cancelado)
        self.t.chamadas.clear()
        t.fn()  # mesmo que dispare atrasado, não desfaz o estado novo
        self.assertEqual(self.t.chamadas, [])

    def test_erro_repetido_volta_ao_estado_de_antes_do_primeiro(self):
        self.r.show("speak")
        self.r.show("error")
        self.r.show("error")
        self.t.chamadas.clear()
        self.timers[-1].fn()
        self.assertEqual(self.t.chamadas[-1], W("LED_EFFECT", 3))

    def test_net_error_is_persistent_on_xvf(self):
        self.r.show("speak")
        self.t.chamadas.clear()
        self.r.show("net_error")
        self.assertEqual(self.timers, [])
        persistent = list(self.t.chamadas)
        self.assertEqual(persistent, [W("LED_COLOR", 0xFF0000), W("LED_SPEED", ring.SPEED_SLOW),
                                      W("LED_BRIGHTNESS", ring.BRIGHTNESS), W("LED_EFFECT", 1)])
        # same writes as "error"
        r2 = ring.Xvf3800Ring(Falso(), timer_factory=lambda a, f: TimerFalso(a, f, self.timers))
        r2.show("error")
        self.assertEqual(r2._t.chamadas, persistent)

    def test_xvf_pending_color(self):
        self.assertEqual(self.mostrar("update_pending"),
                         [W("LED_COLOR", 0xFFC800), W("LED_SPEED", ring.SPEED_SLOW),
                          W("LED_BRIGHTNESS", ring.BRIGHTNESS), W("LED_EFFECT", 1)])

    def test_pending_replaces_off_only(self):
        self.r.set_pending(True)
        pendente = self.mostrar("update_pending")
        self.r.show("off")
        self.assertEqual(self.mostrar("listen"), [W("LED_DOA_COLOR", 0x000010, 0x0000FF), W("LED_EFFECT", 4)])
        self.assertEqual(self.mostrar("off"), pendente)

    def test_pending_does_not_hide_other_states(self):
        self.r.set_pending(True)
        self.assertEqual(self.mostrar("speak"), [W("LED_COLOR", 0x0000FF), W("LED_EFFECT", 3)])
        self.assertEqual(self.mostrar("alarm")[-1], W("LED_EFFECT", 1))
        self.assertEqual(self.mostrar("muted"), [W("LED_COLOR", 0xFF0000), W("LED_EFFECT", 3)])

    def test_set_pending_redraws_when_idle(self):
        self.r.show("off")
        self.t.chamadas.clear()
        self.r.set_pending(True)
        self.assertEqual(self.t.chamadas[-1], W("LED_EFFECT", 1))
        self.assertEqual(self.t.chamadas[0], W("LED_COLOR", 0xFFC800))
        self.t.chamadas.clear()
        self.r.set_pending(False)
        self.assertEqual(self.t.chamadas, [W("LED_EFFECT", 0)])

    def test_set_pending_leaves_a_busy_ring_alone(self):
        self.r.show("speak")
        self.t.chamadas.clear()
        self.r.set_pending(True)
        self.assertEqual(self.t.chamadas, [])
        self.assertEqual(self.mostrar("off")[0], W("LED_COLOR", 0xFFC800))

    def test_set_pending_before_any_show_draws_nothing(self):
        self.r.set_pending(True)
        self.assertEqual(self.t.chamadas, [])

    def test_error_returns_to_pending_background(self):
        self.r.set_pending(True)
        self.r.show("off")
        self.r.show("error")
        self.t.chamadas.clear()
        self.timers[-1].fn()
        self.assertEqual(self.t.chamadas[0], W("LED_COLOR", 0xFFC800))

    def test_muted_fica_ate_mudar(self):
        self.r.show("muted")
        self.assertEqual(self.timers, [])

    def test_muted(self):
        self.assertEqual(self.mostrar("muted"), [W("LED_COLOR", 0xFF0000), W("LED_EFFECT", 3)])

    def test_estado_invalido(self):
        with self.assertRaises(ValueError):
            self.r.show("xyz")

    def test_cache_mesmo_estado_uma_escrita(self):
        self.r.show("speak")
        self.t.chamadas.clear()
        self.r.show("speak")
        self.assertEqual(self.t.chamadas, [])
        self.r.show("off")
        self.assertEqual(len(self.t.chamadas), 1)

    def test_reapply_reenvia_o_ultimo(self):
        self.r.show("muted")
        self.t.chamadas.clear()
        self.r.reapply()
        self.assertEqual(self.t.chamadas, [W("LED_COLOR", 0xFF0000), W("LED_EFFECT", 3)])

    def test_reapply_sem_estado_nao_faz_nada(self):
        self.r.reapply()
        self.assertEqual(self.t.chamadas, [])

    def test_close_apaga(self):
        self.r.show("speak")
        self.t.chamadas.clear()
        self.r.close()
        self.assertEqual(self.t.chamadas, [W("LED_EFFECT", 0)])

    def test_listen_com_direcao_desenha_o_anel(self):
        c = self.mostrar("listen", 0)
        self.assertEqual([x[0] for x in c], ["LED_RING_COLOR", "LED_EFFECT"])
        quadro = c[0][1]
        self.assertEqual(len(quadro), 12)
        self.assertEqual(c[1], W("LED_EFFECT", 5))
        self.assertEqual(quadro[0], 0x0000FF)           # o ponteiro (offset 0, sentido +1)
        self.assertEqual(quadro[1], quadro[11])         # vizinhos mais fracos
        self.assertEqual(quadro[6], 0x000010)           # longe: cor de base
        self.assertEqual(self.mostrar("listen", 0), [])  # quadro igual: não reenvia
        quadro2 = self.mostrar("listen", 180)[0][1]
        self.assertEqual(quadro2[6], 0x0000FF)

    def test_falha_de_usb_nao_levanta_e_reaplica_depois(self):
        self.t.falha = True
        self.r.show("speak")  # não levanta
        self.t.falha = False
        self.t.chamadas.clear()
        self.r.show("speak")  # o cache não guardou a falha: escreve de novo
        self.assertEqual(len(self.t.chamadas), 2)


class TestJabra(unittest.TestCase):
    def test_nomes_de_hoje(self):
        j = Jabra()
        r = ring.JabraRing(j)
        for estado in ("off", "listen", "speak", "think", "dnd", "alarm", "setup", "updating", "error", "muted"):
            r.show(estado, 90)
        self.assertEqual(j.nomes, ["parada", "ouvindo", "cerebro", "cerebro", "nao_perturbe", "alarme", "cerebro",
                                   "cerebro", "alarme", "parada"])  # M4: speak (recado, mic fechado) = luz do cérebro

    def test_delivery_light_from_the_maestro(self):  # M4
        self.assertEqual(ring.ESTADO_DO_MAESTRO["falando"], "speak")
        j = Jabra()
        ring.JabraRing(j).show_maestro(ring.ESTADO_DO_MAESTRO["falando"])
        self.assertEqual(j.nomes, ["cerebro"])

    def test_responding_is_cyan_breathing_on_xvf_and_listening_on_jabra(self):  # 18/11: luz da Echo enquanto ela fala
        self.assertEqual(ring.ESTADO_DO_MAESTRO["respondendo"], "respond")
        t = Falso()
        ring.Xvf3800Ring(t).show_maestro("respond")
        self.assertEqual(t.chamadas, [W("LED_COLOR", 0x00C8FF), W("LED_SPEED", ring.SPEED_RESPOND),
                                      W("LED_BRIGHTNESS", ring.BRIGHTNESS), W("LED_EFFECT", 1)])
        j = Jabra()
        ring.JabraRing(j).show_maestro("respond")
        self.assertEqual(j.nomes, ["ouvindo"])

    def test_update_pending_is_a_known_state(self):
        self.assertEqual(ring.ESTADOS[-1], "update_pending")
        self.assertFalse(ring.NullRing().pendente)

    def test_jabra_pending_uses_the_setup_light(self):
        # o Jabra não tem amarelo: update_pending desenha o mesmo que setup
        j = Jabra()
        r = ring.JabraRing(j)
        r.show("update_pending")
        r.show("setup")
        self.assertEqual(j.nomes, ["cerebro", "cerebro"])

    def test_jabra_pending_replaces_off_only(self):
        j = Jabra()
        r = ring.JabraRing(j)
        r.show("off")
        r.set_pending(True)
        r.show("off")
        r.show("listen")
        r.show("off")
        r.set_pending(False)
        self.assertEqual(j.nomes, ["parada", "cerebro", "cerebro", "ouvindo", "cerebro", "parada"])

    def test_net_error_on_jabra_uses_alarme(self):
        j = Jabra()
        ring.JabraRing(j).show("net_error")
        self.assertEqual(j.nomes, ["alarme"])

    def test_maestro_light_maps_to_net_error(self):
        self.assertEqual(ring.ESTADO_DO_MAESTRO["rede_esgotada"], "net_error")
        j = Jabra()
        ring.JabraRing(j).show(ring.ESTADO_DO_MAESTRO["rede_esgotada"])
        self.assertEqual(j.nomes, ["alarme"])

    def test_sem_cache_repete(self):
        j = Jabra()
        r = ring.JabraRing(j)
        r.show("listen")
        r.show("listen")
        self.assertEqual(j.nomes, ["ouvindo", "ouvindo"])

    def test_nomes_do_maestro_ida_e_volta(self):
        j = Jabra()
        r = ring.JabraRing(j)
        for nome in ("parada", "ouvindo", "cerebro", "nao_perturbe", "alarme"):
            r.show(ring.ESTADO_DO_MAESTRO[nome])
        self.assertEqual(j.nomes, ["parada", "ouvindo", "cerebro", "nao_perturbe", "alarme"])


class TestDeteccao(unittest.TestCase):
    def test_detect_ring(self):
        vistos = []

        def com(**kw):
            vistos.append(kw)
            return object()
        self.assertEqual(ring.detect_ring(com), "xvf3800")
        self.assertEqual(vistos, [{"idVendor": 0x2886, "idProduct": 0x001A}])
        self.assertEqual(ring.detect_ring(lambda **kw: None), "jabra")

    def test_detect_ring_erro_do_usb_vira_jabra(self):
        def quebra(**kw):
            raise RuntimeError("sem libusb")
        self.assertEqual(ring.detect_ring(quebra), "jabra")

    def test_make_ring_xvf(self):
        r = ring.make_ring(lambda: Jabra(), xvf_factory=lambda: Falso(), usb_find=lambda **kw: object(), log=lambda *a: None)
        self.assertIsInstance(r, ring.Xvf3800Ring)

    def test_make_ring_jabra(self):
        r = ring.make_ring(lambda: Jabra(), usb_find=lambda **kw: None, log=lambda *a: None)
        self.assertIsInstance(r, ring.JabraRing)

    def test_make_ring_xvf_quebra_cai_no_jabra(self):
        def ruim():
            raise PermissionError("sem permissão")
        logs = []
        r = ring.make_ring(lambda: Jabra(), xvf_factory=ruim, usb_find=lambda **kw: object(), log=logs.append)
        self.assertIsInstance(r, ring.JabraRing)
        self.assertTrue(logs)

    def test_make_ring_sem_nada_vira_nullring(self):
        def sem():
            raise RuntimeError("sem Jabra")

        def ruim():
            raise PermissionError("x")
        r = ring.make_ring(sem, xvf_factory=ruim, usb_find=lambda **kw: object(), log=lambda *a: None)
        self.assertIsInstance(r, ring.NullRing)
        r.show("speak")
        r.reapply()
        r.close()
        r2 = ring.make_ring(sem, usb_find=lambda **kw: None, log=lambda *a: None)
        self.assertIsInstance(r2, ring.NullRing)


class TestContagem(unittest.TestCase):
    def test_tempo(self):
        c = ring.Contagem()
        self.assertIsNone(c.tique(0))
        c.iniciar(100, 9, 10)
        self.assertIsNone(c.tique(105))  # ainda na frase
        self.assertEqual(c.tique(109), 1.0)
        self.assertAlmostEqual(c.tique(114), 0.5)
        self.assertEqual(c.tique(119), 0.0)
        self.assertIsNone(c.tique(120))
        c.iniciar(0, 0, 10)
        self.assertTrue(c.cancelar())
        self.assertFalse(c.cancelar())
        self.assertIsNone(c.tique(1))

    def test_jabra_desce_os_leds_de_volume_sem_repetir(self):
        pos = []
        r = ring.JabraRing(object(), pos.append)
        for f in (1.0, 0.99, 0.5, 0.0):
            r.countdown(f)
        self.assertEqual(pos, [11, 6, 0])
        r.reapply()
        r.countdown(0.0)
        self.assertEqual(pos, [11, 6, 0, 0])
        ring.JabraRing(object()).countdown(0.5)  # sem mixer: nada acontece

    def test_xvf_acende_um_arco_laranja_e_volta_ao_estado(self):
        escritas = []
        t = type("T", (), {"write": lambda self, n, v: escritas.append((n, v))})()
        r = ring.Xvf3800Ring(t)
        r.show("setup")
        escritas.clear()
        r.countdown(0.5); r.countdown(0.49)
        self.assertEqual(escritas, [("LED_RING_COLOR", [ring.COR_CONTAGEM] * 6 + [0] * 6), ("LED_EFFECT", [5])])
        escritas.clear()
        r.reapply()
        self.assertEqual(escritas[-1], ("LED_EFFECT", [1]))


if __name__ == "__main__":
    unittest.main()


# ---- v0.3.7: uma lista de prioridade + indicador de mudo (spec 2026-11-16-ring-state-priority)
BASES = [s for s in ring.PRIORIDADE if s != "off"]


class TestRingState(unittest.TestCase):
    def test_prioridade(self):
        self.assertEqual(ring.PRIORIDADE, ("alarm", "updating", "error", "setup", "speak", "respond", "listen", "think",
                                           "net_error", "dnd", "update_pending", "off"))

    def test_priority_table(self):
        for i, a in enumerate(BASES):
            for b in BASES:
                if a == "update_pending" or b == "update_pending":
                    continue
                with self.subTest(a=a, b=b):
                    s = ring.RingState()
                    s.set_layer("maestro", a)
                    s.set_layer("externo", b)
                    esperado = a if ring.PRIORIDADE.index(a) <= ring.PRIORIDADE.index(b) else b
                    self.assertEqual(s.resolve(), (esperado, False))

    def test_mute_shows_over_every_base(self):
        for base in ring.PRIORIDADE:
            with self.subTest(base):
                s = ring.RingState()
                if base == "update_pending":
                    s.set_pending(True)
                else:
                    s.set_layer("maestro", base)
                s.set_muted(True)
                self.assertEqual(s.resolve(), (base, True))

    def test_pending_only_below_everything(self):
        s = ring.RingState()
        s.set_pending(True)
        s.set_layer("maestro", "dnd")
        self.assertEqual(s.resolve()[0], "dnd")
        s.set_layer("maestro", "off")
        self.assertEqual(s.resolve()[0], "update_pending")

    def test_off_clears_a_layer(self):
        s = ring.RingState()
        s.set_layer("externo", "updating")
        s.set_layer("externo", "off")
        self.assertEqual(s.resolve(), ("off", False))


class TestComposicaoJabra(unittest.TestCase):
    def test_order_does_not_matter(self):
        ultimos = []
        for ordem in (("dnd", "mudo"), ("mudo", "dnd")):
            j = Jabra()
            r = ring.JabraRing(j)
            for passo in ordem:
                r.show_maestro("dnd") if passo == "dnd" else r.set_muted(True)
            ultimos.append((j.nomes[-1], j.mudos[-1]))
        self.assertEqual(ultimos, [("nao_perturbe", True), ("nao_perturbe", True)])

    def test_jabra_report_carries_mute_bit_while_muted(self):
        j = Jabra()
        r = ring.JabraRing(j)
        r.set_muted(True)
        r.show_maestro("dnd")
        r.show_maestro("listen")  # uma luz nova nunca tira o mudo (Review Focus 2)
        self.assertEqual(j.mudos[-2:], [True, True])
        r.set_muted(False)
        self.assertEqual((j.nomes[-1], j.mudos[-1]), ("ouvindo", False))

    def test_external_and_maestro_layers_compose(self):
        j = Jabra()
        r = ring.JabraRing(j)
        r.show_maestro("listen")
        r.show("updating")
        self.assertEqual(j.nomes[-1], "cerebro")  # updating > listen
        r.show("off")
        self.assertEqual(j.nomes[-1], "ouvindo")


class TestComposicaoXvf(unittest.TestCase):
    def setUp(self):
        # 17/11: estes testes descrevem a composição com a geometria de fábrica (LED 0 = 0°, mudo só no LED 0);
        # a calibração da bancada tem teste próprio em test_xvf_link.TestRingCalibration
        for nome, valor in (("LED_OFFSET", 0), ("MUTE_LEDS", (0,))):
            p = mock.patch.object(ring, nome, valor); p.start(); self.addCleanup(p.stop)
        self.t = Falso()
        self.timers = []
        self.r = ring.Xvf3800Ring(self.t, timer_factory=lambda a, f: TimerFalso(a, f, self.timers))

    def test_error_expiry_returns_to_highest_remaining_layer(self):
        self.r.show("updating")
        self.r.show_maestro("listen")
        self.r.show("error")
        erro = [x for x in self.timers if x.atraso == ring.ERROR_HOLD_S][-1]
        self.t.chamadas.clear()
        erro.fn()
        self.assertEqual(self.t.chamadas[0], W("LED_COLOR", 0xFFFFFF))  # updating

    def test_xvf_mute_frame(self):
        self.r.show_maestro("think")
        self.t.chamadas.clear()
        self.r.set_muted(True)
        quadro = self.t.chamadas[0]
        self.assertEqual(quadro[0], "LED_RING_COLOR")
        self.assertEqual(quadro[1][0], 0xFF0000)
        self.assertEqual(set(quadro[1][1:]), {0x0000FF})
        self.assertEqual(self.t.chamadas[-1], W("LED_EFFECT", 5))

    def test_xvf_mute_over_dnd_keeps_dnd_low(self):  # M9: mudo + não perturbe não fica mais forte que o DND sozinho
        self.r.show_maestro("dnd")
        self.t.chamadas.clear()
        self.r.set_muted(True)
        quadro = self.t.chamadas[0]
        self.assertEqual(set(quadro[1][1:]), {ring.escalar(0x8000FF, ring.DND_LOW)})

    def test_xvf_mute_over_off_lights_only_the_mute_leds(self):
        self.r.show_maestro("off")
        self.t.chamadas.clear()
        self.r.set_muted(True)
        self.assertEqual(self.t.chamadas[0][1], [0xFF0000] + [0] * 11)

    def test_xvf_dnd_low_then_pulse(self):
        self.r.show_maestro("dnd")
        baixa = ring.escalar(0x8000FF, ring.DND_LOW)
        self.assertEqual(self.t.chamadas, [W("LED_COLOR", baixa), W("LED_EFFECT", 3)])
        pulso = self.timers[-1]
        self.assertEqual(pulso.atraso, ring.DND_PULSE_EVERY_S)
        self.t.chamadas.clear()
        pulso.fn()
        self.assertEqual(self.t.chamadas, [W("LED_COLOR", 0x8000FF), W("LED_SPEED", ring.SPEED_SLOW),
                                           W("LED_BRIGHTNESS", ring.BRIGHTNESS), W("LED_EFFECT", 1)])
        volta = self.timers[-1]
        self.assertEqual(volta.atraso, ring.DND_PULSE_S)
        self.t.chamadas.clear()
        volta.fn()
        self.assertEqual(self.t.chamadas, [W("LED_COLOR", baixa), W("LED_EFFECT", 3)])
        proximo = self.timers[-1]
        self.r.show_maestro("listen")
        self.assertTrue(proximo.cancelado)

    def test_dnd_values(self):
        self.assertEqual((ring.DND_LOW, ring.DND_PULSE_EVERY_S, ring.DND_PULSE_S), (0.25, 30.0, 2.0))
        self.assertEqual(ring.escalar(0x8000FF, 0.15), 0x130026)  # a escala em si

    def test_preview_is_unitary_and_off_returns_to_the_composition(self):
        self.r.set_muted(True)
        self.r.show_maestro("dnd")
        self.t.chamadas.clear()
        self.r.preview("speak")
        self.assertEqual(self.t.chamadas, [W("LED_COLOR", 0x0000FF), W("LED_EFFECT", 3)])
        self.r.preview(None)
        self.assertEqual(self.t.chamadas[-1], W("LED_EFFECT", 5))  # de volta: dnd com o mudo por cima


class TestRevisaoFinalRing(unittest.TestCase):
    def setUp(self):
        self.t = Falso()
        self.timers = []
        self.r = ring.Xvf3800Ring(self.t, timer_factory=lambda a, f: TimerFalso(a, f, self.timers))

    def test_a_maestro_light_ends_an_interrupted_preview(self):  # I2
        self.r.preview("update_pending")
        self.r.show_maestro("listen")
        self.assertEqual(self.t.chamadas[-1], W("LED_EFFECT", 4))
        self.r.set_muted(True)
        self.assertEqual(self.t.chamadas[-1], W("LED_EFFECT", 5))

    def test_preview_during_error_does_not_leave_error_stuck(self):  # M2
        self.r.show("speak")
        self.r.show("error")
        self.r.preview("think")
        self.r.preview(None)
        self.assertEqual(self.t.chamadas[-1], W("LED_EFFECT", 3))  # speak, não o erro

    def test_a_stale_dnd_timer_does_nothing(self):  # M1
        self.r.show_maestro("dnd")
        velho = self.timers[-1]
        self.r.show_maestro("listen")
        self.r.show_maestro("dnd")
        novo = self.timers[-1]
        self.t.chamadas.clear()
        velho.fn()  # callback atrasado do timer cancelado
        self.assertEqual(self.t.chamadas, [])
        novo.fn()
        self.assertEqual(self.t.chamadas[-1], W("LED_EFFECT", 1))

    def test_a_stale_error_timer_does_nothing(self):  # M1
        self.r.show("speak")
        self.r.show("error")
        velho = self.timers[-1]
        self.r.show("error")
        self.t.chamadas.clear()
        velho.fn()
        self.assertEqual(self.t.chamadas, [])


class TestPreviaJabraComMudo(unittest.TestCase):
    def test_preview_on_a_muted_jabra_keeps_the_bit(self):  # M3
        j = Jabra()
        r = ring.JabraRing(j)
        r.set_muted(True)
        r.show_maestro("off")
        r.preview("alarm")
        self.assertEqual((j.nomes[-1], j.mudos[-1]), ("alarme", True))


if __name__ == "__main__":
    unittest.main()
