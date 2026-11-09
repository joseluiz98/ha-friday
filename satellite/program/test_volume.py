"""Testes do volume: cd /homeassistant/tools/satelite/programa && python3 -m unittest test_volume -v"""
import unittest
import volume as v


class TestConversao(unittest.TestCase):
    def test_numero_e_o_numero_de_leds_ate_9(self):
        self.assertEqual([v.posicao_de_numero(n) for n in range(1, 10)], list(range(1, 10)))

    def test_numero_10_e_o_maximo(self):
        self.assertEqual(v.posicao_de_numero(10), 11)

    def test_numero_fora_da_escala_recusa(self):
        for n in (0, 11, -1, 5.5, True, "5", None):
            with self.assertRaises(ValueError):
                v.posicao_de_numero(n)

    def test_percentual_vai_para_a_posicao_mais_proxima(self):
        self.assertEqual(v.posicao_de_percentual(100), 11)
        self.assertEqual(v.posicao_de_percentual(45), 5)
        self.assertEqual(v.posicao_de_percentual(36), 4)

    def test_percentual_nunca_desce_abaixo_de_1(self):
        self.assertEqual(v.posicao_de_percentual(0), 1)
        self.assertEqual(v.posicao_de_percentual(3), 1)

    def test_percentual_fora_recusa(self):
        for p in (-1, 101, True, "50", None):
            with self.assertRaises(ValueError):
                v.posicao_de_percentual(p)

    def test_descricao_da_posicao(self):
        self.assertEqual(v.descrever(4), {"posicao": 4, "volume": 4, "percentual": 36})
        self.assertEqual(v.descrever(11), {"posicao": 11, "volume": 10, "percentual": 100})
        self.assertEqual(v.descrever(10), {"posicao": 10, "volume": None, "percentual": 91})


class Falso:
    def __init__(self):
        self.escritas, self.gravados = [], []


def controle(inicial=4, alertas=6):
    f = Falso()
    c = v.ControleVolume(f.escritas.append, f.gravados.append, inicial, alertas)
    return c, f


class TestControle(unittest.TestCase):
    def test_iniciar_forca_a_escrita_com_um_vizinho(self):
        # o driver USB não envia valor igual ao último que ele mesmo gravou (teste de 12/10)
        c, f = controle(4)
        c.iniciar()
        self.assertEqual(f.escritas, [3, 4])

    def test_iniciar_no_zero_usa_o_vizinho_de_cima(self):
        c, f = controle(0)
        c.iniciar()
        self.assertEqual(f.escritas, [1, 0])

    def test_definir_escreve_e_grava_como_normal(self):
        c, f = controle(4)
        self.assertEqual(c.definir(7), 7)
        self.assertEqual((f.escritas, c.normal, c.atual), ([7], 7, 7))
        c.tique(100.0)
        self.assertEqual(f.gravados, [7])

    def test_definir_fora_recusa(self):
        c, _ = controle(4)
        for k in (-1, 12, "3", None):
            with self.assertRaises(ValueError):
                c.definir(k)

    def test_passo_parte_do_nivel_real_e_fica_entre_1_e_11(self):
        c, _ = controle(11)
        self.assertEqual(c.passo(+1), 11)
        c, _ = controle(1)
        self.assertEqual(c.passo(-1), 1)
        c, _ = controle(4)
        c.apertou(+1, 0.0)  # botão físico: aparelho foi para 5
        self.assertEqual(c.passo(+1), 6)

    def test_aperto_regrava_o_nivel_para_sincronizar_o_driver(self):
        c, f = controle(4)
        c.apertou(+1, 0.0)
        c.apertou(+1, 0.1)
        self.assertEqual(f.escritas, [5, 6])
        self.assertEqual(c.normal, 6)

    def test_aperto_respeita_0_e_11(self):
        c, _ = controle(0)
        c.apertou(-1, 0.0)
        self.assertEqual(c.atual, 0)
        c, _ = controle(11)
        c.apertou(+1, 0.0)
        self.assertEqual(c.atual, 11)

    def test_volume_igual_depois_de_apertos_ainda_e_enviado(self):
        # pegadinha: a Pi achava 4, o botão levou a 6; "volume 4" precisa chegar ao aparelho
        c, f = controle(4)
        c.apertou(+1, 0.0)
        c.apertou(+1, 0.1)
        c.definir(4)
        self.assertEqual(f.escritas[-1], 4)

    def test_gravacao_espera_intervalo_minimo(self):
        c, f = controle(4)
        c.apertou(+1, 10.0)
        c.tique(10.5)
        self.assertEqual(f.gravados, [])
        c.apertou(+1, 11.0)
        c.tique(14.0)
        self.assertEqual(f.gravados, [6])
        c.tique(20.0)
        self.assertEqual(f.gravados, [6])  # nada pendente, nada gravado de novo

    def test_alerta_aplica_e_volta_ao_normal(self):
        c, f = controle(4, alertas=6)
        c.entrar_alerta()
        self.assertEqual((c.atual, f.escritas), (6, [6]))
        c.sair_alerta()
        self.assertEqual((c.atual, f.escritas[-1]), (4, 4))

    def test_alerta_10_e_o_maximo(self):
        c, _ = controle(4, alertas=10)
        c.entrar_alerta()
        self.assertEqual(c.atual, 11)

    def test_botao_durante_alerta_nao_muda_o_normal(self):
        c, f = controle(4, alertas=6)
        c.entrar_alerta()
        c.apertou(+1, 0.0)
        self.assertEqual((c.atual, c.normal), (7, 4))
        c.tique(100.0)
        self.assertEqual(f.gravados, [])
        c.sair_alerta()
        self.assertEqual(c.atual, 4)

    def test_entrar_alerta_duas_vezes_nao_perde_o_normal(self):
        c, _ = controle(4)
        c.entrar_alerta()
        c.entrar_alerta()
        c.sair_alerta()
        self.assertEqual(c.atual, 4)

    def test_sair_sem_alerta_nao_escreve(self):
        c, f = controle(4)
        c.sair_alerta()
        self.assertEqual(f.escritas, [])

    def test_definir_durante_alerta_muda_o_normal_e_aplica(self):
        c, f = controle(4)
        c.entrar_alerta()
        c.definir(3)
        self.assertEqual((c.atual, c.normal), (3, 3))
        c.sair_alerta()
        self.assertEqual(c.atual, 3)

    def test_inicial_desconhecido_nao_escreve(self):
        c, f = controle(None)
        c.iniciar()
        self.assertEqual(f.escritas, [])


class TestRampaDeAlarme(unittest.TestCase):
    """spec §11 alarm_ramp_s: alarme crescente. 0 = desligado (comportamento de sempre)."""
    def test_alarm_ramp_off_by_default(self):
        c, f = controle(4, alertas=6)
        c.entrar_alerta()
        self.assertEqual(f.escritas, [6])
        c.tique(100.0)
        self.assertEqual(f.escritas, [6])

    def test_alarm_ramp_steps(self):
        c, f = controle(4, alertas=8)
        c.entrar_alerta(rampa_s=30, agora=100.0)
        self.assertEqual(f.escritas, [1])
        for t in range(100, 135):
            c.tique(float(t))
        self.assertEqual(f.escritas, sorted(f.escritas))
        self.assertEqual(f.escritas[-1], 8)
        self.assertEqual(c.atual, 8)
        self.assertEqual(len(set(f.escritas)), 8)  # passa por todas as posições de 1 a 8

    def test_alarm_ramp_does_not_change_the_saved_normal_volume(self):
        c, f = controle(4, alertas=8)
        c.entrar_alerta(rampa_s=30, agora=100.0)
        c.tique(110.0)
        c.sair_alerta()
        self.assertEqual((c.atual, f.escritas[-1]), (4, 4))
        c.tique(200.0)
        self.assertEqual(f.escritas[-1], 4)  # a rampa não continua depois de sair do alerta

    def test_alarm_ramp_ends_when_volume_is_set(self):
        c, f = controle(4, alertas=8)
        c.entrar_alerta(rampa_s=30, agora=100.0)
        c.definir(3)
        c.tique(129.0)
        self.assertEqual(c.atual, 3)


class TestAvisoDeMudanca(unittest.TestCase):
    """Spec 2026-10-21 §6.2 (ponte de volume): toda mudança do volume NORMAL avisa o HA; o volume de alerta não."""
    def novo(self, inicial=4):
        avisos = []
        c = v.ControleVolume(lambda k: None, lambda k: None, inicial, 6, avisar=avisos.append)
        return c, avisos

    def test_definir_avisa_a_posicao(self):
        c, a = self.novo()
        c.definir(7)
        self.assertEqual(a, [7])

    def test_passo_avisa(self):
        c, a = self.novo(4)
        c.passo(+1)
        self.assertEqual(a, [5])

    def test_aperto_avisa_fora_do_alerta(self):
        c, a = self.novo(4)
        c.apertou(-1, 0.0)
        self.assertEqual(a, [3])

    def test_alerta_nao_avisa(self):
        c, a = self.novo(4)
        c.entrar_alerta()
        c.apertou(+1, 0.0)
        c.sair_alerta()
        self.assertEqual(a, [])

    def test_iniciar_avisa_uma_vez_o_volume_gravado(self):
        c, a = self.novo(4)
        c.iniciar()
        self.assertEqual(a, [4])

    def test_falha_do_aviso_nao_derruba(self):
        def explode(k):
            raise RuntimeError("HA fora")
        c = v.ControleVolume(lambda k: None, lambda k: None, 4, 6, avisar=explode)
        self.assertEqual(c.definir(6), 6)

    def test_sem_aviso_funciona_como_antes(self):
        c, f = controle(4)
        self.assertEqual(c.definir(5), 5)


if __name__ == "__main__":
    unittest.main()
