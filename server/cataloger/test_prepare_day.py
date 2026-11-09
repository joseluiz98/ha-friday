"""Testes do preparo do dia do Lucid Dreaming (prepare_day.py)."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import prepare_day as pd

TZ = ZoneInfo("America/Sao_Paulo")


def rec(ts, prompt="oi", response="olá", mode="friday", session="friday-satelite-sat-0a1b2c", tools="[]"):
    return {"ts": ts, "mode": mode, "sessionKey": session, "prompt": prompt, "response": response, "tools": tools}


def escrever(pasta, nome, registros, extra=()):
    linhas = [json.dumps(r) for r in registros] + list(extra)
    (Path(pasta) / nome).write_text("\n".join(linhas) + "\n", encoding="utf-8")


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.nd = self.tmp / "nd"
        self.nd.mkdir()
        self.cat = self.tmp / "cat"
        self.fri = self.tmp / "fri"
        self.fri.mkdir()
        (self.fri / "MEMORY.md").write_text("memoria\n", encoding="utf-8")
        (self.fri / "USER.md").write_text("usuario\n", encoding="utf-8")


class TestVoiceLines(Base):
    def test_dia_de_brasilia_junta_dois_arquivos_utc(self):
        escrever(self.nd, "2026-10-20.ndjson", [rec("2026-10-20T01:30:00Z", prompt="boa noite")])
        escrever(self.nd, "2026-10-19.ndjson", [rec("2026-10-19T02:00:00Z", prompt="fora do dia"),
                                                rec("2026-10-19T15:00:00.123Z", prompt="meio-dia")])
        linhas = pd.voice_lines(date(2026, 10, 19), self.nd)
        self.assertEqual(len(linhas), 2)
        self.assertTrue(any(l.startswith("22:30 [") and "boa noite" in l for l in linhas))
        self.assertTrue(any(l.startswith("12:00 [") for l in linhas))
        self.assertFalse(any("fora do dia" in l for l in linhas))

    def test_formato_igual_ao_teste_validado(self):
        r = rec("2026-10-19T15:00:00Z", prompt="acender a luz", response="Pronto.",
                tools="[{'name': 'intent__HassTurnOn', 'summary': '{}'}]")
        self.assertEqual(pd.format_turn(r, TZ),
                         "12:00 [satélite (escritório)] pessoa: acender a luz | Friday: Pronto. | ferramentas: intent__HassTurnOn")

    def test_ferramentas_lista_e_varias(self):
        r = rec("2026-10-19T15:00:00Z", tools=[{"name": "a"}, {"name": "b"}])
        self.assertTrue(pd.format_turn(r, TZ).endswith("| ferramentas: a, b"))
        r = rec("2026-10-19T15:00:00Z", tools="não é lista python")
        self.assertNotIn("ferramentas", pd.format_turn(r, TZ))

    def test_canal_pagina(self):
        r = rec("2026-10-19T15:00:00Z", session="friday-web")
        self.assertIn("[página/outro]", pd.format_turn(r, TZ))

    def test_prompt_vazio_e_linha_quebrada(self):
        escrever(self.nd, "2026-10-19.ndjson",
                 [rec("2026-10-19T15:00:00Z", prompt=""), rec("2026-10-19T16:00:00Z", mode="voice")],
                 extra=["{quebrada", '{"ts": "2026-10-19T17:00:00Z", "mode": "friday"}', "[1,2]"])
        linhas = pd.voice_lines(date(2026, 10, 19), self.nd)
        self.assertEqual(len(linhas), 2)  # a aberta pela Pi e a sem prompt (campo ausente)
        self.assertIn("pessoa: (aberta pela Pi, sem fala)", linhas[0])

    def test_ts_invalido_ignorado(self):
        escrever(self.nd, "2026-10-19.ndjson", [rec("lixo")])
        self.assertEqual(pd.voice_lines(date(2026, 10, 19), self.nd), [])


class TestConstantes(unittest.TestCase):
    def test_cataloger_ws_padrao(self):
        self.assertEqual(pd._DEFAULTS["CATALOGER_WS"], "/srv/friday/state/cataloger")


class TestPendentes(unittest.TestCase):
    def test_pendentes_ate_tres_mais_antigo_primeiro(self):
        st = {"last_done": "2026-10-15", "pending": ["2026-10-10"]}
        self.assertEqual(pd.pending_days(st, date(2026, 10, 21)),
                         [date(2026, 10, 10), date(2026, 10, 16), date(2026, 10, 17)])

    def test_last_done_nulo_so_ontem(self):
        self.assertEqual(pd.pending_days({"last_done": None, "pending": []}, date(2026, 10, 21)),
                         [date(2026, 10, 20)])

    def test_sem_repeticao(self):
        st = {"last_done": "2026-10-18", "pending": ["2026-10-19", "2026-10-19"]}
        self.assertEqual(pd.pending_days(st, date(2026, 10, 21)), [date(2026, 10, 19), date(2026, 10, 20)])


class TestSkills(unittest.TestCase):
    def test_skills_text(self):
        self.assertEqual(pd.skills_text({"skills": [{"name": "weather", "description": "Current weather"}]}),
                         "- weather: Current weather")


class TestBackup(Base):
    def test_backup_guarda_14(self):
        bdir = self.tmp / "backups"
        for i in range(16):
            pd.backup(self.fri, bdir, date(2026, 10, 1) + __import__("datetime").timedelta(days=i))
        nomes = sorted(p.name for p in bdir.iterdir())
        self.assertEqual(len(nomes), 14)
        self.assertEqual(nomes[0], "20261003")
        self.assertEqual(nomes[-1], "20261016")
        self.assertEqual((bdir / "20261016" / "USER.md").read_text(encoding="utf-8"), "usuario\n")


class TestCli(Base):
    def rodar(self, args, hoje=date(2026, 10, 21)):
        env = {"NDJSON_DIR": str(self.nd), "CATALOGER_WS": str(self.cat), "FRIDAY_WS": str(self.fri),
               "OC": "/nonexistent/oc", "TODAY": hoje.isoformat()}
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            saida = io.StringIO()
            with contextlib.redirect_stdout(saida):
                cod = pd.main(args)
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        return cod, saida.getvalue()

    def estado(self):
        return json.loads((self.cat / "state" / "lucid.json").read_text(encoding="utf-8"))

    def test_samba_fora_marca_pendente_sem_escrever(self):
        self.nd.rmdir()
        self.cat.mkdir()
        cod, saida = self.rodar(["--next"])
        self.assertEqual(cod, 0)
        self.assertEqual(saida, "")
        self.assertFalse((self.cat / "inbox").exists())
        self.assertIn("2026-10-20", self.estado()["pending"])

    def test_dia_sem_turnos_vira_feito(self):
        cod, saida = self.rodar(["--next"])
        self.assertEqual((cod, saida), (0, ""))
        self.assertEqual(self.estado()["last_done"], "2026-10-20")
        self.assertFalse((self.cat / "inbox" / "voice-day.md").exists())

    def test_com_turnos_grava_inbox_backup_e_imprime(self):
        escrever(self.nd, "2026-10-20.ndjson", [rec("2026-10-20T15:00:00Z", prompt="acender a luz")])
        cod, saida = self.rodar(["--next"])
        self.assertEqual(cod, 0)
        self.assertEqual(saida.strip(), "2026-10-20 1")
        voz = (self.cat / "inbox" / "voice-day.md").read_text(encoding="utf-8")
        self.assertTrue(voz.startswith("# Conversas de voz de 2026-10-20 (horário de Brasília, said_by=unknown)"))
        self.assertIn("12:00 [satélite (escritório)] pessoa: acender a luz", voz)
        self.assertTrue((self.cat / "inbox" / "brain-skills.md").exists())  # vazio: oc indisponível
        self.assertEqual((self.cat / "backups" / "20261020" / "MEMORY.md").read_text(encoding="utf-8"), "memoria\n")
        # ainda não está feito: --next não mexe no estado
        self.assertFalse((self.cat / "state" / "lucid.json").exists())

    def test_done_atualiza_e_remove_pendente(self):
        (self.cat / "state").mkdir(parents=True)
        (self.cat / "state" / "lucid.json").write_text(
            json.dumps({"last_done": "2026-10-15", "pending": ["2026-10-12", "2026-10-17"]}))
        self.rodar(["--done", "2026-10-17"])
        st = self.estado()
        self.assertEqual(st["last_done"], "2026-10-17")
        self.assertEqual(st["pending"], ["2026-10-12"])
        self.rodar(["--done", "2026-10-16"])  # menor: não recua
        self.assertEqual(self.estado()["last_done"], "2026-10-17")


if __name__ == "__main__":
    unittest.main()
