"""Testes do Maestro: cd /homeassistant/tools/satelite/programa && python3 -m unittest test_maestro -v"""
import unittest
from maestro import Ajustes, Maestro


def tipos(acoes):
    return [a[0] if a[1] is None else f"{a[0]}:{a[1]}" for a in acoes if a[0] not in ("registrar", "registrar_espera")]


def registro(acoes):
    return next(a[1] for a in acoes if a[0] == "registrar")


class Base(unittest.TestCase):
    def setUp(self):
        self.m = Maestro(Ajustes())

    def abrir_e_conectar(self, t=0.0):
        self.m.evento("palavra", t)
        self.m.evento("conectou", t + 1)


class TestAbrir(Base):
    def test_palavra_abre_com_som_luz_e_conexao(self):
        self.assertEqual(tipos(self.m.evento("palavra", 0.0)), ["som:inicio", "luz:ouvindo", "conectar"])
        self.assertEqual(self.m.estado, "conectando")

    def test_botao_parado_abre_igual_a_palavra(self):
        self.assertEqual(tipos(self.m.evento("botao", 0.0)), ["som:inicio", "luz:ouvindo", "conectar"])

    def test_palavra_dupla_nao_reconecta(self):
        self.m.evento("palavra", 0.0)
        self.assertEqual(self.m.evento("palavra", 0.3), [])

    def test_palavra_ignorada_ouvindo(self):
        self.abrir_e_conectar()
        self.assertEqual(self.m.evento("palavra", 2.0), [])
        self.assertEqual(self.m.estado, "ouvindo")


class TestEsperaInicial(Base):
    def test_sem_fala_em_4s_fecha(self):
        self.abrir_e_conectar()
        self.assertEqual(self.m.evento("tique", 3.9), [])
        acoes = self.m.evento("tique", 4.0)
        self.assertEqual(tipos(acoes), ["som:fim", "luz:parada", "desconectar"])
        self.assertEqual(registro(acoes)["como_fechou"], "sem_fala")
        self.assertEqual(self.m.estado, "parada")

    def test_fala_cancela_espera_inicial(self):
        self.abrir_e_conectar()
        self.m.evento("fala_comecou", 1.5)
        self.assertEqual(self.m.evento("tique", 10.0), [])

    def test_espera_conta_mesmo_conectando(self):
        self.m.evento("palavra", 0.0)
        self.assertEqual(tipos(self.m.evento("tique", 4.0)), ["som:fim", "luz:parada", "desconectar"])


class TestFalaLonga(Base):
    """Spec 2026-10-21 R13: música cantada que o VAD confunde com voz não pode manter o microfone aberto."""
    def test_continuous_speech_closes_after_speech_max_s(self):
        self.abrir_e_conectar()
        self.m.evento("fala_comecou", 0.5)
        self.assertEqual(self.m.evento("tique", 15.4), [])
        acoes = self.m.evento("tique", 15.6)
        self.assertIn(("desconectar", None), acoes)
        self.assertEqual(registro(acoes)["como_fechou"], "fala_longa")

    def test_speech_pauses_reset_the_ceiling(self):
        self.abrir_e_conectar()
        self.m.evento("fala_comecou", 0.5)
        self.m.evento("fala_parou", 10.0)
        self.m.evento("fala_comecou", 10.3)
        self.assertNotIn(("desconectar", None), self.m.evento("tique", 20.0))
        self.assertEqual(self.m.estado, "ouvindo")

    def test_brain_pending_is_not_cut(self):
        self.abrir_e_conectar()
        self.m.evento("cerebro_pendente", 1.0, 1)
        self.m.evento("fala_comecou", 2.0)
        self.assertNotIn(("desconectar", None), self.m.evento("tique", 32.0))

    def test_whispered_open_with_speech_in_progress_also_has_ceiling(self):
        self.m.evento("palavra", 0.0, {"whisper": True})
        self.m.evento("conectou", 0.5)
        acoes = self.m.evento("tique", 16.0)
        self.assertEqual(registro(acoes)["como_fechou"], "fala_longa")

    def test_zero_turns_the_ceiling_off(self):  # 10/11: ditado longo no MK1; com ou sem música, nunca fecha
        self.m = Maestro(Ajustes(speech_max_s=0))
        self.abrir_e_conectar()
        self.m.evento("musica", 1.0, True)
        self.m.evento("fala_comecou", 0.5)
        self.assertNotIn(("desconectar", None), self.m.evento("tique", 60.0))
        self.assertEqual(self.m.estado, "ouvindo")

    def test_her_speech_is_not_counted(self):
        self.abrir_e_conectar()
        self.m.evento("fala_comecou", 0.5)
        self.m.evento("ela_comecou", 1.0)
        self.assertNotIn(("desconectar", None), self.m.evento("tique", 30.0))
        self.assertEqual(self.m.estado, "ouvindo")


class TestContinuacao(Base):
    def conversa(self):
        self.abrir_e_conectar()
        self.m.evento("fala_comecou", 1.2); self.m.evento("fala_parou", 2.5)
        self.m.evento("ela_comecou", 3.0); self.m.evento("ela_parou", 5.0)

    def test_8s_de_silencio_depois_dela_fecha(self):
        self.conversa()
        self.assertEqual(self.m.evento("tique", 12.9), [])
        acoes = self.m.evento("tique", 13.0)
        self.assertEqual(tipos(acoes), ["som:fim", "luz:parada", "desconectar"])
        self.assertEqual(registro(acoes)["como_fechou"], "silencio")

    def test_pedido_encadeado_mantem_aberto(self):
        self.conversa()
        self.m.evento("fala_comecou", 8.0)
        self.assertEqual(self.m.evento("tique", 13.0), [])
        self.m.evento("fala_parou", 9.0); self.m.evento("ela_comecou", 9.5); self.m.evento("ela_parou", 11.0)
        self.assertEqual(self.m.evento("tique", 18.9), [])
        self.assertEqual(tipos(self.m.evento("tique", 19.0)), ["som:fim", "luz:parada", "desconectar"])

    def test_cerebro_pendente_vai_para_aguardando(self):
        self.conversa()
        self.m.evento("cerebro_pendente", 6.0, 1)
        acoes = self.m.evento("tique", 13.0)
        self.assertEqual(tipos(acoes), ["som:fim", "luz:cerebro", "microfone:False"])
        self.assertEqual(self.m.estado, "aguardando")

    def test_resposta_que_nunca_vem_fecha_calada_em_8s(self):
        # spec 2026-10-15 D1: conversa aberta pela palavra; ela calada (áudio proativo) fecha em 8 s, não em 20 s
        self.abrir_e_conectar()
        self.m.evento("fala_comecou", 1.2); self.m.evento("fala_parou", 2.0)
        self.assertEqual(self.m.evento("tique", 9.9), [])
        self.assertEqual(registro(self.m.evento("tique", 10.0))["como_fechou"], "calada")


class TestCorte(Base):
    def test_botao_ouvindo_corta_e_fecha_depois_da_resposta(self):
        self.abrir_e_conectar()
        self.m.evento("fala_comecou", 1.2)
        self.assertEqual(tipos(self.m.evento("botao", 2.0)), ["som:fim", "luz:parada", "cortar"])
        self.assertEqual(self.m.estado, "cortada")
        self.m.evento("ela_comecou", 4.0)
        acoes = self.m.evento("ela_parou", 6.0)
        self.assertEqual(tipos(acoes), ["luz:parada", "desconectar"])
        self.assertEqual(registro(acoes)["como_fechou"], "botao")

    def test_corte_sem_continuacao_mesmo_com_tempo(self):
        self.abrir_e_conectar()
        self.m.evento("botao", 2.0); self.m.evento("ela_comecou", 3.0)
        self.assertEqual(self.m.evento("tique", 3.5), [])
        self.assertEqual(self.m.estado, "cortada")

    def test_botao_durante_conectando_corta(self):
        self.m.evento("palavra", 0.0)
        self.assertEqual(tipos(self.m.evento("botao", 0.5)), ["som:fim", "luz:parada", "cortar"])

    def test_mudo_fisico_ouvindo_corta(self):
        self.abrir_e_conectar()
        acoes = self.m.evento("mudo", 2.0, True)
        self.assertEqual(tipos(acoes), ["som:fim", "cortar"])  # luz filtrada: o Jabra mostra o vermelho
        self.assertEqual(self.m.estado, "cortada")

    def test_corte_com_cerebro_pendente_aguarda(self):
        self.abrir_e_conectar()
        self.m.evento("cerebro_pendente", 1.5, 1)
        self.m.evento("botao", 2.0); self.m.evento("ela_comecou", 3.0)
        self.assertEqual(tipos(self.m.evento("ela_parou", 4.0)), ["luz:cerebro"])
        self.assertEqual(self.m.estado, "aguardando")


class TestCerebro(Base):
    def aguardando(self):
        self.abrir_e_conectar()
        self.m.evento("fala_comecou", 1.2); self.m.evento("fala_parou", 2.0)
        self.m.evento("cerebro_pendente", 2.5, 1)
        self.m.evento("ela_comecou", 3.0); self.m.evento("ela_parou", 4.0)
        self.m.evento("tique", 12.0)
        assert self.m.estado == "aguardando"

    def test_entrega_reabre_com_som_de_inicio(self):
        self.aguardando()
        self.assertEqual(tipos(self.m.evento("cerebro_entregando", 40.0)),
                         ["som:inicio", "luz:ouvindo", "microfone:True"])
        self.m.evento("ela_comecou", 40.5)
        self.m.evento("cerebro_pendente", 40.6, 0)
        self.m.evento("ela_parou", 45.0)
        self.assertEqual(tipos(self.m.evento("tique", 53.0)), ["som:fim", "luz:parada", "desconectar"])

    def test_palavra_aguardando_reabre_na_mesma_conversa(self):
        self.aguardando()
        self.assertEqual(tipos(self.m.evento("palavra", 20.0)), ["som:inicio", "luz:ouvindo", "microfone:True"])
        self.assertEqual(self.m.estado, "ouvindo")

    def test_pendentes_zerados_aguardando_espera_fala_e_fecha_sem_som(self):
        # 06/10: o cérebro avisa "0" logo que devolve; a fala dela vem ~1 s depois. Não fechar na hora.
        self.aguardando()
        self.assertEqual(self.m.evento("cerebro_pendente", 30.0, 0), [])
        self.assertEqual(self.m.evento("tique", 37.9), [])
        acoes = self.m.evento("tique", 38.0)
        self.assertEqual(tipos(acoes), ["luz:parada", "desconectar"])
        self.assertEqual(registro(acoes)["como_fechou"], "cerebro_concluido")

    def test_cerebro_fim_nao_usa_continuacao_s(self):
        # M10 (revisão 08/10): a tolerância para a fala COMEÇAR depois do cérebro devolver reaproveitava
        # continuacao_s (tolerância para a fala CONTINUAR no meio da conversa) — um prazo curto para o
        # outro caso faz a resposta sumir calada. Agora são campos independentes de Ajustes.
        m = Maestro(Ajustes(espera_inicial_s=4.0, continuacao_s=2.0, cerebro_fim_s=12.0))
        m.evento("palavra", 0.0); m.evento("conectou", 1.0)
        m.evento("fala_comecou", 1.2); m.evento("fala_parou", 2.0)
        m.evento("cerebro_pendente", 2.5, 1)
        m.evento("ela_comecou", 3.0); m.evento("ela_parou", 4.0)
        m.evento("tique", 12.0)
        self.assertEqual(m.estado, "aguardando")
        m.evento("cerebro_pendente", 30.0, 0)
        # continuacao_s (2 s) teria fechado em 32 s; cerebro_fim_s (12 s) só fecha em 42 s.
        self.assertEqual(m.evento("tique", 35.0), [])
        self.assertEqual(m.evento("tique", 41.9), [])
        acoes = m.evento("tique", 42.0)
        self.assertEqual(tipos(acoes), ["luz:parada", "desconectar"])
        self.assertEqual(registro(acoes)["como_fechou"], "cerebro_concluido")

    def test_ela_falar_aguardando_reabre_como_entrega(self):
        # 06/10: resposta do cérebro dentro da janela de 8 s chega sem "cerebro_entregando".
        self.aguardando()
        self.m.evento("cerebro_pendente", 30.0, 0)
        self.assertEqual(tipos(self.m.evento("ela_comecou", 31.0)), ["som:inicio", "luz:ouvindo", "microfone:True"])
        self.assertEqual(self.m.estado, "ouvindo")
        self.m.evento("ela_parou", 35.0)
        self.assertEqual(self.m.evento("tique", 42.9), [])  # Ajustes() padrão: continuação de 8 s
        self.assertEqual(tipos(self.m.evento("tique", 43.0)), ["som:fim", "luz:parada", "desconectar"])

    def test_cerebro_comeca_dentro_da_continuacao(self):
        # 06/10 (caso real): "Deixa eu ver" + cérebro; a continuação de 5–8 s não pode fechar a conversa.
        self.abrir_e_conectar()
        self.m.evento("fala_comecou", 1.2); self.m.evento("fala_parou", 2.0)
        self.m.evento("ela_comecou", 2.5); self.m.evento("cerebro_pendente", 2.6, 1); self.m.evento("ela_parou", 3.0)
        self.assertEqual(tipos(self.m.evento("tique", 11.0)), ["som:fim", "luz:cerebro", "microfone:False"])
        self.assertEqual(self.m.estado, "aguardando")


class TestNuncaFechaComCerebro(Base):
    """06/10 (caso real 12:12): a palavra reabriu o microfone durante a espera, 4 s sem fala fecharam a
    conversa e a resposta do cérebro chegou 2 s depois, perdida. Com cérebro pendente, prazos voltam a aguardar."""

    def aguardando(self):
        self.abrir_e_conectar()
        self.m.evento("fala_comecou", 1.2); self.m.evento("fala_parou", 2.0)
        self.m.evento("cerebro_pendente", 2.5, 1)
        self.m.evento("ela_comecou", 3.0); self.m.evento("ela_parou", 4.0)
        self.m.evento("tique", 12.0)
        assert self.m.estado == "aguardando"

    def test_espera_inicial_com_cerebro_volta_a_aguardar(self):
        self.aguardando()
        self.m.evento("palavra", 20.0)
        acoes = self.m.evento("tique", 24.0)
        self.assertEqual(tipos(acoes), ["som:fim", "luz:cerebro", "microfone:False"])
        self.assertEqual(self.m.estado, "aguardando")

    def test_resposta_sem_fala_com_cerebro_volta_a_aguardar(self):
        self.aguardando()
        self.m.evento("palavra", 20.0); self.m.evento("fala_comecou", 21.0); self.m.evento("fala_parou", 22.0)
        self.assertEqual(tipos(self.m.evento("tique", 42.0)), ["som:fim", "luz:cerebro", "microfone:False"])
        self.assertEqual(self.m.estado, "aguardando")



class TestCerebroDevolveComConversaAberta(unittest.TestCase):
    """19/10 (caso real 10:46): o cérebro devolveu 45 ms antes de vencer a continuação de 5 s, ainda em "ouvindo";
    sem nada pendente o prazo fechou a conversa por "silencio" e a resposta nunca foi falada. A resposta devolvida e
    ainda não falada conta como pendente até ela começar a falar (teto cerebro_fim_s). O microfone fecha no mesmo
    instante em que fecharia antes: a conversa só espera a fala dela, como nos casos que já iam para "aguardando"."""

    def setUp(self):
        self.m = Maestro(Ajustes(espera_inicial_s=4.0, continuacao_s=5.0, cerebro_fim_s=8.0))
        self.m.evento("palavra", 0.0); self.m.evento("conectou", 1.0)
        self.m.evento("fala_comecou", 36.7); self.m.evento("fala_parou", 38.7)
        self.m.evento("ela_comecou", 39.5); self.m.evento("cerebro_pendente", 39.7, 1)
        self.m.evento("ela_parou", 40.68)  # continuação vence em 45.68
        self.m.evento("cerebro_pendente", 45.635, 0)

    def test_prazo_vencido_logo_apos_o_cerebro_vai_aguardar_e_fecha_o_microfone(self):
        acoes = self.m.evento("tique", 45.7)
        self.assertEqual(tipos(acoes), ["som:fim", "luz:cerebro", "microfone:False"])
        self.assertEqual(self.m.estado, "aguardando")

    def test_fala_dela_reabre_a_conversa(self):
        self.m.evento("tique", 45.7)
        self.assertEqual(tipos(self.m.evento("ela_comecou", 46.5)), ["som:inicio", "luz:ouvindo", "microfone:True"])

    def test_sem_fala_fecha_calado_em_cerebro_fim_s_a_partir_da_resposta(self):
        self.m.evento("tique", 45.7)
        self.assertEqual(self.m.evento("tique", 53.6), [])
        acoes = self.m.evento("tique", 53.7)
        self.assertEqual(tipos(acoes), ["luz:parada", "desconectar"])
        self.assertEqual(registro(acoes)["como_fechou"], "cerebro_concluido")


class TestRespostaFaladaNaoSegura(unittest.TestCase):
    """Depois que ela começa a falar a resposta, a conversa volta às regras normais: o microfone não fica aberto
    nem a conversa espera mais do que antes."""

    def test_continuacao_normal_depois_da_fala_da_resposta(self):
        m = Maestro(Ajustes(espera_inicial_s=4.0, continuacao_s=5.0, cerebro_fim_s=8.0))
        m.evento("palavra", 0.0); m.evento("conectou", 1.0)
        m.evento("fala_comecou", 1.2); m.evento("fala_parou", 2.0)
        m.evento("ela_comecou", 2.5); m.evento("cerebro_pendente", 2.6, 1); m.evento("ela_parou", 3.0)
        m.evento("cerebro_pendente", 5.0, 0)
        m.evento("ela_comecou", 5.8); m.evento("ela_parou", 9.0)
        self.assertEqual(m.evento("tique", 13.9), [])
        acoes = m.evento("tique", 14.0)
        self.assertEqual(tipos(acoes), ["som:fim", "luz:parada", "desconectar"])
        self.assertEqual(registro(acoes)["como_fechou"], "silencio")


class TestFalhas(Base):
    def test_falha_ao_conectar_toca_erro(self):
        self.m.evento("palavra", 0.0)
        acoes = self.m.evento("falha_conexao", 1.0)
        self.assertEqual(tipos(acoes), ["som:erro", "luz:parada", "desconectar"])
        self.assertEqual(registro(acoes)["como_fechou"], "falha")

    def test_caiu_e_reabre(self):
        self.abrir_e_conectar()
        self.assertEqual(tipos(self.m.evento("caiu", 3.0)), ["som:erro", "luz:parada", "desconectar"])
        self.assertEqual(self.m.estado, "parada")
        self.assertEqual(tipos(self.m.evento("palavra", 5.0)), ["som:inicio", "luz:ouvindo", "conectar"])

    def test_caiu_parado_nao_faz_nada(self):
        self.assertEqual(self.m.evento("caiu", 1.0), [])


class TestMudo(Base):
    def test_palavra_e_botao_ignorados_com_mudo(self):
        self.m.evento("mudo", 0.0, True)
        self.assertEqual(self.m.evento("palavra", 1.0), [])
        self.assertEqual(self.m.evento("botao", 2.0), [])

    def test_soltar_mudo_restaura_luz(self):
        self.m.evento("mudo", 0.0, True)
        self.assertEqual(tipos(self.m.evento("mudo", 5.0, False)), ["luz:parada"])


class TestRegistro(Base):
    def test_registro_tem_campos_da_spec(self):
        self.m.evento("botao", 10.0); self.m.evento("conectou", 11.0)
        r = registro(self.m.evento("tique", 14.0))
        self.assertEqual(r["como_abriu"], "botao")
        self.assertEqual(r["duracao_s"], 4.0)
        self.assertIn("inicio", r)
        self.assertEqual(self.m.publico()["ultima_abertura"], r)


class TestEncerrar(Base):
    """07/10: "parar" (ou ela percebe que a conversa não é com ela) fecha NA HORA, sem continuação."""
    def test_ouvindo_fecha_na_hora_com_som_de_fim(self):
        self.abrir_e_conectar()
        self.m.evento("ela_comecou", 2.0)
        acoes = self.m.evento("encerrar", 2.5)
        self.assertEqual(tipos(acoes), ["som:fim", "luz:parada", "desconectar"])
        self.assertEqual(registro(acoes)["como_fechou"], "encerrar")
        self.assertEqual(self.m.estado, "parada")

    def test_descarta_cerebro_pendente(self):
        self.abrir_e_conectar()
        self.m.evento("cerebro_pendente", 2.0, 1)
        self.m.evento("ela_parou", 3.0)
        self.m.evento("tique", 12.0)  # continuação (8 s) vence com cérebro pendente: aguardando
        self.assertEqual(self.m.estado, "aguardando")
        self.assertEqual(tipos(self.m.evento("encerrar", 13.0)), ["som:fim", "luz:parada", "desconectar"])
        self.assertEqual(self.m.pendentes, 0)

    def test_cortada_nao_repete_som(self):
        self.abrir_e_conectar()
        self.m.evento("botao", 2.0)
        self.assertEqual(tipos(self.m.evento("encerrar", 2.5)), ["luz:parada", "desconectar"])

    def test_parada_ignora(self):
        self.assertEqual(self.m.evento("encerrar", 1.0), [])


class TestNaoPerturbe(Base):
    def test_luz_de_repouso_muda(self):
        self.assertEqual(tipos(self.m.evento("nao_perturbe", 0.0, True)), ["som:nao_perturbe_ligou", "luz:nao_perturbe"])
        self.abrir_e_conectar(1.0)
        self.assertEqual(tipos(self.m.evento("tique", 5.0)), ["som:fim", "luz:nao_perturbe", "desconectar"])

    def test_palavra_funciona_normalmente(self):
        self.m.evento("nao_perturbe", 0.0, True)
        self.assertEqual(tipos(self.m.evento("palavra", 1.0)), ["som:inicio", "luz:ouvindo", "conectar"])

    def test_entrega_espontanea_e_calada(self):
        self.m.evento("nao_perturbe", 0.0, True)
        self.abrir_e_conectar(1.0)
        self.m.evento("fala_comecou", 1.5); self.m.evento("fala_parou", 2.0)
        self.m.evento("cerebro_pendente", 2.5, 1)
        self.m.evento("ela_comecou", 3.0); self.m.evento("ela_parou", 4.0)
        self.m.evento("tique", 12.0)
        acoes = self.m.evento("cerebro_entregando", 30.0)
        self.assertEqual(tipos(acoes), ["luz:nao_perturbe", "desconectar"])
        self.assertEqual(registro(acoes)["como_fechou"], "nao_perturbe")

    def test_desligar_volta_ao_branco(self):
        self.m.evento("nao_perturbe", 0.0, True)
        self.assertEqual(tipos(self.m.evento("nao_perturbe", 1.0, False)), ["som:nao_perturbe_desligou", "luz:parada"])

    def test_ligar_no_meio_da_conversa_nao_troca_a_luz_de_ouvindo(self):
        self.abrir_e_conectar()
        self.assertEqual(tipos(self.m.evento("nao_perturbe", 2.0, True)), ["som:nao_perturbe_ligou", "luz:ouvindo"])

    def test_soltar_mudo_volta_a_luz_do_nao_perturbe(self):
        self.m.evento("nao_perturbe", 0.0, True)
        self.m.evento("mudo", 1.0, True)
        self.assertEqual(tipos(self.m.evento("mudo", 2.0, False)), ["luz:nao_perturbe"])

    def test_encerrar_volta_a_luz_do_nao_perturbe(self):
        self.m.evento("nao_perturbe", 0.0, True)
        self.abrir_e_conectar(1.0)
        self.assertEqual(tipos(self.m.evento("encerrar", 2.0)), ["som:fim", "luz:nao_perturbe", "desconectar"])

    def test_sobrevive_ao_fim_da_conversa(self):
        self.m.evento("nao_perturbe", 0.0, True)
        self.abrir_e_conectar(1.0)
        self.m.evento("tique", 5.0)
        self.assertTrue(self.m.publico()["nao_perturbe"])

    # ---- spec 2026-10-20: som de confirmação quando o não perturbe MUDA de fato
    def test_pedido_sem_mudanca_nao_toca_som(self):
        self.m.evento("nao_perturbe", 0.0, True)
        self.assertEqual(tipos(self.m.evento("nao_perturbe", 1.0, True)), ["luz:nao_perturbe"])

    # 20/10 (Alex, depois do teste ao vivo): na conversa o som toca NA HORA do pedido e o fim daquela conversa some
    def test_mudanca_na_conversa_toca_na_hora_e_tira_o_fim(self):
        self.abrir_e_conectar()
        self.assertEqual(tipos(self.m.evento("nao_perturbe", 2.0, True)), ["som:nao_perturbe_ligou", "luz:ouvindo"])
        self.assertEqual(tipos(self.m.evento("encerrar", 2.1)), ["luz:nao_perturbe", "desconectar"])

    def test_desligar_na_conversa_toca_na_hora_e_tira_o_fim(self):
        self.m.evento("nao_perturbe", 0.0, True)
        self.abrir_e_conectar(1.0)
        self.assertEqual(tipos(self.m.evento("nao_perturbe", 3.0, False))[0], "som:nao_perturbe_desligou")
        self.assertNotIn("som:fim", tipos(self.m.evento("encerrar", 3.5)))

    def test_sem_mudanca_na_conversa_mantem_o_fim(self):
        self.m.evento("nao_perturbe", 0.0, True)
        self.abrir_e_conectar(1.0)
        self.assertEqual(tipos(self.m.evento("nao_perturbe", 3.0, True)), ["luz:ouvindo"])
        self.assertEqual(tipos(self.m.evento("encerrar", 3.5)), ["som:fim", "luz:nao_perturbe", "desconectar"])

    def test_duas_mudancas_tocam_as_duas_na_hora(self):
        self.abrir_e_conectar()
        self.assertEqual(tipos(self.m.evento("nao_perturbe", 2.0, True))[0], "som:nao_perturbe_ligou")
        self.assertEqual(tipos(self.m.evento("nao_perturbe", 2.2, False))[0], "som:nao_perturbe_desligou")
        self.assertNotIn("som:fim", tipos(self.m.evento("encerrar", 2.5)))

    def test_corte_depois_da_mudanca_nao_toca_fim(self):
        self.abrir_e_conectar()
        self.m.evento("nao_perturbe", 1.5, True)
        self.assertNotIn("som:fim", tipos(self.m.evento("botao", 2.0)))  # cortada
        self.assertEqual(tipos(self.m.evento("encerrar", 2.5)), ["luz:nao_perturbe", "desconectar"])

    def test_aguardar_cerebro_depois_da_mudanca_nao_toca_fim(self):
        self.abrir_e_conectar()
        self.m.evento("cerebro_pendente", 1.2, 1)
        self.m.evento("nao_perturbe", 1.5, True)
        self.assertNotIn("som:fim", tipos(self.m.evento("encerrar", 2.0, "agradecimento")))
        self.assertEqual(self.m.estado, "aguardando")

    def test_proxima_conversa_volta_a_ter_fim(self):
        self.test_mudanca_na_conversa_toca_na_hora_e_tira_o_fim()
        self.abrir_e_conectar(10.0)
        self.assertEqual(tipos(self.m.evento("encerrar", 12.0)), ["som:fim", "luz:nao_perturbe", "desconectar"])

    def test_sussurro_nao_filtra_o_som_do_nao_perturbe(self):
        self.m.evento("palavra", 0.0, {"whisper": True})
        self.m.evento("conectou", 1.0)
        self.assertIn("som:nao_perturbe_ligou", tipos(self.m.evento("nao_perturbe", 2.0, True)))
        self.assertNotIn("som:fim", tipos(self.m.evento("encerrar", 2.5)))

    def test_estado_inicial_nao_toca_som(self):
        self.assertEqual(tipos(self.m.evento("nao_perturbe_inicial", 0.0, True)), ["luz:nao_perturbe"])
        self.assertIs(self.m.nao_perturbe, True)
        self.assertEqual(tipos(self.m.evento("nao_perturbe", 1.0, True)), ["luz:nao_perturbe"])

    def test_mudanca_com_alarme_toca_quando_o_alarme_acaba(self):
        self.m.evento("timer_venceu", 0.0, {"id": "t1", "nome": "x"})
        self.assertEqual(tipos(self.m.evento("nao_perturbe", 1.0, True)), ["luz:alarme"])
        t = tipos(self.m.evento("botao", 2.0))
        self.assertEqual(t[0], "som:nao_perturbe_ligou")


class TestMudoDesconhecidoNaPartida(unittest.TestCase):
    """Revisão final C1 (08/10): escrever 0x41/0x10 com o mudo físico ligado RELIGA o microfone (medido).
    Na partida o mudo é desconhecido até a captura decidir: nenhuma luz, palavra nem botão até lá."""
    def setUp(self):
        self.m = Maestro(Ajustes(), mudo_inicial=None)

    def test_nao_escreve_luz_nem_abre_ate_saber(self):
        self.assertEqual(self.m.evento("nao_perturbe_inicial", 0.0, True), [])  # o que o arranque manda desde 20/10
        self.assertEqual(self.m.evento("botao", 0.2), [])
        self.assertEqual(self.m.evento("palavra", 0.3), [])
        self.assertEqual(tipos(self.m.evento("mudo", 0.5, False)), ["luz:nao_perturbe"])

    def test_partida_com_mudo_ligado_segue_sem_luz(self):
        self.m.evento("nao_perturbe", 0.0, True)
        self.assertEqual(self.m.evento("mudo", 0.5, True), [])
        self.assertEqual(self.m.evento("botao", 1.0), [])
        self.assertEqual(tipos(self.m.evento("mudo", 9.0, False)), ["luz:nao_perturbe"])


class TestEventosDeConexaoVelha(Base):
    """Revisão final I1 (08/10): eventos que chegam com o satélite parado não podem contaminar a próxima conversa."""
    def test_cerebro_pendente_parado_e_ignorado(self):
        self.m.evento("cerebro_pendente", 0.0, 1)
        self.abrir_e_conectar(1.0)
        self.assertEqual(tipos(self.m.evento("tique", 5.5)), ["som:fim", "luz:parada", "desconectar"])

    def test_ela_comecou_parado_e_ignorado(self):
        self.m.evento("ela_comecou", 0.0)
        self.abrir_e_conectar(1.0)
        self.m.evento("fala_comecou", 2.0); self.m.evento("fala_parou", 3.0)
        self.m.evento("tique", 30.0)
        self.assertEqual(self.m.estado, "parada")


class TestEsperaCurta(Base):
    """Spec 2026-10-15 D1–D4: depois da fala humana, 8 s para ela começar; se ela não falar, fecha como "calada"."""

    def falou(self):
        self.abrir_e_conectar()
        self.m.evento("fala_comecou", 1.2); self.m.evento("fala_parou", 2.5)

    def test_sem_resposta_em_8s_fecha_calada(self):
        self.falou()
        self.assertEqual(self.m.evento("tique", 10.4), [])
        acoes = self.m.evento("tique", 10.5)
        self.assertEqual(tipos(acoes), ["som:fim", "luz:parada", "desconectar"])
        self.assertEqual(registro(acoes)["como_fechou"], "calada")

    def test_ferramenta_em_andamento_usa_o_teto_e_rearma_ao_terminar(self):
        self.falou()
        self.m.evento("ferramenta", 3.0, 1)
        self.assertEqual(self.m.evento("tique", 10.5), [])
        self.m.evento("ferramenta", 9.0, 0)
        self.assertEqual(self.m.evento("tique", 16.9), [])
        self.assertEqual(registro(self.m.evento("tique", 17.0))["como_fechou"], "calada")

    def test_ferramenta_que_nunca_termina_fecha_no_teto(self):
        self.falou()
        self.m.evento("ferramenta", 3.0, 1)
        self.assertEqual(self.m.evento("tique", 22.9), [])
        self.assertEqual(registro(self.m.evento("tique", 23.0))["como_fechou"], "sem_resposta")

    def test_ferramenta_antes_da_fala_parar_usa_o_teto(self):
        self.abrir_e_conectar()
        self.m.evento("fala_comecou", 1.2); self.m.evento("ferramenta", 2.0, 1); self.m.evento("fala_parou", 2.5)
        self.assertEqual(self.m.evento("tique", 22.4), [])
        self.assertEqual(registro(self.m.evento("tique", 22.5))["como_fechou"], "sem_resposta")

    def test_alguem_falando_quando_vence_nao_fecha(self):
        self.falou()
        self.m.evento("fala_comecou", 10.0)
        self.assertEqual(self.m.evento("tique", 10.5), [])
        self.assertEqual(self.m.estado, "ouvindo")

    def test_cerebro_pendente_vai_aguardar(self):
        self.abrir_e_conectar()
        self.m.evento("fala_comecou", 1.2); self.m.evento("cerebro_pendente", 2.0, 1); self.m.evento("fala_parou", 2.5)
        self.assertEqual(tipos(self.m.evento("tique", 10.5)), ["som:fim", "luz:cerebro", "microfone:False"])

    def test_registra_quanto_ela_levou_para_falar(self):
        self.falou()
        acoes = self.m.evento("ela_comecou", 3.7)
        self.assertIn(("registrar_espera", {"espera_resposta": 1.2, "como_abriu": "palavra"}), acoes)

    def test_entrega_mantem_os_20s(self):
        self.m.mudo = False
        self.assertEqual(self.m.campainha("e1abcdef", 0.0)[0], "vou_abrir")
        self.m.evento("conectou", 0.5)
        self.m.evento("ela_comecou", 1.0); self.m.evento("ela_parou", 3.0)
        self.m.evento("fala_comecou", 4.0); self.m.evento("fala_parou", 5.0)
        self.assertEqual(self.m.evento("tique", 24.9), [])
        self.assertEqual(registro(self.m.evento("tique", 25.0))["como_fechou"], "sem_resposta")

    def test_ferramenta_de_conexao_velha_e_ignorada(self):
        self.assertEqual(self.m.evento("ferramenta", 0.0, 3), [])
        self.assertEqual(self.m.ferramentas, 0)

    def test_encerrar_com_motivo_registra_o_motivo(self):
        self.abrir_e_conectar()
        acoes = self.m.evento("encerrar", 3.0, "agradecimento")
        self.assertEqual(tipos(acoes), ["som:fim", "luz:parada", "desconectar"])
        self.assertEqual(registro(acoes)["como_fechou"], "agradecimento")


class TestBotaoVermelho(Base):
    """Spec 2026-11-04: o vermelho corta tudo e fecha a conversa em qualquer estado aberto; o verde fica como hoje."""

    def aguardando(self):
        self.abrir_e_conectar()
        self.m.evento("fala_comecou", 1.2); self.m.evento("fala_parou", 2.0)
        self.m.evento("cerebro_pendente", 2.5, 1)
        self.m.evento("ela_comecou", 3.0); self.m.evento("ela_parou", 4.0)
        self.m.evento("tique", 12.0)
        assert self.m.estado == "aguardando"

    def test_verde_ouvindo_mantem_o_corte_de_hoje(self):
        self.abrir_e_conectar()
        acoes = self.m.evento("botao", 5.0, "verde")
        self.assertEqual(self.m.estado, "cortada")
        self.assertNotIn("cancelar_conversa", tipos(acoes))

    def test_vermelho_ouvindo_cancela_e_fecha(self):
        self.abrir_e_conectar()
        acoes = self.m.evento("botao", 5.0, "vermelho")
        t = tipos(acoes)
        self.assertIn("cancelar_conversa", t)
        self.assertLess(t.index("cancelar_conversa"), t.index("desconectar"))
        self.assertIn("som:fim", t)
        self.assertEqual(self.m.estado, "parada")
        self.assertEqual(registro(acoes)["como_fechou"], "botao_vermelho")

    def test_vermelho_com_ela_falando_cancela(self):
        self.abrir_e_conectar()
        self.m.evento("ela_comecou", 3.0)
        acoes = self.m.evento("botao", 4.0, "vermelho")
        self.assertIn("cancelar_conversa", tipos(acoes))
        self.assertEqual(self.m.estado, "parada")

    def test_vermelho_aguardando_cerebro_cancela(self):
        self.aguardando()
        acoes = self.m.evento("botao", 13.0, "vermelho")
        self.assertIn("cancelar_conversa", tipos(acoes))
        self.assertEqual(self.m.estado, "parada")
        self.assertEqual(self.m.pendentes, 0)

    def test_vermelho_depois_do_corte_cancela(self):
        self.abrir_e_conectar()
        self.m.evento("botao", 3.0, "verde")
        self.assertEqual(self.m.estado, "cortada")
        acoes = self.m.evento("botao", 4.5, "vermelho")
        self.assertIn("cancelar_conversa", tipos(acoes))
        self.assertEqual(self.m.estado, "parada")

    def test_vermelho_parado_nao_abre(self):
        self.assertEqual(self.m.evento("botao", 0.0, "vermelho"), [])
        self.assertEqual(self.m.estado, "parada")

    def test_verde_parado_abre(self):
        self.assertEqual(tipos(self.m.evento("botao", 0.0, "verde")), ["som:inicio", "luz:ouvindo", "conectar"])

class TestTestePalavra(Base):
    """spec 2026-11-05: teste da palavra de ativação na instalação."""
    def setUp(self):
        super().setUp()
        self.m.mudo = False

    def ob(self, acoes):
        return [a[1] for a in acoes if a[0] == "onboarding"]

    def test_so_a_partir_de_parada(self):
        self.m.evento("palavra", 0.0)
        self.assertEqual(self.m.evento("teste_palavra", 1.0, 30), [])
        self.assertEqual(self.m.estado, "conectando")

    def test_entra_toca_say_wake_word(self):
        a = self.m.evento("teste_palavra", 0.0, 30)
        self.assertEqual(self.m.estado, "teste_palavra")
        self.assertIn(("frase", "say_wake_word"), a)

    def test_duas_palavras_dao_ok(self):
        self.m.evento("teste_palavra", 0.0, 30)
        a1 = self.m.evento("palavra", 2.0, {"wake_word": "friday"})
        self.assertEqual(a1, [("frase", "heard_it")])
        self.assertEqual(self.m.estado, "teste_palavra")  # nenhuma conversa abriu
        a2 = self.m.evento("palavra", 5.0)
        self.assertEqual(self.ob(a2), [{"resultado": "wake_word_ok"}])
        self.assertEqual(self.m.estado, "parada")
        self.assertNotIn("conectar", [x[0] for x in a1 + a2])

    def test_timeout_falha(self):
        self.m.evento("teste_palavra", 0.0, 30)
        self.m.evento("palavra", 2.0)
        self.assertEqual(self.m.evento("tique", 29.9), [])
        a = self.m.evento("tique", 30.0)
        self.assertEqual(self.ob(a), [{"resultado": "wake_word_failed", "motivo": "tempo_esgotado"}])
        self.assertEqual(self.m.estado, "parada")

    def test_15s_sem_palavra_avisa_mic_mudo_uma_vez(self):
        self.m.evento("teste_palavra", 0.0, 30)
        self.assertEqual(self.m.evento("tique", 14.9), [])
        self.assertEqual(self.m.evento("tique", 15.0), [("frase", "mic_muted")])
        self.assertEqual(self.m.evento("tique", 20.0), [])

    def test_aviso_nao_sai_depois_de_ouvir_uma(self):
        self.m.evento("teste_palavra", 0.0, 30)
        self.m.evento("palavra", 3.0)
        self.assertEqual(self.m.evento("tique", 16.0), [])

    def test_vermelho_cancela(self):
        self.m.evento("teste_palavra", 0.0, 30)
        a = self.m.evento("botao", 1.0, "vermelho")
        self.assertEqual(self.ob(a), [{"resultado": "wake_word_failed", "motivo": "botao_vermelho"}])
        self.assertEqual(self.m.estado, "parada")

    def test_verde_nao_cancela(self):
        self.m.evento("teste_palavra", 0.0, 30)
        self.assertEqual(self.m.evento("botao", 1.0, "verde"), [])
        self.assertEqual(self.m.estado, "teste_palavra")

    def test_mudo_cancela(self):
        self.m.evento("teste_palavra", 0.0, 30)
        a = self.m.evento("mudo", 1.0, True)
        self.assertEqual(self.ob(a), [{"resultado": "wake_word_failed", "motivo": "mudo"}])
        self.assertEqual(self.m.estado, "parada")

    def test_mudo_na_partida_falha_na_hora(self):
        self.m.mudo = True
        a = self.m.evento("teste_palavra", 0.0, 30)
        self.assertEqual(self.m.estado, "parada")
        self.assertIn(("frase", "mic_muted"), a)
        self.assertEqual(self.ob(a), [{"resultado": "wake_word_failed", "motivo": "mudo"}])

    def test_timer_cancela_o_teste(self):
        self.m.evento("teste_palavra", 0.0, 30)
        a = self.m.evento("timer_venceu", 1.0, {"id": "t1", "nome": "x"})
        self.assertEqual(self.ob(a), [{"resultado": "wake_word_failed", "motivo": "timer"}])
        self.assertEqual(self.m.estado, "alarme")


class TestApresentacao(Base):
    def setUp(self):
        super().setUp()
        self.m.mudo = False

    def test_abre_conversa_propria_sem_som_de_lembrete(self):
        a = self.m.evento("abrir_apresentacao", 0.0, {"areas": []})
        self.assertEqual(a, [("luz", "ouvindo"), ("conectar", {"presentation": {"areas": []}})])
        self.assertEqual(self.m.estado, "conectando")

    def test_fora_de_parada_ou_mudo_nao_abre(self):
        self.m.evento("palavra", 0.0)
        self.assertEqual(self.m.evento("abrir_apresentacao", 1.0, {}), [])
        m2 = Maestro(Ajustes(), mudo_inicial=True)
        self.assertEqual(m2.evento("abrir_apresentacao", 0.0, {}), [])



if __name__ == "__main__":
    unittest.main()


class TestAgradecimentoComCerebroPendente(unittest.TestCase):
    """Revisão final (I1): "valeu" depois de "vou ver com o cérebro" não pode jogar fora a resposta pendente."""
    def test_agradecimento_com_pendente_aguarda_o_cerebro(self):
        m = Maestro(Ajustes())
        m.evento("palavra", 0.0); m.evento("conectou", 0.5)
        m.evento("fala_comecou", 1.0); m.evento("fala_parou", 2.0)
        m.evento("ela_comecou", 3.0); m.evento("cerebro_pendente", 3.5, 1); m.evento("ela_parou", 5.0)
        acoes = m.evento("encerrar", 7.0, "agradecimento")
        self.assertNotIn(("desconectar", None), acoes)
        self.assertEqual(m.estado, "aguardando")

    def test_agradecimento_sem_pendente_fecha(self):
        m = Maestro(Ajustes())
        m.evento("palavra", 0.0); m.evento("conectou", 0.5)
        m.evento("fala_comecou", 1.0); m.evento("fala_parou", 2.0)
        m.evento("ela_comecou", 3.0); m.evento("ela_parou", 5.0)
        self.assertIn(("desconectar", None), m.evento("encerrar", 7.0, "agradecimento"))


class TestFecharMicrofoneEsperandoResposta(unittest.TestCase):
    """04/11 (pedido do Alex, para testar): com `close_mic_awaiting_reply`, o microfone fecha quando a pessoa para de
    falar e só reabre quando ela começa a responder (como a Alexa). Desligado = comportamento de sempre."""

    def abrir(self, ligado):
        self.m = Maestro(Ajustes(close_mic_awaiting_reply=ligado))
        self.m.evento("palavra", 0.0)
        self.m.evento("conectou", 1.0)
        self.m.evento("fala_comecou", 1.5)

    def test_desligado_nao_mexe_no_microfone(self):
        self.abrir(False)
        self.assertEqual(tipos(self.m.evento("fala_parou", 3.0)), [])

    def test_ligado_fecha_ao_parar_de_falar_e_mostra_que_pensa(self):
        self.abrir(True)
        self.assertEqual(tipos(self.m.evento("fala_parou", 3.0)), ["cortar", "luz:cerebro"])
        self.assertEqual(self.m.estado, "ouvindo")

    def test_reabre_quando_ela_comeca_a_falar(self):
        self.abrir(True)
        self.m.evento("fala_parou", 3.0)
        self.assertEqual(tipos(self.m.evento("ela_comecou", 4.0)), ["microfone:True", "luz:ouvindo"])
        self.assertEqual(tipos(self.m.evento("ela_parou", 6.0)), [])  # continuação normal depois da fala dela

    def test_fala_com_o_microfone_fechado_nao_estica_a_espera(self):
        self.abrir(True)
        self.m.evento("fala_parou", 3.0)  # espera curta até 3 + 8 = 11
        self.m.evento("fala_comecou", 5.0)  # conversa da sala com o microfone fechado
        self.assertEqual(tipos(self.m.evento("fala_parou", 7.0)), [])
        acoes = self.m.evento("tique", 11.1)
        self.assertEqual(registro(acoes)["como_fechou"], "calada")

    def test_conversa_nova_comeca_com_o_microfone_normal(self):
        self.abrir(True)
        self.m.evento("fala_parou", 3.0)
        self.m.evento("tique", 11.1)  # fecha calada
        self.m.evento("palavra", 20.0)
        self.m.evento("conectou", 21.0)
        self.m.evento("fala_comecou", 21.5)
        self.assertEqual(tipos(self.m.evento("fala_parou", 23.0)), ["cortar", "luz:cerebro"])


class TestContinuacaoEstiloAlexa(unittest.TestCase):
    """04/11: sem continuação com música tocando nem depois de comando da casa; janela de continuação ajustável."""

    def abrir(self, **kw):
        self.m = Maestro(Ajustes(continuacao_s=5.0, **kw))
        self.m.evento("palavra", 0.0)
        self.m.evento("conectou", 1.0)
        self.m.evento("fala_comecou", 1.5)
        self.m.evento("fala_parou", 3.0)
        self.m.evento("ela_comecou", 4.0)

    def test_com_musica_fecha_quando_ela_termina(self):
        self.abrir()
        self.m.evento("musica", 4.2, True)
        acoes = self.m.evento("ela_parou", 6.0)
        self.assertEqual(registro(acoes)["como_fechou"], "musica")
        self.assertIn("som:fim", tipos(acoes))
        self.assertEqual(self.m.estado, "parada")

    def test_sem_musica_abre_a_janela(self):
        self.abrir()
        self.m.evento("musica", 4.2, False)
        self.assertEqual(self.m.evento("ela_parou", 6.0), [])
        self.assertEqual(self.m.evento("tique", 10.9), [])
        self.assertEqual(registro(self.m.evento("tique", 11.1))["como_fechou"], "silencio")

    def test_musica_com_switch_desligado_mantem_a_janela(self):
        self.abrir(no_follow_up_while_music=False)
        self.m.evento("musica", 4.2, True)
        self.assertEqual(self.m.evento("ela_parou", 6.0), [])

    def test_musica_nao_vaza_para_a_conversa_seguinte(self):
        self.abrir()
        self.m.evento("musica", 4.2, True)
        self.m.evento("ela_parou", 6.0)
        self.m.evento("palavra", 20.0)
        self.m.evento("conectou", 21.0)
        self.m.evento("fala_comecou", 21.5)
        self.m.evento("fala_parou", 23.0)
        self.m.evento("ela_comecou", 24.0)
        self.assertEqual(self.m.evento("ela_parou", 26.0), [])
        self.assertEqual(self.m.estado, "ouvindo")

    def test_comando_casa_fecha(self):
        self.abrir()
        self.m.evento("ela_parou", 6.0)
        acoes = self.m.evento("encerrar", 7.0, "comando_casa")
        self.assertEqual(registro(acoes)["como_fechou"], "comando_casa")

    def test_comando_casa_ignorado_com_alguem_falando(self):
        self.abrir()
        self.m.evento("ela_parou", 6.0)
        self.m.evento("fala_comecou", 6.5)
        self.assertEqual(self.m.evento("encerrar", 7.0, "comando_casa"), [])
        self.assertEqual(self.m.estado, "ouvindo")

    def test_comando_casa_ignorado_parado_e_com_switch_desligado(self):
        m = Maestro(Ajustes())
        self.assertEqual(m.evento("encerrar", 1.0, "comando_casa"), [])
        self.abrir(no_follow_up_after_home_commands=False)
        self.m.evento("ela_parou", 6.0)
        self.assertEqual(self.m.evento("encerrar", 7.0, "comando_casa"), [])

    def test_follow_up_window_zero_fecha_no_proximo_tique(self):
        self.m = Maestro(Ajustes(continuacao_s=0.0))
        self.m.evento("palavra", 0.0)
        self.m.evento("conectou", 1.0)
        self.m.evento("fala_comecou", 1.5)
        self.m.evento("fala_parou", 3.0)
        self.m.evento("ela_comecou", 4.0)
        self.assertEqual(self.m.evento("ela_parou", 6.0), [])
        self.assertEqual(registro(self.m.evento("tique", 6.05))["como_fechou"], "silencio")
