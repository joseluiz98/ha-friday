"""cd /homeassistant/tools/satelite/programa && python3 -m unittest test_ativacao_detalhes"""
import unittest
from ativacao_detalhes import payload


class TestPayload(unittest.TestCase):
    def test_rejeicao_completa(self):
        p = payload("friday", 0.8612, "barrada", modo="ativa", limiar=0.35, limiar_voz=0.6, voz=0.523, causa="sem_voz",
                    nota_verificador=0.86, limiar_verificador=0.3, razao_sussurro_db=-11.64, sussurro=False,
                    gravacao="/home/jose/gravacoes/ativacoes/sem_voz_20261103_094322.wav")
        self.assertEqual(p, {"palavra": "friday", "nota": 0.86, "resultado": "barrada", "duracao_s": None, "modo": "ativa",
                             "limiar": 0.35, "limiar_voz": 0.6, "voz": 0.52, "causa": "sem_voz", "nota_verificador": 0.86,
                             "limiar_verificador": 0.3, "razao_sussurro_db": -11.6, "sussurro": False,
                             "gravacao": "sem_voz_20261103_094322.wav"})

    def test_sem_opcionais(self):
        p = payload("sexta_feira", 0.7, "sombra", modo="sombra", limiar=0.5, limiar_voz=0.6)
        self.assertEqual(set(p), {"palavra", "nota", "resultado", "duracao_s", "modo", "limiar", "limiar_voz"})
