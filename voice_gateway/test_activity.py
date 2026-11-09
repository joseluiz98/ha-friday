"""cd /srv/friday/app && python3 -m unittest -q test_activity"""
import json, os, tempfile, unittest
import activity as act


class TestActivity(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "activity.json")
        self.t = [1000.0]
        self.a = act.Activity(self.path, clock=lambda: self.t[0])

    def ler(self):
        return json.load(open(self.path))

    def test_comeca_vazio(self):
        d = self.ler()
        self.assertEqual((d["sessions"], d["brain_turns"], d["updated_at"]), ([], 0, 1000.0))

    def test_duas_sessoes_contam_as_duas(self):
        self.a.opened("voz-1"); self.a.opened("voz-2"); self.a.closed("voz-1")
        self.assertEqual(self.ler()["sessions"], ["voz-2"])

    def test_fechar_duas_vezes_nao_quebra(self):
        self.a.opened("voz-1"); self.a.closed("voz-1"); self.a.closed("voz-1")
        self.assertEqual(self.ler()["sessions"], [])

    def test_turno_do_cerebro_conta_sem_conversa(self):
        self.a.brain_started(); self.a.brain_started(); self.a.brain_finished()
        self.assertEqual(self.ler()["brain_turns"], 1)
        self.a.brain_finished(); self.a.brain_finished()
        self.assertEqual(self.ler()["brain_turns"], 0)

    def test_renovacao_atualiza_updated_at(self):
        self.t[0] = 1020.0
        self.a.write()
        self.assertEqual(self.ler()["updated_at"], 1020.0)

    def test_pasta_inexistente_nao_levanta(self):
        act.Activity("/nao/existe/activity.json").opened("x")


if __name__ == "__main__":
    unittest.main()
