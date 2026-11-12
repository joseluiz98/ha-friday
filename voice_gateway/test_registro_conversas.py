"""cd /srv/friday/app && python3 -m unittest test_registro_conversas -v"""
import json, os, stat, tempfile, threading, time, unittest
from datetime import datetime, timezone
import registro_conversas as rc

CAMPOS = {"ts", "prompt", "response", "ok", "error", "durationMs", "sessionKey", "sessionId", "speakTarget",
          "warmState", "mode", "tools", "turnNumber", "resumed"}


def t(seg):
    return datetime(2026, 10, 6, 13, 0, seg, tzinfo=timezone.utc).timestamp()


class TestTurnos(unittest.TestCase):
    def test_um_turno_vira_uma_linha_com_os_campos_do_cv(self):
        tr = rc.Turnos("friday-satelite-sat-7f3a2c", "voz-x", "sat-7f3a2c")
        tr.usuario("apaga a luz ", t(1)); tr.usuario("da sala", t(2))
        tr.ferramenta("HassTurnOff", "light.sala")
        tr.ela("Pronto, "); tr.ela("apaguei.")
        r = tr.ela_parou(t(5))
        self.assertEqual(set(r), CAMPOS)
        self.assertEqual(r["prompt"], "apaga a luz da sala")
        self.assertEqual(r["response"], "Pronto, apaguei.")
        self.assertEqual(r["tools"], [{"name": "HassTurnOff", "summary": "light.sala"}])
        self.assertEqual(r["durationMs"], 3000)
        self.assertEqual((r["mode"], r["turnNumber"], r["ok"], r["warmState"], r["resumed"]),
                         ("friday", 1, True, None, False))
        self.assertEqual(r["ts"], "2026-10-06T13:00:05.000Z")

    def test_portao_de_fala_vai_para_a_linha_do_turno(self):
        # 12/11: o tempo que o portão segura cada turno fica no registro permanente, não só no log do container
        tr = rc.Turnos("k", "voz-x", "sat")
        tr.usuario("que horas são", t(1)); tr.portao("released", 0.1234); tr.ela("Nove horas.")
        r = tr.ela_parou(t(3))
        self.assertEqual(r["speechGate"], [{"action": "released", "heldMs": 123}])
        tr.usuario("conversa com outra pessoa", t(4)); tr.portao("dropped", 0.05); tr.ela("<no speech>{pause}")
        r = tr.ela_parou(t(5))
        self.assertEqual(r["speechGate"], [{"action": "dropped", "heldMs": 50}])
        tr.usuario("oi", t(6)); tr.ela("Oi.")
        self.assertNotIn("speechGate", tr.ela_parou(t(7)))      # turno sem decisão do portão: sem o campo

    def test_turnos_numerados_e_parada_sem_conteudo_nao_grava(self):
        tr = rc.Turnos("friday-pagina", "voz-y", "pagina")
        self.assertIsNone(tr.ela_parou(t(1)))
        tr.ela("Oi!"); self.assertEqual(tr.ela_parou(t(2))["turnNumber"], 1)
        tr.usuario("tudo bem?", t(3)); tr.ela("Tudo."); self.assertEqual(tr.ela_parou(t(4))["turnNumber"], 2)

    def test_entrega_do_cerebro_vira_prompt(self):
        tr = rc.Turnos("friday-pagina", "voz-y", "pagina")
        tr.entrega_do_cerebro("quanto de swap?", t(1)); tr.ela("Está em 1,2 GB.")
        self.assertEqual(tr.ela_parou(t(3))["prompt"], "[resposta do cérebro a: quanto de swap?]")


class TestSussurro(unittest.TestCase):
    """22/10: conversa do modo sussurro (spec 2026-10-20) leva `whisper: true`; as demais NÃO têm o campo
    (retrocompatível: quem lê trata a ausência como falso)."""
    def test_conversa_sussurrada_marca_whisper(self):
        tr = rc.Turnos("friday-satelite-sat-1", "voz-w", "sat-1", whisper=True)
        tr.usuario("que horas são?", t(1)); tr.ela("São cinco e dez.")
        r = tr.ela_parou(t(3))
        self.assertEqual(set(r), CAMPOS | {"whisper"})
        self.assertIs(r["whisper"], True)

    def test_conversa_normal_sem_o_campo(self):
        tr = rc.Turnos("friday-satelite-sat-1", "voz-n", "sat-1")
        tr.ela("Oi!")
        self.assertNotIn("whisper", tr.ela_parou(t(2)))

    def test_linha_avulsa_sussurrada(self):
        r = rc.linha_avulsa("friday-satelite-sat-1", "voz-w", "sat-1", "p", "r", erro="x", agora=t(9), whisper=True)
        self.assertIs(r["whisper"], True)
        self.assertNotIn("whisper", rc.linha_avulsa("k", "s", "a", "p", "r", erro="x", agora=t(9)))

class TestWakeWord(unittest.TestCase):
    """28/10: a palavra que abriu a conversa (`wakeWord`) vai SÓ no 1º turno — o Jarvis Logbook mostra no card que
    abriu o diálogo e não nos seguintes. Sem palavra (botão, página, entrega, timer) = sem o campo."""
    def test_so_no_primeiro_turno(self):
        tr = rc.Turnos("friday-satelite-sat-1", "voz-p", "sat-1", wake_word="sexta_feira")
        tr.usuario("que horas são?", t(1)); tr.ela("Cinco e dez.")
        r1 = tr.ela_parou(t(2))
        self.assertEqual(set(r1), CAMPOS | {"wakeWord"})
        self.assertEqual(r1["wakeWord"], "sexta_feira")
        tr.usuario("obrigado", t(3)); tr.ela("De nada.")
        self.assertNotIn("wakeWord", tr.ela_parou(t(4)))

    def test_sem_palavra_sem_o_campo(self):
        tr = rc.Turnos("friday-satelite-sat-1", "voz-b", "sat-1")
        tr.ela("Oi!")
        self.assertNotIn("wakeWord", tr.ela_parou(t(2)))

    def test_parada_vazia_nao_gasta_o_primeiro_turno(self):
        tr = rc.Turnos("friday-satelite-sat-1", "voz-v", "sat-1", wake_word="friday")
        self.assertIsNone(tr.ela_parou(t(1)))
        tr.ela("Oi!")
        self.assertEqual(tr.ela_parou(t(2))["wakeWord"], "friday")


class TestFerramentas(unittest.TestCase):
    def test_mesma_chamada_nao_duplica(self):
        # 06/10: o Pipecat empurra o FunctionCallInProgressFrame nas duas direções; cada ferramenta saía 2x.
        tr = rc.Turnos("k", "s", "a")
        tr.ferramenta("llm__GetDateTime", "{}", "fc_1"); tr.ferramenta("llm__GetDateTime", "{}", "fc_1")
        tr.ferramenta("llm__GetDateTime", "{}", "fc_2"); tr.ela("ok")
        self.assertEqual(len(tr.ela_parou(t(2))["tools"]), 2)


class TestGravar(unittest.TestCase):
    def test_arquivo_do_dia_utc_append_e_600(self):
        with tempfile.TemporaryDirectory() as d:
            r = {"ts": "2026-10-06T02:00:00.000Z", "prompt": "a"}
            rc.gravar(r, d); rc.gravar(dict(r, prompt="b"), d)
            p = os.path.join(d, "2026-10-06.ndjson")
            self.assertEqual([json.loads(l)["prompt"] for l in open(p)], ["a", "b"])
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)

    def test_linha_avulsa_de_entrega_calada(self):
        r = rc.linha_avulsa("friday-satelite-sat-1", "voz-z", "sat-1", "pedido x", "resposta y",
                            erro="conversa encerrada antes da entrega", agora=t(9))
        self.assertEqual(set(r), CAMPOS); self.assertFalse(r["ok"])


class TestReserva(unittest.TestCase):
    def setUp(self):
        base = tempfile.mkdtemp()
        self.pasta = os.path.join(base, "conversas")   # NÃO criada: compartilhamento fora
        self.reserva = os.path.join(base, "reserva")

    def r(self, texto):
        return {"ts": "2026-10-20T10:00:00.000Z", "prompt": texto}

    def linhas(self):
        return [json.loads(x)["prompt"] for x in open(os.path.join(self.pasta, "2026-10-20.ndjson")).read().splitlines()]

    def test_pasta_fora_vai_para_reserva(self):
        self.assertEqual(rc.gravar_com_reserva(self.r("a"), self.pasta, self.reserva), "reserva")
        self.assertFalse(os.path.exists(self.pasta))
        self.assertTrue(os.path.exists(os.path.join(self.reserva, "2026-10-20.ndjson")))

    def test_descarrega_uma_vez_sem_duplicar(self):
        rc.gravar_com_reserva(self.r("a"), self.pasta, self.reserva)
        os.makedirs(self.pasta)
        self.assertEqual(rc.descarregar_reserva(self.pasta, self.reserva), 1)
        self.assertEqual(rc.descarregar_reserva(self.pasta, self.reserva), 0)
        self.assertEqual(self.linhas(), ["a"])
        self.assertFalse(os.path.exists(os.path.join(self.reserva, "2026-10-20.ndjson")))

    def test_preserva_linhas_de_outro_escritor(self):
        os.makedirs(self.pasta)
        with open(os.path.join(self.pasta, "2026-10-20.ndjson"), "w") as f:
            f.write(json.dumps({"prompt": "do_cv"}) + "\n")      # o Claude Voice grava no mesmo arquivo
        self.assertEqual(rc.gravar_com_reserva(self.r("b"), self.pasta, self.reserva), "pasta")
        self.assertEqual(self.linhas(), ["do_cv", "b"])

    def test_reserva_pendente_sai_antes_do_novo(self):
        rc.gravar_com_reserva(self.r("1"), self.pasta, self.reserva)
        os.makedirs(self.pasta)
        rc.gravar_com_reserva(self.r("2"), self.pasta, self.reserva)
        self.assertEqual(self.linhas(), ["1", "2"])

    def test_reanexo_depois_de_falha_nao_duplica(self):
        """Minor 12: o anexo deu certo mas a reserva não foi apagada (erro no meio, montagem soft)."""
        rc.gravar_com_reserva(self.r("a"), self.pasta, self.reserva)
        os.makedirs(self.pasta)
        with open(os.path.join(self.reserva, "2026-10-20.ndjson")) as f:
            ja = f.read()
        with open(os.path.join(self.pasta, "2026-10-20.ndjson"), "w") as f:
            f.write(ja)
        self.assertEqual(rc.descarregar_reserva(self.pasta, self.reserva), 0)
        self.assertEqual(self.linhas(), ["a"])
        self.assertFalse(os.path.exists(os.path.join(self.reserva, "2026-10-20.ndjson")))

    def test_linha_cortada_no_destino_nao_cola_o_registro(self):
        os.makedirs(self.pasta)
        destino = os.path.join(self.pasta, "2026-10-20.ndjson")
        with open(destino, "w") as f:
            f.write('{"prompt": "cort')                       # escrita interrompida, sem quebra de linha
        rc.gravar_com_reserva(self.r("b"), self.pasta, self.reserva)
        linhas = open(destino).read().splitlines()
        self.assertEqual(linhas[0], '{"prompt": "cort')
        self.assertEqual(json.loads(linhas[1])["prompt"], "b")

    def test_arquivo_novo_na_pasta_tem_permissao_600(self):
        rc.gravar_com_reserva(self.r("a"), self.pasta, self.reserva)
        os.makedirs(self.pasta)
        rc.descarregar_reserva(self.pasta, self.reserva)
        modo = stat.S_IMODE(os.stat(os.path.join(self.pasta, "2026-10-20.ndjson")).st_mode)
        self.assertEqual(modo, 0o600)


class TestGravadorEmFila(unittest.TestCase):
    def test_enviar_nao_bloqueia_e_mantem_a_ordem(self):
        feitos = []
        def lento(registro, pasta, reserva):
            time.sleep(0.3); feitos.append(registro["prompt"]); return "pasta"
        g = rc.GravadorEmFila("/p", "/r", gravar=lento)
        t0 = time.monotonic()
        for x in ("1", "2", "3"):
            g.enviar({"prompt": x})
        self.assertLess(time.monotonic() - t0, 0.1)
        self.assertTrue(g.esperar(5))
        self.assertEqual(feitos, ["1", "2", "3"])

    def test_erro_nao_derruba_a_fila(self):
        feitos = []
        def falha_uma(registro, pasta, reserva):
            if registro["prompt"] == "1":
                raise OSError("Host is down")
            feitos.append(registro["prompt"]); return "reserva"
        destinos = []
        g = rc.GravadorEmFila("/p", "/r", gravar=falha_uma, ao_gravar=destinos.append)
        g.enviar({"prompt": "1"}); g.enviar({"prompt": "2"})
        self.assertTrue(g.esperar(5))
        self.assertEqual(feitos, ["2"])
        self.assertEqual(destinos, ["reserva"])

    def test_spool_drains_on_its_own_when_share_returns(self):
        """Sem conversa nova, a reserva não pode ficar na VM para sempre (spec §4.9: copiada quando o
        compartilhamento volta). O gravador tenta a cada `idle_s` sem registros na fila."""
        base = tempfile.mkdtemp()
        pasta, reserva = os.path.join(base, "conversas"), os.path.join(base, "reserva")
        g = rc.GravadorEmFila(pasta, reserva, idle_s=0.1)
        g.enviar({"ts": "2026-10-20T10:00:00.000Z", "prompt": "na reserva"})
        self.assertTrue(g.esperar(5))
        self.assertTrue(os.listdir(reserva))
        os.mkdir(pasta)
        fim = time.monotonic() + 5
        while os.listdir(reserva) and time.monotonic() < fim:
            time.sleep(0.05)
        self.assertEqual(os.listdir(reserva), [])
        linhas = open(os.path.join(pasta, "2026-10-20.ndjson")).read().splitlines()
        self.assertEqual([json.loads(x)["prompt"] for x in linhas], ["na reserva"])

    def test_idle_drain_only_runs_while_spool_is_pending(self):
        """Sem nada na reserva o gravador fica parado esperando registro: nenhuma tentativa periódica (Alex, 18/10)."""
        base = tempfile.mkdtemp()
        pasta, reserva = os.path.join(base, "conversas"), os.path.join(base, "reserva")
        os.mkdir(pasta)
        chamadas = []
        g = rc.GravadorEmFila(pasta, reserva, idle_s=0.05, drain=lambda p, r: chamadas.append(1) or 0)
        time.sleep(0.4)
        self.assertEqual(chamadas, [])
        g.enviar({"ts": "2026-10-20T10:00:00.000Z", "prompt": "ok"})
        self.assertTrue(g.esperar(5)); time.sleep(0.3)
        self.assertEqual(chamadas, [])

    def test_spool_found_at_start_is_drained(self):
        base = tempfile.mkdtemp()
        pasta, reserva = os.path.join(base, "conversas"), os.path.join(base, "reserva")
        rc.gravar({"ts": "2026-10-20T10:00:00.000Z", "prompt": "de antes"}, reserva)
        os.mkdir(pasta)
        rc.GravadorEmFila(pasta, reserva, idle_s=0.05)
        fim = time.monotonic() + 5
        while os.listdir(reserva) and time.monotonic() < fim:
            time.sleep(0.05)
        self.assertEqual(os.listdir(reserva), [])


if __name__ == "__main__":
    unittest.main()
