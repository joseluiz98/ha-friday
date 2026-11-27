"""Spec 2026-11-24 goal 6: "Friday, parar" para os timers que tocam em TODOS os satélites. A ferramenta `parar_timers`
manda `timers_parar` a cada satélite cadastrado. cd voice_gateway && python3 -m unittest -q test_parar_timers"""
import asyncio, unittest

import parar_timers as pt

SATS = {
    "sat-0a1b2c": {"comodo": "Sala", "senha": "a" * 43, "ip": "192.0.2.15"},
    "sat-0d1e2f": {"comodo": "Escritório", "senha": "b" * 43, "ip": "192.0.2.16"},
}


class Deps:
    def __init__(self):
        self.satelites = SATS
        self.wifi = {"sat-0a1b2c": "192.0.2.8", "sat-0d1e2f": "192.0.2.9"}
        self.comandos = []
        self.respostas = {}   # ip -> dict | Exception
        self.status_ = {}     # ip -> dict | Exception
        self.atraso = {}      # ip -> segundos
        self.comando_timeout_s = 3.0

    async def ip_wifi(self, sid):
        return self.wifi.get(sid)

    async def comandar(self, ip, senha, comando):
        self.comandos.append((ip, comando))
        if self.atraso.get(ip):
            await asyncio.sleep(self.atraso[ip])
        r = self.respostas.get(ip, {"ok": True, "parados": [], "tocando": False})
        if isinstance(r, Exception):
            raise r
        return r

    async def status(self, ip, senha):
        r = self.status_.get(ip, {"estado": "parada", "alarmes": []})
        if isinstance(r, Exception):
            raise r
        return r


class TestArgs(unittest.TestCase):
    def test_defaults_and_trimming(self):
        self.assertEqual(pt.args_parar_timers({}), {"nome": None, "comodo": None})
        self.assertEqual(pt.args_parar_timers({"nome": " arroz ", "comodo": " Sala "}), {"nome": "arroz", "comodo": "Sala"})
        self.assertEqual(pt.args_parar_timers({"nome": ""}), {"nome": None, "comodo": None})

    def test_rejects_non_strings_and_long_values(self):
        for ruim in ({"nome": 5}, {"comodo": ["Sala"]}, {"nome": "x" * 41}, "texto", 5):
            with self.assertRaises(ValueError, msg=ruim):
                pt.args_parar_timers(ruim)


class TestPararEmTodos(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.deps = Deps()

    async def test_stop_without_args_sends_to_every_satellite(self):
        self.deps.respostas = {
            "192.0.2.15": {"ok": True, "parados": [{"id": "t111111", "nome": "arroz"}], "tocando": False},
            "192.0.2.16": {"ok": True, "parados": [{"id": "t222222", "nome": None}], "tocando": False}}
        r = await pt.parar_em_todos(self.deps)
        self.assertEqual(sorted(c[0] for c in self.deps.comandos), ["192.0.2.15", "192.0.2.16"])
        self.assertTrue(all(c[1] == {"acao": "timers_parar", "nome": None} for c in self.deps.comandos))
        self.assertEqual(r, {"ok": True, "parados": [{"timer": "arroz", "comodo": "Sala"},
                                                      {"timer": "sem nome", "comodo": "Escritório"}],
                             "sem_resposta": []})

    async def test_stop_by_room_only_hits_that_room(self):
        await pt.parar_em_todos(self.deps, comodo="escritorio")  # sem acento e minúsculo: o mesmo cômodo
        self.assertEqual([c[0] for c in self.deps.comandos], ["192.0.2.16"])

    async def test_unknown_room_changes_nothing_and_lists_the_rooms(self):
        r = await pt.parar_em_todos(self.deps, comodo="garagem")
        self.assertEqual(self.deps.comandos, [])
        self.assertFalse(r["ok"])
        self.assertEqual(sorted(r["comodos"]), ["Escritório", "Sala"])

    async def test_stop_by_name_passes_the_name(self):
        await pt.parar_em_todos(self.deps, nome="arroz")
        self.assertTrue(all(c[1]["nome"] == "arroz" for c in self.deps.comandos))

    async def test_one_satellite_timing_out_is_listed_not_fatal(self):
        self.deps.comando_timeout_s = 0.05
        self.deps.atraso = {"192.0.2.16": 5}
        self.deps.respostas = {"192.0.2.15": {"ok": True, "parados": [{"id": "t111111", "nome": "arroz"}]}}
        r = await pt.parar_em_todos(self.deps)
        self.assertEqual(r["parados"], [{"timer": "arroz", "comodo": "Sala"}])
        self.assertEqual(r["sem_resposta"], ["Escritório"])

    async def test_falls_back_to_the_wifi_address(self):
        self.deps.respostas = {"192.0.2.15": ConnectionError("x"),
                               "192.0.2.8": {"ok": True, "parados": [{"id": "t111111", "nome": "arroz"}]}}
        r = await pt.parar_em_todos(self.deps, comodo="Sala")
        self.assertEqual(r["parados"], [{"timer": "arroz", "comodo": "Sala"}])

    async def test_a_satellite_without_the_command_counts_as_no_answer(self):
        self.deps.respostas = {"192.0.2.15": {"ok": False, "erro": "ação desconhecida: timers_parar"}}
        r = await pt.parar_em_todos(self.deps, comodo="Sala")
        self.assertEqual((r["parados"], r["sem_resposta"]), ([], ["Sala"]))


class TestAlarmesNasCasas(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.deps = Deps()

    async def test_lists_ringing_timers_of_the_other_satellites(self):
        self.deps.status_ = {"192.0.2.15": {"alarmes": [{"id": "t111111", "nome": "arroz"}]},
                             "192.0.2.16": {"alarmes": [{"id": "t222222", "nome": "pizza"}, {"id": "t333333", "nome": None}]}}
        r = await pt.alarmes_nas_casas(self.deps, excluir="sat-0a1b2c")
        self.assertEqual(r, [{"nome": "pizza", "comodo": "Escritório"}, {"nome": None, "comodo": "Escritório"}])

    async def test_silent_satellites_and_old_status_are_ignored(self):
        self.deps.status_ = {"192.0.2.16": ConnectionError("x"), "192.0.2.9": {"estado": "parada"}}
        self.assertEqual(await pt.alarmes_nas_casas(self.deps, excluir="sat-0a1b2c"), [])


if __name__ == "__main__":
    unittest.main()
