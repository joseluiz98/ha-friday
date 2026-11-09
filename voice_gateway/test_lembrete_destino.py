import unittest
"""Coerência do destino dos lembretes (14/10/2026): o fato fica num lugar só, o AGENTS.md da friday, e precisa bater
com a rota do bot, a porta do add-on e o dono do Telegram. Se alguém mudar um lado, este teste quebra antes de
publicar. python3 -m unittest test_lembrete_destino"""
import json, os, re, unittest
from urllib.parse import urlparse

AQUI = os.path.dirname(os.path.abspath(__file__))
WS = os.environ.get("FRIDAY_TEST_WS", "/srv/friday/home/.openclaw/workspace/friday")
AGENTS = f"{WS}/AGENTS.md"
SKILL = f"{WS}/skills/falar-em-casa/SKILL.md"
OC_CONF = os.environ.get("FRIDAY_TEST_OC_CONF", "/srv/friday/home/.openclaw/openclaw.json")
RUN_SH = os.environ.get("FRIDAY_TEST_RUN_SH", "/opt/friday/image/run.sh")


def delivery_do_agents():
    m = re.search(r'`"delivery": (\{.*\})`', open(AGENTS).read())
    return json.loads(m.group(1)) if m else None


@unittest.skipUnless(os.path.exists(WS), "needs the production brain workspace (runs on the VM: scripts/test-all.sh)")
class TestDestinoDoLembrete(unittest.TestCase):
    def test_agents_descreve_o_delivery(self):
        d = delivery_do_agents()
        self.assertIsNotNone(d, "o AGENTS.md não tem o `delivery` dos lembretes")
        self.assertEqual((d["mode"], d["channel"]), ("announce", "telegram"))
        self.assertEqual(d["completionDestination"]["mode"], "webhook")

    def test_webhook_aponta_para_a_rota_do_bot(self):
        u = urlparse(delivery_do_agents()["completionDestination"]["to"])
        self.assertEqual((u.scheme, u.hostname, u.path), ("http", "127.0.0.1", "/reminder"))
        self.assertIn(f'@app.post("{u.path}")', open(os.path.join(AQUI, "bot.py")).read())
        if os.path.exists(RUN_SH):
            self.assertIn(f"BOT_PORT={u.port}", open(RUN_SH).read())

    def test_ssrf_libera_o_host_do_webhook(self):
        if not os.access(OC_CONF, os.R_OK):
            self.skipTest("sem acesso ao openclaw.json")
        cron = json.load(open(OC_CONF)).get("cron", {})
        host = urlparse(delivery_do_agents()["completionDestination"]["to"]).hostname
        self.assertIn(host, cron.get("webhookSsrfPolicy", {}).get("allowedHostnames", []))
        self.assertTrue(cron.get("webhookToken"))

    def test_telegram_vai_para_o_dono(self):
        if not os.access(OC_CONF, os.R_OK):
            self.skipTest("sem acesso ao openclaw.json")
        donos = json.load(open(OC_CONF)).get("commands", {}).get("ownerAllowFrom", [])
        self.assertIn("telegram:" + delivery_do_agents()["to"], donos)

    def test_skill_nao_se_anuncia_para_lembretes(self):
        # revisão final 15/10: com "lembretes na hora marcada" na descrição, o turno agendado podia abrir a skill e
        # rodar o entregar.sh, e o /reminder tocava de novo com o texto do turno (entrega dupla)
        desc = re.search(r"^description: (.*)$", open(SKILL).read(), re.M).group(1)
        self.assertNotIn("lembrete", desc.lower())

    def test_agents_diz_que_o_turno_agendado_so_escreve(self):
        txt = open(AGENTS).read()
        self.assertIn("entregar.sh", txt)
        self.assertIn("twice", txt)

    def test_fato_num_lugar_so(self):
        self.assertNotIn("completionDestination", open(SKILL).read())


if __name__ == "__main__":
    unittest.main()
