import subprocess, types, unittest
import volume_inicial as vi

JABRA = """ 0 [Headphones     ]: bcm2835_headpho - bcm2835 Headphones
                      bcm2835 Headphones
 1 [USB            ]: USB-Audio - Jabra SPEAK 410 USB
                      Jabra SPEAK 410 USB at usb-3f980000.usb-1.3, full speed
 2 [vc4hdmi        ]: vc4-hdmi - vc4-hdmi
                      vc4-hdmi
"""
XVF = " 1 [Array          ]: USB-Audio - reSpeaker XVF3800 4-Mic Array\n                      x\n"
OUTRO = " 0 [Headphones     ]: bcm2835_headpho - bcm2835 Headphones\n                      y\n 1 [Device         ]: USB-Audio - USB Audio Device\n                      z\n"
CONT = """Simple mixer control 'Music',0
  Capabilities: volume
Simple mixer control 'Headset',0
  Capabilities: cvolume cvolume-joined cswitch
Simple mixer control 'Speaker',0
  Capabilities: pvolume pswitch
"""


class Run:
    def __init__(self, recusa=(), conteudo=None):
        self.chamadas, self.recusa, self.conteudo = [], recusa, conteudo or {}

    def __call__(self, argv, **kw):
        self.chamadas.append(argv)
        if argv[-1] == "scontents":
            return types.SimpleNamespace(stdout=self.conteudo.get(argv[2], ""), returncode=0)
        return types.SimpleNamespace(returncode=1 if (argv[3], argv[-2]) in self.recusa else 0)


class TestVolumeInicial(unittest.TestCase):
    def test_jabra_usa_a_posicao(self):
        r = Run()
        self.assertEqual(vi.aplicar(65, JABRA, r), {"via": "jabra", "card": "USB", "controle": "PCM"})
        self.assertEqual(r.chamadas, [["amixer", "-q", "-c", "USB", "sset", "PCM", "7"]])  # 7 de 11 = 64%

    def test_xvf3800_tenta_pcm_em_percentual(self):
        r = Run()
        self.assertEqual(vi.aplicar(65, XVF, r)["via"], "xvf3800")
        self.assertEqual(r.chamadas[0][-1], "65%")

    def test_xvf3800_sem_pcm_cai_no_generico(self):
        r = Run(recusa={("Array", "PCM")}, conteudo={"Array": CONT})
        self.assertEqual(vi.aplicar(65, XVF, r), {"via": "generico", "card": "Array", "controle": "Speaker"})

    def test_generico_prefere_usb_e_ignora_softvol_e_captura(self):
        r = Run(conteudo={"Device": CONT, "Headphones": CONT})
        self.assertEqual(vi.aplicar(65, OUTRO, r), {"via": "generico", "card": "Device", "controle": "Speaker"})

    def test_sem_aparelho_nao_levanta(self):
        self.assertEqual(vi.aplicar(65, "", Run())["via"], None)
        def quebra(*a, **k):
            raise subprocess.TimeoutExpired("amixer", 5)
        self.assertEqual(vi.aplicar(65, JABRA, quebra)["via"], None)


if __name__ == "__main__":
    unittest.main()
