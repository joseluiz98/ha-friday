"""cd /homeassistant/tools/satelite/wakeword-training && python3 -m unittest -q test_eval_logic"""
import unittest
import eval_logic as e


class TestEvalLogic(unittest.TestCase):
    def test_disparos_cruza_limiar_e_rearma_abaixo_de_0_2(self):
        notas = [0.0, 0.7, 0.9, 0.5, 0.7, 0.1, 0.8]
        # dispara no 1; fica desarmado em 0,9/0,5/0,7 (nunca < 0,2); rearma no 0,1; dispara de novo no 6
        self.assertEqual(e.disparos(notas, 0.6), [1, 6])

    def test_disparos_vazio(self):
        self.assertEqual(e.disparos([], 0.6), [])
        self.assertEqual(e.disparos([0.5] * 10, 0.6), [])

    def test_avaliar_arquivo(self):
        notas = [0.0] * 700
        notas[int(10.5 / 0.08)] = 0.9    # dentro da janela do pos (10,0–10,4): acerto, não é falso
        notas[int(50.0 / 0.08)] = 0.95   # longe de qualquer pos: falso
        pos = [{"id": "a@10.00", "ini": 10.0, "fim": 10.4, "split": "holdout"}]
        r = e.avaliar_arquivo(notas, pos, 0.6)
        self.assertEqual(r["detectados"], ["a@10.00"]); self.assertEqual(r["falsos"], 1)

    def test_disparo_perto_de_pos_de_outro_lado_nao_conta_como_falso(self):
        notas = [0.0] * 500
        notas[int(30.0 / 0.08)] = 0.9
        pos = [{"id": "t@29.00", "ini": 29.2, "fim": 29.6, "split": "train"}]  # item de treino do mesmo arquivo
        r = e.avaliar_arquivo(notas, pos, 0.6)
        self.assertEqual(r["falsos"], 0)

    def test_pos_nao_detectado(self):
        r = e.avaliar_arquivo([0.1] * 100, [{"id": "a", "ini": 2.0, "fim": 2.4, "split": "holdout"}], 0.6)
        self.assertEqual(r["detectados"], [])

    def test_comparar(self):
        ids = ["a", "b", "c"]
        r = e.comparar(ids, {"a", "b"}, {"b", "c"})
        self.assertEqual(r["regressoes"], ["a"]); self.assertEqual(r["ganhos"], ["c"])


if __name__ == "__main__":
    unittest.main()
