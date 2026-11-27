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
        self.assertEqual(self.dev.calls, [("AEC_FIXEDBEAMSONOFF", [0]), ("AEC_FIXEDBEAMSELEVATION_VALUES", [0.0, 0.0]),
                                          ("AUDIO_MGR_OP_L", [8, 0]),
                                          ("AUDIO_MGR_OP_R", [7, 3]), ("AUDIO_MGR_SYS_DELAY", [3]),
                                          ("GPO_WRITE_VALUE", [30, 1])])

    def test_startup_default_delay(self):
        xvf_link.apply_startup(self.link, {}, mudo=False)
        self.assertIn(("AUDIO_MGR_SYS_DELAY", [12]), self.dev.calls)
        self.assertEqual(self.dev.calls[-1], ("GPO_WRITE_VALUE", [30, 0]))

    def test_startup_resets_elevation(self):
        xvf_link.apply_startup(self.link, {}, mudo=False)
        self.assertEqual(self.dev.calls[1], ("AEC_FIXEDBEAMSELEVATION_VALUES", [0.0, 0.0]))

    def test_never_saves_or_reboots_the_board(self):
        src = open(xvf_link.__file__).read()
        self.assertNotIn("SAVE_CONFIGURATION", src.replace("# nunca SAVE_CONFIGURATION", ""))
        self.assertNotIn('"REBOOT"', src)


class FakeUsb:
    def __init__(self, respostas):
        self.respostas, self.chamadas = list(respostas), []

    def ctrl_transfer(self, req, request, wvalue, windex, length, timeout):
        self.chamadas.append((req, request, wvalue, windex, length, timeout))
        r = self.respostas.pop(0) if len(self.respostas) > 1 else self.respostas[0]
        if isinstance(r, Exception):
            raise r
        return r


class VendorDev(Dev):
    """Falso do vendor com `.dev` (pyusb) e a tabela PARAMETERS: nome -> (resid, cmdid, n, rw, tipo, descricao)."""
    def __init__(self, respostas):
        super().__init__()
        self.dev = FakeUsb(respostas)
        self.PARAMETERS = {"AEC_SPENERGY_VALUES": (33, 80, 4, "ro", "float", ""),
                           "DOA_VALUE": (20, 18, 2, "ro", "uint16", ""),
                           "AEC_FIXEDBEAMSONOFF": (33, 37, 1, "rw", "int32", ""),
                           "AUDIO_MGR_OP_L": (35, 15, 2, "rw", "uint8", "")}


class TestReadBounded(unittest.TestCase):
    def setUp(self):
        self.log = []

    def link(self, dev):
        return xvf_link.XvfLink(lambda: dev, log=self.log.append)

    def test_read_bounded_decodes_floats(self):
        import struct
        dev = VendorDev([bytes([0]) + struct.pack("<4f", 0.5, 1.5, -2.0, 0.25)])
        self.assertEqual(self.link(dev).read_bounded("AEC_SPENERGY_VALUES"), [0.5, 1.5, -2.0, 0.25])
        req, request, wvalue, windex, length, timeout = dev.dev.chamadas[0]
        self.assertEqual((req, request, wvalue, windex, length, timeout), (0xC0, 0, 0x80 | 80, 33, 17, 300))

    def test_read_bounded_decodes_other_types(self):
        import struct
        dev = VendorDev([bytes([0]) + struct.pack("<2H", 90, 1)])
        self.assertEqual(self.link(dev).read_bounded("DOA_VALUE"), [90, 1])
        dev = VendorDev([bytes([0]) + struct.pack("<i", -3)])
        self.assertEqual(self.link(dev).read_bounded("AEC_FIXEDBEAMSONOFF"), [-3])
        dev = VendorDev([bytes([0, 8, 0])])
        self.assertEqual(self.link(dev).read_bounded("AUDIO_MGR_OP_L"), [8, 0])

    def test_read_bounded_gives_up_after_max_retries(self):
        dev = VendorDev([bytes([64])])
        link = self.link(dev)
        self.assertIsNone(link.read_bounded("AEC_SPENERGY_VALUES", max_retries=3))
        self.assertEqual(len(dev.dev.chamadas), 4)
        self.assertEqual(link.falhas_leitura, 1)

    def test_read_bounded_other_status_is_none(self):
        dev = VendorDev([bytes([1])])
        self.assertIsNone(self.link(dev).read_bounded("AEC_SPENERGY_VALUES"))
        self.assertEqual(len(dev.dev.chamadas), 1)

    def test_read_bounded_unknown_name_none(self):
        dev = VendorDev([bytes([0])])
        link = self.link(dev)
        self.assertIsNone(link.read_bounded("NAO_EXISTE"))
        self.assertEqual(dev.dev.chamadas, [])
        self.assertEqual(link.falhas_leitura, 1)

    def test_read_bounded_usb_error_reopens(self):
        import struct
        ruim = VendorDev([OSError("usb")])
        bom = VendorDev([bytes([0]) + struct.pack("<4f", 1, 2, 3, 4)])
        devs = [ruim, bom]
        link = xvf_link.XvfLink(lambda: devs.pop(0), log=self.log.append)
        self.assertIsNone(link.read_bounded("AEC_SPENERGY_VALUES"))
        self.assertEqual(link.read_bounded("AEC_SPENERGY_VALUES"), [1.0, 2.0, 3.0, 4.0])
        self.assertEqual(devs, [])

    def test_read_bounded_busy_lock_is_none(self):
        dev = VendorDev([bytes([0])])
        link = xvf_link.XvfLink(lambda: dev, log=self.log.append, lock_timeout_s=0.01)
        with link._lock:
            self.assertIsNone(link.read_bounded("AEC_SPENERGY_VALUES"))
        self.assertEqual(dev.dev.chamadas, [])


class TestRingCalibration(unittest.TestCase):
    def test_ring_calibration_constants(self):
        # medidos na bancada em 17/11 (nota xvf3800-requisitos §6)
        self.assertEqual((ring.LED_OFFSET, ring.LED_SENTIDO, ring.MUTE_LEDS, ring.DND_LOW), (5, 1, (10, 11, 0), 0.25))


if __name__ == "__main__":
    unittest.main()


class TestRevisao(unittest.TestCase):
    def test_no_process_wide_stdout_swap(self):
        # review I3: redirect_stdout at 8 Hz from the DOA thread swallowed program log lines
        self.assertNotIn("redirect_stdout", open(xvf_link.__file__).read())

    def test_busy_lock_times_out(self):
        log = []
        link = xvf_link.XvfLink(lambda: Dev(), log=log.append, lock_timeout_s=0.05)
        link._lock.acquire()
        try:
            self.assertFalse(link.write("LED_EFFECT", [1]))
        finally:
            link._lock.release()
        self.assertTrue(any("ocupado" in m for m in log))

    def test_repeated_failure_logs_once_a_minute(self):
        dev, log, t = Dev(), [], {"t": 0.0}
        dev.falhar = True
        link = xvf_link.XvfLink(lambda: dev, log=log.append, relogio=lambda: t["t"])
        for _ in range(8):
            link.write("X", [1])
        self.assertEqual(len(log), 1)
        t["t"] = 61.0
        link.write("X", [1])
        self.assertEqual(len(log), 2)
