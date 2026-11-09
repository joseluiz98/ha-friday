"""Primeiro boot: identidade do satélite (spec 2026-11-05 §6.2).
cd /homeassistant/tools/satelite/programa && python3 -m unittest test_firstboot -v"""
import json, os, re, shutil, stat, tempfile, unittest

import firstboot

CPUINFO = "processor\t: 0\nmodel name\t: ARMv8\n\nHardware\t: BCM2835\nSerial\t\t: 00000000A1B2C3D4E5\nModel\t\t: Raspberry Pi 3\n"


def chave_falsa(caminho, comentario):
    with open(caminho, "w") as f:
        f.write("PRIVATE " + comentario)
    with open(caminho + ".pub", "w") as f:
        f.write("ssh-ed25519 AAAAFAKE " + comentario + "\n")
    os.chmod(caminho, 0o600)


class TestSerial(unittest.TestCase):
    def test_serial_vira_id(self):
        self.assertEqual(firstboot.serial_id(CPUINFO), "sat-c3d4e5")

    def test_serial_ultimos_seis_hex(self):
        self.assertEqual(firstboot.serial_id("Serial : 0000000012345678\n"), "sat-345678")

    def test_sem_serial_gera_aleatorio(self):
        a, b = firstboot.serial_id("processor : 0\n"), firstboot.serial_id("")
        for x in (a, b):
            self.assertRegex(x, r"^sat-[0-9a-f]{6}$")

    def test_serial_de_zeros_nao_serve(self):
        self.assertRegex(firstboot.serial_id("Serial : 0000000000000000\n"), r"^sat-[0-9a-f]{6}$")


class TestRun(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.etc = os.path.join(self.root, "etc/friday-satelite")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def run_(self, cpuinfo=CPUINFO, mic="jabra-speak-410"):
        return firstboot.run(self.root, cpuinfo, mic, keygen=chave_falsa)

    def test_cria_tudo(self):
        r = self.run_()
        self.assertEqual(r["id"], "sat-c3d4e5")
        with open(os.path.join(self.etc, "id")) as f:
            self.assertEqual(f.read().strip(), r["id"])
        cfg = json.load(open(os.path.join(self.etc, "config.json")))
        self.assertGreaterEqual(len(cfg["senha_porta_local"]), 24)
        self.assertFalse(cfg["comodo"])
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.etc, "config.json")).st_mode), 0o600)
        self.assertTrue(os.path.exists(os.path.join(self.etc, "pairable")))
        self.assertFalse(os.path.exists(os.path.join(self.etc, "ha_token")))
        self.assertEqual(open(os.path.join(self.etc, "mic")).read().strip(), "jabra-speak-410")
        self.assertTrue(os.path.exists(os.path.join(self.root, "home/jose/.ssh/id_satsync")))
        self.assertTrue(os.path.exists(os.path.join(self.etc, ".firstboot-done")))
        self.assertIn("pairable", r["created"])

    def test_chave_leva_o_id_no_comentario(self):
        r = self.run_()
        pub = open(os.path.join(self.root, "home/jose/.ssh/id_satsync.pub")).read()
        self.assertIn(r["id"] + "-satsync", pub)

    def test_segunda_execucao_nao_muda_nada(self):
        self.run_()
        antes = {n: open(os.path.join(self.etc, n), "rb").read() for n in os.listdir(self.etc)}
        chave = open(os.path.join(self.root, "home/jose/.ssh/id_satsync")).read()
        r = self.run_(cpuinfo="Serial : 0000000099999999\n")
        self.assertEqual(r["created"], [])
        depois = {n: open(os.path.join(self.etc, n), "rb").read() for n in os.listdir(self.etc)}
        self.assertEqual(antes, depois)
        self.assertEqual(chave, open(os.path.join(self.root, "home/jose/.ssh/id_satsync")).read())

    def test_id_existente_e_preservado(self):
        os.makedirs(self.etc)
        open(os.path.join(self.etc, "id"), "w").write("sat-0a1b2c\n")
        r = self.run_()
        self.assertEqual(r["id"], "sat-0a1b2c")
        self.assertEqual(open(os.path.join(self.etc, "id")).read().strip(), "sat-0a1b2c")

    def test_config_existente_e_preservada(self):
        os.makedirs(self.etc)
        p = os.path.join(self.etc, "config.json")
        open(p, "w").write('{"senha_porta_local": "abc", "comodo": "Escritório"}')
        self.run_()
        self.assertEqual(json.load(open(p))["senha_porta_local"], "abc")

    def test_com_ha_token_nao_fica_pareavel(self):
        os.makedirs(self.etc)
        open(os.path.join(self.etc, "ha_token"), "w").write("x")
        r = self.run_()
        self.assertFalse(os.path.exists(os.path.join(self.etc, "pairable")))
        self.assertNotIn("pairable", r["created"])

    def test_sem_serial_formato_do_id(self):
        r = self.run_(cpuinfo="processor: 0\n")
        self.assertRegex(r["id"], r"^sat-[0-9a-f]{6}$")

    def test_mic_do_detect(self):
        self.assertEqual(firstboot.mic_from_ring("xvf3800"), "xvf3800")
        self.assertEqual(firstboot.mic_from_ring("jabra"), "jabra-speak-410")

    @unittest.skipUnless(shutil.which("ssh-keygen"), "sem ssh-keygen")
    def test_keygen_padrao_gera_ed25519_de_verdade(self):
        firstboot.run(self.root, CPUINFO, "xvf3800")
        pub = open(os.path.join(self.root, "home/jose/.ssh/id_satsync.pub")).read()
        self.assertTrue(pub.startswith("ssh-ed25519 "))
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.root, "home/jose/.ssh/id_satsync")).st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
