"""cd /homeassistant/tools/satelite/programa && python3 -m unittest test_detectores -v"""
import unittest
from detectores import (DetectorFala, DetectorMudo, DetectorPalavra, EpisodioVerificador, RejeicaoEpisodio,
                        trecho_do_episodio)

ZERO = bytes(2560)
SOM = (b"\x10\x00" * 1280)


class TestFalaEmAndamento(unittest.TestCase):
    def test_comeca_falando_e_para_depois_do_silencio(self):
        d = DetectorFala()
        d.iniciar_falando()
        self.assertTrue(d.falando)
        self.assertEqual([d.alimentar(0.0) for _ in range(10)], [None] * 9 + ["fala_parou"])

    def test_fala_continua_nao_emite_comecou(self):
        d = DetectorFala()
        d.iniciar_falando()
        self.assertEqual([d.alimentar(0.9) for _ in range(5)], [None] * 5)


class TestSussurro(unittest.TestCase):
    def novo(self, ls=5):
        return DetectorPalavra(0.6, limiar_voz=0.6, limiar_sussurro=ls)

    def test_sussurro_com_voz_baixa_dispara(self):
        d = self.novo()
        self.assertTrue(d.alimentar(0.8, 0.2, razao=12))
        self.assertTrue(d.ultimo_sussurro)

    def test_voz_baixa_sem_razao_nao_dispara(self):
        self.assertFalse(self.novo().alimentar(0.8, 0.2, razao=None))

    def test_voz_baixa_razao_baixa_nao_dispara(self):
        self.assertFalse(self.novo().alimentar(0.8, 0.2, razao=-3))

    def test_voz_alta_razao_alta_e_sussurro(self):
        d = self.novo()
        self.assertTrue(d.alimentar(0.8, 0.9, razao=12))
        self.assertTrue(d.ultimo_sussurro)

    def test_voz_alta_razao_baixa_e_normal(self):
        d = self.novo()
        self.assertTrue(d.alimentar(0.8, 0.9, razao=-3))
        self.assertFalse(d.ultimo_sussurro)

    def test_razao_alta_sem_palavra_nao_dispara(self):
        self.assertFalse(self.novo().alimentar(0.3, 0.1, razao=25))

    def test_null_desliga_sussurro(self):
        self.assertFalse(self.novo(ls=None).alimentar(0.8, 0.2, razao=25))

    def test_armado(self):
        d = self.novo()
        self.assertTrue(d.armado)
        d.alimentar(0.8, 0.9)
        self.assertFalse(d.armado)
        d.alimentar(0.1, 0.9)
        self.assertTrue(d.armado)


class TestPalavra(unittest.TestCase):
    def test_dispara_uma_vez_e_rearma(self):
        d = DetectorPalavra(0.5)
        self.assertEqual([d.alimentar(s) for s in (0.1, 0.6, 0.9, 0.4, 0.1, 0.7)],
                         [False, True, False, False, False, True])

    def test_sem_voz_nao_dispara_e_continua_armado(self):
        # 06/10: música da Echo deu nota 0,99 com voz 0,14; "Friday" de verdade tem voz >= 0,81
        d = DetectorPalavra(0.5, limiar_voz=0.6)
        self.assertFalse(d.alimentar(0.99, voz=0.14))
        self.assertTrue(d.alimentar(0.9, voz=0.85))  # a voz chega no pedaço seguinte: ainda vale

    def test_limiar_voz_zero_desliga_a_porta(self):
        self.assertTrue(DetectorPalavra(0.5, limiar_voz=0).alimentar(0.6, voz=0.0))

    def test_quase_episodio_sem_disparo(self):
        d = DetectorPalavra(0.5, limiar_voz=0.6)
        for s, v in ((0.1, 0.9), (0.35, 0.9), (0.45, 0.95), (0.1, 0.9)):
            self.assertFalse(d.alimentar(s, voz=v))
        self.assertEqual(d.pegar_quase(), {"nota": 0.45, "voz": 0.95})
        self.assertIsNone(d.pegar_quase())

    def test_episodio_que_disparou_nao_vira_quase(self):
        d = DetectorPalavra(0.5)
        d.alimentar(0.4); d.alimentar(0.6); d.alimentar(0.1)
        self.assertIsNone(d.pegar_quase())

    def test_abaixo_de_0_3_nao_e_episodio(self):
        d = DetectorPalavra(0.5)
        d.alimentar(0.25); d.alimentar(0.1)
        self.assertIsNone(d.pegar_quase())

    def test_sem_voz_acima_do_limiar_vira_quase_com_a_voz_baixa(self):
        # o programa só registra "quase" com voz humana; o detector devolve o episódio e quem filtra é quase_vale
        d = DetectorPalavra(0.5, limiar_voz=0.6)
        d.alimentar(0.9, voz=0.1); d.alimentar(0.1, voz=0.1)
        self.assertEqual(d.pegar_quase(), {"nota": 0.9, "voz": 0.1})


class TestEpisodioVerificador(unittest.TestCase):
    """20/10: "Friday" barrado pelo verificador não deixava rastro (a nota base é trocada pela dele)."""
    def test_barrada_quando_o_verificador_foi_consultado_e_nao_disparou(self):
        e = EpisodioVerificador()
        self.assertIsNone(e.alimentar(True, 0.05, 0.9, False))
        self.assertIsNone(e.alimentar(True, 0.12, 0.95, False))
        self.assertEqual(e.alimentar(False, 0.0, 0.4, False), {"nota": 0.12, "voz": 0.95})

    def test_episodio_que_disparou_nao_e_barrado(self):
        e = EpisodioVerificador()
        e.alimentar(True, 0.3, 0.9, False); e.alimentar(True, 0.8, 0.9, True)
        self.assertIsNone(e.alimentar(False, 0.0, 0.9, False))

    def test_sem_consulta_nada_acontece(self):
        e = EpisodioVerificador()
        self.assertIsNone(e.alimentar(False, 0.0, 0.9, False))
        self.assertIsNone(e.alimentar(False, 0.0, 0.9, False))

    def test_um_aviso_por_episodio(self):
        e = EpisodioVerificador()
        e.alimentar(True, 0.1, 0.9, False)
        self.assertIsNotNone(e.alimentar(False, 0.0, 0.9, False))
        self.assertIsNone(e.alimentar(False, 0.0, 0.9, False))


class TestFala(unittest.TestCase):
    def test_histerese(self):
        d = DetectorFala(limiar=0.5, comeca_apos=2, para_apos=3)
        seq = [0.9, 0.9, 0.9, 0.1, 0.1, 0.9, 0.1, 0.1, 0.1]
        self.assertEqual([d.alimentar(p) for p in seq],
                         [None, "fala_comecou", None, None, None, None, None, None, "fala_parou"])


class TestMudo(unittest.TestCase):
    def test_entra_apos_6_pedacos_de_zero_e_sai_no_primeiro_com_som(self):
        d = DetectorMudo(pedacos=6)
        r = [d.alimentar(ZERO) for _ in range(6)] + [d.alimentar(ZERO), d.alimentar(SOM), d.alimentar(SOM)]
        self.assertEqual(r, [None] * 5 + [True, None, False, None])

    def test_silencio_real_nao_e_mudo(self):
        d = DetectorMudo(pedacos=6)
        quase = (b"\x01\x00" + bytes(2558))  # uma amostra não-zero: sala silenciosa, não mudo
        self.assertEqual([d.alimentar(quase) for _ in range(10)], [None] * 10)

    def test_partida_desconhecida_decide_no_primeiro_sinal(self):
        # Revisão final C1 (08/10): na partida o maestro espera esta decisão antes de escrever luz.
        d = DetectorMudo(pedacos=6, desconhecido=True)
        self.assertIsNone(d.mudo)
        self.assertEqual([d.alimentar(SOM), d.alimentar(SOM)], [False, None])
        d2 = DetectorMudo(pedacos=6, desconhecido=True)
        self.assertEqual([d2.alimentar(ZERO) for _ in range(6)], [None] * 5 + [True])


class TestBotaoTelefone(unittest.TestCase):
    """Tabela medida em 06/10 (relatório HID 3 do Jabra 410, byte 1):
       branco 00: verde 01 | vermelho 00;  espera 10: verde e vermelho 10→00;  3 verdes 01: verde 11→01 | vermelho 00.
       Ecos das luzes: entrar em 01 → 01; sair de 01 → 00; entrar em 10 não ecoa."""

    def novo(self, comando, t=0.0):
        from detectores import DetectorBotaoTelefone
        d = DetectorBotaoTelefone()
        d.comandou(comando, agora=t)
        return d

    def test_eco_das_luzes_nao_e_toque(self):
        d = self.novo(0x01)
        self.assertIsNone(d.relatorio(0x01, agora=0.1))
        d.comandou(0x10, agora=5.0)
        self.assertIsNone(d.relatorio(0x00, agora=5.2))

    def test_luz_alarme_devolve_ambos(self):
        self.assertEqual(self.novo(0x10).relatorio(0x00, agora=5.0), "ambos")

    def test_luz_alarme_corta_uma_vez(self):
        d = self.novo(0x10)
        self.assertEqual(d.relatorio(0x10, agora=5.0), "ambos")
        self.assertIsNone(d.relatorio(0x00, agora=5.2))   # soltura do mesmo toque

    def test_luz_ouvindo_separa_verde_e_vermelho(self):
        # Medido na bancada de 04/11: com 0x11 o verde manda 11,01 e o vermelho manda 00.
        self.assertEqual(self.novo(0x11).relatorio(0x11, agora=5.0), "verde")
        self.assertEqual(self.novo(0x11).relatorio(0x00, agora=5.0), "vermelho")

    def test_eco_do_comando_ouvindo_nao_e_toque(self):
        self.assertIsNone(self.novo(0x11).relatorio(0x01, agora=0.5))

    def test_luz_cerebro_separa_verde_e_vermelho(self):
        self.assertEqual(self.novo(0x01).relatorio(0x11, agora=5.0), "verde")
        self.assertEqual(self.novo(0x01).relatorio(0x00, agora=5.0), "vermelho")

    def test_luz_nao_perturbe_vermelho(self):
        self.assertEqual(self.novo(0x41).relatorio(0x00, agora=5.0), "vermelho")

    def test_nao_perturbe_41_verde_e_eco_nao(self):
        # Medido em 08/10 com 0x41 aceso: eco 01 em 0,5 s; verde 11→01.
        d = self.novo(0x41)
        self.assertIsNone(d.relatorio(0x01, agora=0.5))   # eco de entrar no 0x41
        self.assertIsNone(d.relatorio(0x01, agora=5.0))   # nem verde (bit 10) nem vermelho (00)
        self.assertEqual(d.relatorio(0x11, agora=8.0), "verde")

    def test_repouso_separa_verde_e_vermelho(self):
        self.assertEqual(self.novo(0x00).relatorio(0x01, agora=5.0), "verde")
        self.assertEqual(self.novo(0x00).relatorio(0x00, agora=5.0), "vermelho")

    def test_eco_da_troca_de_luz_nao_vira_vermelho(self):
        # Review Focus 1: trocar a luz devolve um relatório 00 igual ao do vermelho.
        d = self.novo(0x01)
        d.comandou(0x00, agora=10.0)
        self.assertIsNone(d.relatorio(0x00, agora=10.5))

    def test_toque_s_continua_valendo(self):
        d = self.novo(0x00)
        self.assertEqual(d.relatorio(0x00, agora=5.0), "vermelho")
        self.assertIsNone(d.relatorio(0x00, agora=5.5))

    def test_eco_atrasado_por_cpu_ocupada_nao_e_toque(self):
        # M9 (revisão 08/10): com a CPU ocupada, o eco pode chegar depois dos 0,6 s antigos e virar um
        # toque falso, cortando a conversa recém-aberta. 0,8 s ainda é o eco de entrar em "ouvindo".
        d = self.novo(0x10)
        self.assertIsNone(d.relatorio(0x10, agora=0.8))

class TestPortaoDeEco(unittest.TestCase):
    """Spec 2026-11-04: portão de energia no microfone enquanto ela fala; desligado (None) não muda nada."""

    @staticmethod
    def pcm(amp, n=1280):
        import array
        return array.array("h", [amp, -amp] * (n // 2)).tobytes()

    def test_portao_desligado_passa_tudo(self):
        from detectores import portao_eco
        baixo = self.pcm(100)
        self.assertEqual(portao_eco(baixo, True, None), baixo)

    def test_portao_so_age_com_ela_falando(self):
        from detectores import portao_eco
        baixo = self.pcm(100)
        self.assertEqual(portao_eco(baixo, False, -30.0), baixo)

    def test_portao_zera_abaixo_do_limiar(self):
        from detectores import portao_eco
        baixo = self.pcm(100)  # ~ -50 dBFS
        out = portao_eco(baixo, True, -30.0)
        self.assertEqual(out, bytes(len(baixo)))

    def test_portao_passa_acima_do_limiar(self):
        from detectores import portao_eco
        alto = self.pcm(10000)  # ~ -10 dBFS
        self.assertEqual(portao_eco(alto, True, -30.0), alto)

    def test_rms_dbfs_silencio(self):
        from detectores import rms_dbfs
        self.assertEqual(rms_dbfs(bytes(320)), -120.0)

    def test_rms_dbfs_valor_conhecido(self):
        from detectores import rms_dbfs
        self.assertAlmostEqual(rms_dbfs(self.pcm(16384)), -6.02, places=1)

    def test_medidor_resumo_e_zera(self):
        from detectores import MedidorEco
        m = MedidorEco()
        self.assertIsNone(m.resumo())
        for amp in range(100, 1100, 100):
            m.alimentar(self.pcm(amp))
        r = m.resumo()
        self.assertEqual(r["n"], 10)
        self.assertLessEqual(r["p50"], r["p90"])
        self.assertLessEqual(r["p90"], r["max"])
        self.assertIsNone(m.resumo())


if __name__ == "__main__":
    unittest.main()


class TestPicoDaFala(unittest.TestCase):
    """20/10 (instrumentação): nota que o modelo base deu a cada trecho de fala, mesmo abaixo de 0,3."""
    def _trecho(self, quadros, fim="fala_parou"):
        from detectores import PicoDaFala
        p = PicoDaFala()
        saidas = [p.alimentar("fala_comecou", *quadros[0])]
        saidas += [p.alimentar(None, *q) for q in quadros[1:]]
        saidas.append(p.alimentar(fim, 0.0, False, 0.0, 0.0, False))
        return saidas

    def test_pico_baixo_do_modelo_base(self):
        s = self._trecho([(0.02, False, 0.0, 0.9, False), (0.12, False, 0.0, 0.95, False)])
        self.assertEqual(s[:-1], [None, None])
        self.assertEqual(s[-1], {"pico_base": 0.12, "verificador_consultas": 0, "nota_verificador": 0.0,
                                 "voz": 0.95, "duracao_s": 0.16, "ativou": False})

    def test_quadro_com_verificador_nao_entra_no_pico_base(self):
        s = self._trecho([(0.1, False, 0.0, 0.8, False), (0.07, True, 0.07, 0.9, False)])
        self.assertEqual(s[-1]["pico_base"], 0.1)
        self.assertEqual(s[-1]["verificador_consultas"], 1)
        self.assertEqual(s[-1]["nota_verificador"], 0.07)

    def test_marca_quando_ativou(self):
        s = self._trecho([(0.3, False, 0.0, 0.9, False), (0.92, True, 0.92, 0.98, True)])
        self.assertTrue(s[-1]["ativou"])

    def test_inferencia_pulada_nao_conta(self):
        from detectores import PicoDaFala
        p = PicoDaFala()
        p.alimentar("fala_comecou", None, False, 0.0, 0.9, False)
        p.alimentar(None, None, False, 0.0, 0.9, False)
        self.assertIsNone(p.alimentar("fala_parou", None, False, 0.0, 0.0, False))

    def test_fora_de_trecho_nao_emite(self):
        from detectores import PicoDaFala
        p = PicoDaFala()
        self.assertIsNone(p.alimentar(None, 0.9, False, 0.0, 0.9, False))
        self.assertIsNone(p.alimentar("fala_parou", 0.9, False, 0.0, 0.9, False))


class TestEscolherAcao(unittest.TestCase):
    def test_escolher_acao(self):
        from detectores import escolher_acao
        tabela = [(("ativa", True, False), "evento"), (("ativa", True, True), None),
                  (("sombra", True, False), "sombra"), (("sombra", True, True), "sombra"),
                  (("desligada", True, False), None), (("ativa", False, False), None),
                  (("sombra", False, False), None)]
        for args, esperado in tabela:
            self.assertEqual(escolher_acao(*args), esperado, args)


class TestPicoComExtras(unittest.TestCase):
    def test_pico_da_fala_com_extras(self):
        from detectores import PicoDaFala
        p = PicoDaFala()
        p.alimentar("fala_comecou", 0.1, False, 0.0, 0.9, False, extras={"sexta_feira": 0.2})
        p.alimentar(None, 0.1, False, 0.0, 0.9, False, extras={"sexta_feira": 0.7})
        t = p.alimentar("fala_parou", 0.1, False, 0.0, 0.9, False)
        self.assertEqual(t["picos_extras"], {"sexta_feira": 0.7})

    def test_pico_da_fala_sem_extras_sem_chave(self):
        from detectores import PicoDaFala
        p = PicoDaFala()
        p.alimentar("fala_comecou", 0.1, False, 0.0, 0.9, False)
        p.alimentar(None, 0.3, False, 0.0, 0.9, False)
        t = p.alimentar("fala_parou", 0.1, False, 0.0, 0.9, False)
        self.assertNotIn("picos_extras", t)


class TestPaciencia(unittest.TestCase):
    """02/11: com paciência N, só dispara depois de N quadros seguidos acima do limiar (medido offline na Sexta:
    paciência 2 tirou ~60% dos disparos falsos da sombra e 1 de 40 positivos nativos)."""

    def test_padrao_um_quadro_basta(self):
        self.assertTrue(DetectorPalavra(0.5).alimentar(0.6))

    def test_pico_isolado_nao_dispara_com_paciencia_dois(self):
        d = DetectorPalavra(0.5, paciencia=2)
        self.assertFalse(d.alimentar(0.9))
        self.assertFalse(d.alimentar(0.3))
        self.assertFalse(d.alimentar(0.9))

    def test_dois_quadros_seguidos_disparam_uma_vez(self):
        d = DetectorPalavra(0.5, paciencia=2)
        self.assertFalse(d.alimentar(0.6))
        self.assertTrue(d.alimentar(0.7))
        self.assertFalse(d.alimentar(0.8))
        d.alimentar(0.1)
        self.assertFalse(d.alimentar(0.6))
        self.assertTrue(d.alimentar(0.6))

    def test_quadro_sem_voz_zera_a_contagem(self):
        d = DetectorPalavra(0.5, limiar_voz=0.6, paciencia=2)
        self.assertFalse(d.alimentar(0.9, voz=0.9))
        self.assertFalse(d.alimentar(0.9, voz=0.1))
        self.assertFalse(d.alimentar(0.9, voz=0.9))
        self.assertTrue(d.alimentar(0.9, voz=0.9))



class TestTrechoDoEpisodio(unittest.TestCase):
    def test_episodio_mais_a_folga_antes(self):
        ctx = list(range(100))
        self.assertEqual(trecho_do_episodio(ctx, 4, antes=25), list(range(71, 100)))

    def test_limitado_ao_contexto(self):
        self.assertEqual(trecho_do_episodio([1, 2, 3], 10, antes=25), [1, 2, 3])


class TestRejeicaoEpisodio(unittest.TestCase):
    def _rodar(self, quadros, **kw):  # quadros: (nota, voz, disparou, consultado)
        r, out = RejeicaoEpisodio(), []
        for n, v, d, c in quadros:
            x = r.alimentar(n, v, 0.35, 0.6, d, consultado=c, **kw)
            if x:
                out.append(x)
        return out

    def test_caso_0943_vira_sem_voz(self):
        out = self._rodar([(0.83, 0.52, False, True), (0.86, 0.52, False, True), (0.1, 0.1, False, False)])
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0]["causa"], out[0]["nota"], out[0]["voz"]), ("sem_voz", 0.86, 0.52))

    def test_verificador_puxou_para_baixo(self):
        r = RejeicaoEpisodio()
        r.alimentar(0.07, 0.08, 0.35, 0.6, False, consultado=True, nota_verificador=0.07)
        x = r.alimentar(0.05, 0.05, 0.35, 0.6, False, consultado=False)
        self.assertEqual((x["causa"], x["nota_verificador"]), ("verificador", 0.07))

    def test_paciencia(self):
        out = self._rodar([(0.6, 0.9, False, False), (0.1, 0.9, False, False)])
        self.assertEqual(out[0]["causa"], "paciencia")

    def test_conta_os_quadros_do_episodio(self):
        # 09/11: o clipe da rejeição por paciência é recortado do contexto pelo tamanho do episódio
        out = self._rodar([(0.52, 0.99, False, False), (0.3, 0.99, False, False), (0.25, 0.9, False, False),
                           (0.1, 0.9, False, False)])
        self.assertEqual(out[0]["quadros"], 4)

    def test_sussurro_conta_como_voz(self):
        r = RejeicaoEpisodio()
        r.alimentar(0.6, 0.1, 0.35, 0.6, False, sussurro=True, razao=-4.0)
        x = r.alimentar(0.1, 0.1, 0.35, 0.6, False)
        self.assertEqual((x["causa"], x["razao_sussurro_db"]), ("paciencia", -4.0))

    def test_disparou_nao_rejeita(self):
        self.assertEqual(self._rodar([(0.6, 0.3, False, False), (0.7, 0.9, True, False), (0.1, 0.9, False, False)]), [])

    def test_abaixo_do_limiar_sem_consulta_nao_abre(self):
        self.assertEqual(self._rodar([(0.3, 0.9, False, False), (0.1, 0.9, False, False)]), [])

    def test_gravacao_primeira_do_episodio(self):
        r = RejeicaoEpisodio()
        r.anotar_gravacao("fora.wav")
        r.alimentar(0.5, 0.1, 0.35, 0.6, False)
        r.anotar_gravacao("/x/sem_voz_20261103_094322.wav"); r.anotar_gravacao("/x/barrada_20261103_094323.wav")
        self.assertEqual(r.alimentar(0.1, 0.1, 0.35, 0.6, False)["gravacao"], "/x/sem_voz_20261103_094322.wav")

    def test_um_resultado_por_episodio(self):
        out = self._rodar([(0.5, 0.1, False, False), (0.1, 0.1, False, False), (0.1, 0.1, False, False)])
        self.assertEqual(len(out), 1)
