"""Testes do despacho de comandos: cd /homeassistant/tools/satelite/programa && python3 -m unittest test_comandos -v"""
import unittest
import comandos
from timers import Timers
from volume import ControleVolume


class Base(unittest.TestCase):
    def setUp(self):
        self.escritas = []
        self.vol = ControleVolume(self.escritas.append, lambda k: None, 4, 6)
        self.tim = Timers(lambda lista: None)

    def run_(self, **cmd):
        return comandos.executar(dict(cmd, id="c1"), self.vol, self.tim, 1000.0)


class TestVolume(Base):
    def test_numero(self):
        r, ef = self.run_(acao="volume", numero=10)
        self.assertEqual(r, {"id": "c1", "ok": True, "posicao": 11, "volume": 10, "percentual": 100})
        self.assertEqual(ef, ["bip_volume"])

    def test_percentual(self):
        r, _ = self.run_(acao="volume", percentual=45)
        self.assertEqual(r["posicao"], 5)

    def test_mais_e_menos(self):
        self.assertEqual(self.run_(acao="volume", ajuste="mais")[0]["posicao"], 5)
        self.assertEqual(self.run_(acao="volume", ajuste="menos")[0]["posicao"], 4)

    def test_exatamente_um_parametro(self):
        for cmd in ({}, {"numero": 3, "percentual": 30}, {"ajuste": "lado"}, {"numero": 0}):
            r, ef = self.run_(acao="volume", **cmd)
            self.assertFalse(r["ok"], cmd)
            self.assertEqual(ef, [])
        self.assertEqual(self.escritas, [])


class TestTimer(Base):
    def test_criar_consultar_cancelar(self):
        r, ef = self.run_(acao="timer_criar", duracao_s=600, nome="arroz")
        self.assertTrue(r["ok"])
        self.assertEqual((r["timer"]["nome"], r["timer"]["falta_s"], r["ativos"]), ("arroz", 600, 1))
        self.assertEqual(ef, [])
        r, _ = self.run_(acao="timer_consultar")
        self.assertEqual([t["nome"] for t in r["timers"]], ["arroz"])
        r, _ = self.run_(acao="timer_cancelar", nome="arroz")
        self.assertEqual([t["nome"] for t in r["cancelados"]], ["arroz"])

    def test_erro_vira_resposta_sem_excecao(self):
        r, _ = self.run_(acao="timer_criar", duracao_s=90000)
        self.assertEqual(r["ok"], False)
        self.assertIn("lembrete", r["erro"])

    def test_cancelar_ambiguo(self):
        self.run_(acao="timer_criar", duracao_s=60)
        self.run_(acao="timer_criar", duracao_s=120)
        self.assertFalse(self.run_(acao="timer_cancelar")[0]["ok"])

    def test_acao_desconhecida(self):
        r, _ = self.run_(acao="formatar")
        self.assertEqual((r["ok"], r["id"]), (False, "c1"))

    def test_resposta_nao_leva_termino_em_epoch(self):
        r, _ = self.run_(acao="timer_criar", duracao_s=60)
        self.assertNotIn("termino", r["timer"])


class TestTimerComId(Base):
    """14/10: o timer devolve o próprio id e as ferramentas passam a trabalhar com ele."""

    def test_criar_e_consultar_devolvem_o_id(self):
        r, _ = self.run_(acao="timer_criar", duracao_s=600)
        self.assertRegex(r["timer"]["id"], r"^t[0-9a-f]{6}$")
        r2, _ = self.run_(acao="timer_consultar")
        self.assertEqual([t["id"] for t in r2["timers"]], [r["timer"]["id"]])
        self.assertNotIn("termino", r2["timers"][0])

    def test_cancela_so_o_id_pedido_entre_dois_sem_nome(self):
        a = self.run_(acao="timer_criar", duracao_s=600)[0]["timer"]
        b = self.run_(acao="timer_criar", duracao_s=300)[0]["timer"]
        r, _ = self.run_(acao="timer_cancelar", timer_id=a["id"])
        self.assertEqual([t["id"] for t in r["cancelados"]], [a["id"]])
        self.assertEqual([t["id"] for t in self.run_(acao="timer_consultar")[0]["timers"]], [b["id"]])

    def test_id_desconhecido_e_erro_claro(self):
        self.run_(acao="timer_criar", duracao_s=60)
        r, _ = self.run_(acao="timer_cancelar", timer_id="t000000")
        self.assertFalse(r["ok"])
        self.assertIn("id", r["erro"])
        self.assertEqual(len(self.run_(acao="timer_consultar")[0]["timers"]), 1)

    def test_cancelar_ambiguo_devolve_os_candidatos_para_ela_decidir(self):
        # 14/10 (API): com 2 timers ela chamava cancelar às cegas, recebia só "diga qual" e perguntava sem opções
        a = self.run_(acao="timer_criar", duracao_s=600)[0]["timer"]
        b = self.run_(acao="timer_criar", duracao_s=300, nome="ovo")[0]["timer"]
        r, _ = self.run_(acao="timer_cancelar")
        self.assertFalse(r["ok"])
        self.assertEqual([(t["id"], t["nome"], t["duracao_s"]) for t in r["timers"]],
                         [(b["id"], "ovo", 300), (a["id"], None, 600)])  # por quem vence primeiro
        self.assertEqual(len(self.run_(acao="timer_consultar")[0]["timers"]), 2)  # nada foi cancelado

    def test_outros_erros_nao_levam_lista(self):
        r, _ = self.run_(acao="timer_criar", duracao_s=90000)
        self.assertNotIn("timers", r)

    def test_nome_que_nao_existe_e_erro_com_os_ativos(self):
        # revisão final 15/10: "cancela o do arroz" com o timer chamado "o arroz" voltava ok com cancelados=[];
        # ela dizia que cancelou e o alarme tocava
        a = self.run_(acao="timer_criar", duracao_s=600, nome="o arroz")[0]["timer"]
        r, _ = self.run_(acao="timer_cancelar", nome="arroz")
        self.assertFalse(r["ok"])
        self.assertIn("nenhum timer ativo com esse nome", r["erro"])
        self.assertEqual([t["id"] for t in r["timers"]], [a["id"]])
        self.assertEqual(len(self.run_(acao="timer_consultar")[0]["timers"]), 1)

    def test_nome_que_nao_existe_sem_timers_ativos(self):
        r, _ = self.run_(acao="timer_cancelar", nome="arroz")
        self.assertFalse(r["ok"])
        self.assertEqual(r["timers"], [])

    def test_nome_repetido_devolve_so_os_candidatos_com_aquele_nome(self):
        a = self.run_(acao="timer_criar", duracao_s=600, nome="arroz")[0]["timer"]
        b = self.run_(acao="timer_criar", duracao_s=300, nome="arroz")[0]["timer"]
        self.run_(acao="timer_criar", duracao_s=60, nome="ovo")
        r, _ = self.run_(acao="timer_cancelar", nome="arroz")
        self.assertFalse(r["ok"])
        self.assertEqual([t["id"] for t in r["timers"]], [b["id"], a["id"]])  # só os dois "arroz", por quem vence primeiro
        self.assertEqual(len(self.run_(acao="timer_consultar")[0]["timers"]), 3)


class _Vozes:
    def __init__(self, ocupado=False):
        self.ocupado = ocupado

    def listar(self):
        return [{"nome": "Bia", "id": "bia", "desde": "2026-10-15 18:00"}]


class TestAprendizagem(unittest.TestCase):
    """Spec 2026-10-15 §4.4: comandos do programa de aprendizagem de voz."""

    def test_consultar_devolve_nome_e_data(self):
        r, ef = comandos.executar({"id": "c1", "acao": "vozes_consultar"}, None, None, 0.0, _Vozes())
        self.assertEqual((r["ok"], r["id"], r["vozes"], ef), (True, "c1", [{"nome": "Bia", "desde": "2026-10-15 18:00"}], []))

    def test_cadastro_valido_devolve_efeito_sem_colidir_com_o_id_do_comando(self):
        r, ef = comandos.executar({"id": "c2", "acao": "cadastro_voz", "nome": "Bia"}, None, None, 0.0, _Vozes())
        self.assertEqual((r["ok"], r["id"], r["nome"], r["id_pessoa"]), (True, "c2", "Bia", "bia"))
        self.assertEqual(ef, ["cadastro"])

    def test_nome_invalido(self):
        r, ef = comandos.executar({"id": "c3", "acao": "cadastro_voz", "nome": "K1"}, None, None, 0.0, _Vozes())
        self.assertEqual((r["ok"], ef), (False, []))

    def test_recusa_com_cadastro_em_andamento(self):
        r, ef = comandos.executar({"id": "c4", "acao": "cadastro_voz", "nome": "Bia"}, None, None, 0.0,
                                  _Vozes(ocupado=True))
        self.assertFalse(r["ok"]); self.assertIn("andamento", r["erro"]); self.assertEqual(ef, [])

    def test_sem_vozes_e_indisponivel(self):
        r, _ = comandos.executar({"id": "c5", "acao": "cadastro_voz", "nome": "Bia"}, None, None, 0.0)
        self.assertFalse(r["ok"])


class TestTimerMusica(Base):
    def test_timer_criar_repassa_music(self):
        r, _ = self.run_(acao="timer_criar", duracao_s=60, nome="acordar", music="Legião Urbana")
        self.assertTrue(r["ok"])
        self.assertEqual(self.tim.consultar(1000.0)[0]["music"], "Legião Urbana")

    def test_timer_criar_sem_music_segue_igual(self):
        r, _ = self.run_(acao="timer_criar", duracao_s=60)
        self.assertTrue(r["ok"])
        self.assertNotIn("music", self.tim.consultar(1000.0)[0])

    def test_music_invalida_recusa(self):
        r, _ = self.run_(acao="timer_criar", duracao_s=60, music="x" * 81)
        self.assertFalse(r["ok"])

class TestProvisionamento(unittest.TestCase):
    def setUp(self):
        import os, tempfile
        self.d = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.d, "onboarding"))
        for k in ("setup_mode", "digit_1"):
            open(os.path.join(self.d, "onboarding", k + ".wav"), "wb").write(b"x")
        self.vol = ControleVolume(lambda k: None, lambda k: None, 4, 6)
        self.tim = Timers(lambda lista: None)

    def run_(self, estado="parada", **cmd):
        return comandos.executar(dict(cmd, id="c1"), self.vol, self.tim, 1000.0, sons_dir=self.d, estado_maestro=estado)

    def test_play_phrases_toca_so_as_que_existem(self):
        r, ef = self.run_(acao="play_phrases", keys=["setup_mode", "nao_existe", "digit_1"])
        self.assertEqual((r["ok"], r["played"]), (True, 2))
        self.assertEqual(ef[0][0], "tocar_frases")
        self.assertEqual([c.rsplit("/", 1)[1] for c in ef[0][1]], ["setup_mode.wav", "digit_1.wav"])

    def test_ring_countdown(self):
        r, ef = self.run_(acao="ring_countdown", seconds=10, delay_s=9)
        self.assertIn(("contagem", (9, 10)), ef)
        r, ef = self.run_(acao="ring_countdown")
        self.assertIn(("contagem", (0, 0)), ef)

    def test_ring_countdown_invalido(self):
        for bad in (-1, 61, "10", True):
            r, ef = self.run_(acao="ring_countdown", seconds=bad)
            self.assertFalse(r["ok"], bad)

    def test_play_phrases_chaves_invalidas(self):
        for keys in (None, [], ["../x"], [1], "setup_mode"):
            r, ef = self.run_(acao="play_phrases", keys=keys)
            self.assertFalse(r["ok"], keys)
            self.assertEqual(ef, [])

    def test_ring(self):
        r, ef = self.run_(acao="ring", state="setup")
        self.assertTrue(r["ok"])
        self.assertEqual(ef, [("anel", "setup")])
        r, ef = self.run_(acao="ring", state="rosa")
        self.assertFalse(r["ok"])
        self.assertEqual(ef, [])

    def test_wake_word_test(self):
        r, ef = self.run_(acao="wake_word_test", timeout_s=30)
        self.assertTrue(r["ok"])
        self.assertEqual(ef, [("teste_palavra", 30)])

    def test_wake_word_test_fora_de_parada_recusa(self):
        r, ef = self.run_(estado="ouvindo", acao="wake_word_test", timeout_s=30)
        self.assertFalse(r["ok"])
        self.assertEqual(ef, [])

    def test_wake_word_test_timeout_invalido(self):
        for t in (0, 1000, "30", True):
            self.assertFalse(self.run_(acao="wake_word_test", timeout_s=t)[0]["ok"], t)

    def test_open_presentation(self):
        fatos = {"areas": [{"id": "sala", "name": "Sala"}], "wake_word_ok": True}
        r, ef = self.run_(acao="open_presentation", facts=fatos)
        self.assertTrue(r["ok"])
        self.assertEqual(ef, [("apresentacao", fatos)])
        self.assertFalse(self.run_(estado="ouvindo", acao="open_presentation", facts=fatos)[0]["ok"])
        self.assertFalse(self.run_(acao="open_presentation", facts="x")[0]["ok"])

    def test_set_area(self):
        r, ef = self.run_(acao="set_area", area_id="sala_de_estar")
        self.assertTrue(r["ok"])
        self.assertEqual(ef, [("onboarding", {"resultado": "area_selected", "area_id": "sala_de_estar"})])
        for ruim in (None, "", "Sala de estar", "a/b", 3):
            self.assertFalse(self.run_(acao="set_area", area_id=ruim)[0]["ok"], ruim)

