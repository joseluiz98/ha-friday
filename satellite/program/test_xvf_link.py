"""Canal USB único do XVF3800 e ajustes na partida (spec 2026-11-17-xvf3800-support). python3 -m unittest -q test_xvf_link"""
import threading
import time
import unittest

import ring
import xvf_link


class Dev:
    """Falso do vendor/xvf_host: registra escritas; `falhar` faz a próxima escrita levantar."""
    def __init__(self):
        self.calls, self.falhar, self.lento = [], False, 0.0
        self.valores = {"GPO_READ_VALUES": [0, 1, 0]}

    def write(self, nome, valores):
        if self.falhar:
            raise TimeoutError("usb timeout")
        if self.lento:
            self.calls.append(("ini", nome))
            time.sleep(self.lento)
        self.calls.append((nome, list(valores)))

    def read(self, nome):
        return self.valores.get(nome)


class TestXvfLink(unittest.TestCase):
    def setUp(self):
        self.dev, self.log = Dev(), []
        self.link = xvf_link.XvfLink(lambda: self.dev, log=self.log.append)

    def test_link_write_failure_is_logged_not_raised(self):
        self.dev.falhar = True
        self.assertFalse(self.link.write("LED_EFFECT", [1]))
        self.assertTrue(self.log and "LED_EFFECT" in self.log[0])

    def test_open_failure_is_logged_and_retried(self):
        abre = {"n": 0}
        def fabrica():
            abre["n"] += 1
            if abre["n"] == 1:
                raise RuntimeError("XVF3800 não encontrado")
            return self.dev
        link = xvf_link.XvfLink(fabrica, log=self.log.append)
        self.assertFalse(link.write("LED_EFFECT", [1]))
        self.assertTrue(link.write("LED_EFFECT", [1]))

    def test_read(self):
        self.assertEqual(self.link.read("GPO_READ_VALUES"), [0, 1, 0])

    def test_link_serializes(self):
        self.dev.lento = 0.05
        ts = [threading.Thread(target=self.link.write, args=(n, [1])) for n in ("A", "B")]
        [t.start() for t in ts]
        [t.join() for t in ts]
        nomes = [c[0] for c in self.dev.calls]
        self.assertIn(nomes, (["ini", "A", "ini", "B"], ["ini", "B", "ini", "A"]))

    def test_startup_unlocks_first(self):
        xvf_link.apply_startup(self.link, {"xvf_sys_delay": 12}, mudo=False)
        self.assertEqual(self.dev.calls[0], ("AEC_FIXEDBEAMSONOFF", [0]))

    def test_startup_order_and_values(self):
        xvf_link.apply_startup(self.link, {"xvf_sys_delay": 3}, mudo=True)
        self.assertEqual(self.dev.calls, [("AEC_FIXEDBEAMSONOFF", [0]), ("AUDIO_MGR_OP_L", [8, 0]),
                                          ("AUDIO_MGR_OP_R", [7, 3]), ("AUDIO_MGR_SYS_DELAY", [3]),
                                          ("GPO_WRITE_VALUE", [30, 1])])

    def test_startup_default_delay(self):
        xvf_link.apply_startup(self.link, {}, mudo=False)
        self.assertIn(("AUDIO_MGR_SYS_DELAY", [12]), self.dev.calls)
        self.assertEqual(self.dev.calls[-1], ("GPO_WRITE_VALUE", [30, 0]))

    def test_never_saves_or_reboots_the_board(self):
        src = open(xvf_link.__file__).read()
        self.assertNotIn("SAVE_CONFIGURATION", src.replace("# nunca SAVE_CONFIGURATION", ""))
        self.assertNotIn('"REBOOT"', src)


class TestRingCalibration(unittest.TestCase):
    def test_ring_calibration_constants(self):
        # medidos na bancada em 17/11 (nota xvf3800-requisitos §6)
        self.assertEqual((ring.LED_OFFSET, ring.LED_SENTIDO, ring.MUTE_LEDS, ring.DND_LOW), (5, 1, (10, 11, 0), 0.25))


if __name__ == "__main__":
    unittest.main()
