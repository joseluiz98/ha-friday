"""Produção (spec 2026-10-18 §4.2, §4.11): sem POC, sem Supervisor, rotas em inglês.
cd /srv/friday/app && python3 -m unittest -q test_production"""
import glob, os, unittest

AQUI = os.path.dirname(os.path.abspath(__file__))


def fontes():
    return [p for p in glob.glob(os.path.join(AQUI, "*.py")) if not os.path.basename(p).startswith("test_")]


class TestProducao(unittest.TestCase):
    def test_sem_variaveis_poc(self):
        for p in fontes():
            self.assertNotIn("POC_", open(p).read(), p)

    def test_sem_supervisor(self):
        for p in fontes():
            t = open(p).read()
            self.assertNotIn("http://supervisor", t, p)
            self.assertNotIn("SUPERVISOR_TOKEN", t, p)

    def test_sem_caminho_poc(self):
        for p in fontes():
            self.assertNotIn("/share/openclaw-poc", open(p).read(), p)

    def test_rotas_em_ingles(self):
        t = open(os.path.join(AQUI, "bot.py")).read()
        for r in ('@app.post("/deliver")', '@app.post("/reminder")', '@app.post("/satellite_alert")', ":8700/doorbell"):
            self.assertIn(r, t)
        for r in ('"/entregar"', '"/aviso_satelite"', ":8700/campainha"):
            self.assertNotIn(r, t)

    def test_ha_pela_rede(self):
        t = open(os.path.join(AQUI, "bot.py")).read()
        self.assertIn('local_config.get("FRIDAY_HA_URL")', t)  # 09/11: o endereço do HA vem da configuração local
        self.assertIn('f"{HA_URL}/api/mcp"', t)
        self.assertIn('f"{HA_URL}/api/states/{entidade}"', t)
        self.assertEqual(t.count("os.environ['FRIDAY_HA_TOKEN']") + t.count('os.environ["FRIDAY_HA_TOKEN"]'), 7)  # 21/10: +1 do estado do player (fato de música), +1 do play_music (R11); 23/10: +1 do inventário em /voice_skills

    def test_conversa_pela_fila(self):
        t = open(os.path.join(AQUI, "bot.py")).read()
        self.assertNotIn("rc.gravar(", t)          # nenhuma gravação direta no laço de eventos
        self.assertIn("rc.GravadorEmFila(CONVERSAS_DIR, RESERVA_DIR", t)

    def test_cadastro_com_cache(self):
        t = open(os.path.join(AQUI, "bot.py")).read()
        self.assertNotIn("cc.ler_satelites(SATELITES_ARQ)", t)
        # desde 19/10 o cache entra pela leitura sem travar (read_satellites_without_blocking usa ler_satelites_com_cache)
        self.assertEqual(t.count("cc.read_satellites_without_blocking(SATELITES_ARQ, SATELITES_CACHE)"), 4)  # 21/10: +1 (player do satélite, fato de música; com prazo), +1 (/satellite_media)


if __name__ == "__main__":
    unittest.main()


class TestSatellitesNonBlocking(unittest.TestCase):
    def test_bot_never_reads_the_share_inside_the_event_loop(self):
        """Revisão final de 19/10: o cadastro dos satélites mora no Samba; lido direto num handler async, o Samba fora
        congelava a voz ~25 s. O bot só lê pelo caminho com thread e prazo."""
        t = open(os.path.join(AQUI, "bot.py")).read()
        self.assertNotIn("cc.ler_satelites_com_cache(", t)
        self.assertNotIn("DepsCanal()", t)
        self.assertIn("cc.read_satellites_without_blocking(SATELITES_ARQ, SATELITES_CACHE)", t)
