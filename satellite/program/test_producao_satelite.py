"""Produção (spec 2026-10-18 §4.11): rotas em inglês e endereço da VM. python3 -m unittest -q test_producao_satelite"""
import os, unittest
import configuracao as cfgmod
AQUI = os.path.dirname(os.path.abspath(__file__))

class TestProducaoSatelite(unittest.TestCase):
    def test_endereco_da_vm_vem_da_casa(self):
        # 09/11: o endereço da VM saiu do código; vem de /etc/friday-satelite/house.json (gravado na imagem)
        self.assertIsNone(cfgmod.PADROES["endereco_friday"])
        self.assertEqual(cfgmod.CASA, "/etc/friday-satelite/house.json")
    def test_aviso_vai_para_satellite_alert(self):
        t = open(os.path.join(AQUI, "friday_satelite.py")).read()
        self.assertIn("/satellite_alert", t); self.assertNotIn("/aviso_satelite", t)
    def test_porta_local_em_ingles(self):
        t = open(os.path.join(AQUI, "porta_local.py")).read()
        self.assertIn('web.get("/status"', t); self.assertIn('web.post("/doorbell"', t)
        self.assertNotIn('"/estado"', t); self.assertNotIn('"/campainha"', t)

if __name__ == "__main__":
    unittest.main()
