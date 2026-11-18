"""Produção (spec 2026-10-18 §4.11): rotas em inglês e endereço da VM. python3 -m unittest -q test_producao_satelite"""
import shutil
import os, unittest
import configuracao as cfgmod
AQUI = os.path.dirname(os.path.abspath(__file__))

class TestProducaoSatelite(unittest.TestCase):
    def test_endereco_da_vm_vem_da_casa(self):
        # 09/11: o endereço da VM saiu do código; vem de /etc/friday-satelite/house.json (gravado na imagem)
        self.assertIsNone(cfgmod.PADROES["endereco_friday"])
        self.assertEqual(cfgmod.CASA, "/etc/friday-satelite/house.json")
    def test_null_do_firstboot_nao_apaga_o_endereco_da_casa(self):
        # 17/11: firstboot.py grava PADROES (endereco_friday null) no config.json; o null vencia o house.json e o
        # programa morria em loop ("endereco_friday ausente") em todo satélite novo. Visto no MK1 com a imagem v0.3.4.
        import json, tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        casa, conf = os.path.join(d, "house.json"), os.path.join(d, "config.json")
        json.dump({"endereco_friday": "https://vm.example:7443"}, open(casa, "w"))
        json.dump({"endereco_friday": None, "senha_porta_local": "x", "comodo": None}, open(conf, "w"))
        cfg = cfgmod.carregar(conf, casa)
        self.assertEqual(cfg["endereco_friday"], "https://vm.example:7443")
        cfgmod.exigir_endereco(cfg, casa)
        # um valor de verdade no config.json continua valendo sobre a casa
        json.dump({"endereco_friday": "https://outro.example:7443", "senha_porta_local": "x"}, open(conf, "w"))
        self.assertEqual(cfgmod.carregar(conf, casa)["endereco_friday"], "https://outro.example:7443")
    def test_tons_de_fabrica_sao_os_da_elevenlabs(self):
        # 17/11: satélite novo nascia com os tons antigos (inicio_a…); o conjunto escolhido pelo dono em 20/10 vira o padrão
        s = cfgmod.PADROES["sons"]
        self.assertEqual((s["inicio"], s["fim"], s["erro"], s["alarme"], s["lembrete"], s["volume"]),
                         ("inicio_elevenlabs.wav", "fim_elevenlabs_20261020.wav", "erro_b.wav",
                          "alarme_elevenlabs.wav", "lembrete_elevenlabs.wav", "volume_a.wav"))
        import json
        vault = json.load(open(os.path.join(AQUI, "..", "..", "vault", "manifest.json")))["files"]
        nomes = {k.rsplit("/", 1)[-1] for k in vault}
        for f in ("inicio_elevenlabs.wav", "fim_elevenlabs_20261020.wav", "erro_b.wav",
                  "alarme_elevenlabs.wav", "lembrete_elevenlabs.wav"):
            self.assertIn(f, nomes)
    def test_aviso_vai_para_satellite_alert(self):
        t = open(os.path.join(AQUI, "friday_satelite.py")).read()
        self.assertIn("/satellite_alert", t); self.assertNotIn("/aviso_satelite", t)
    def test_porta_local_em_ingles(self):
        t = open(os.path.join(AQUI, "porta_local.py")).read()
        self.assertIn('web.get("/status"', t); self.assertIn('web.post("/doorbell"', t)
        self.assertNotIn('"/estado"', t); self.assertNotIn('"/campainha"', t)

if __name__ == "__main__":
    unittest.main()
