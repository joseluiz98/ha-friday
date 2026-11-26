import asyncio, unittest
from unittest import mock

import registro_conversas as rc
import speaker_gate_client as sg

JAN = b"\0" * sg.JANELA_BYTES


def fake(seq):
    it = iter(seq)
    calls = []

    async def post(body):
        calls.append(body)
        r = next(it)
        if isinstance(r, Exception):
            raise r
        return r
    post.calls = calls
    return post


def ok(score, who="jose"):
    return {"available": True, "scores": {who: score}, "best": who, "score": score}


def run(c):
    return asyncio.run(c)


class T(unittest.TestCase):
    def setUp(self):
        sg.SombraPortao._avisou = False

    def test_first_turn_sets_caller_match(self):
        async def go():
            g = sg.SombraPortao("s1", fake([ok(0.2), ok(0.7)]))
            g.comeco_do_turno(False)
            g.audio(JAN * 2)
            return await g.fim_do_turno()
        r = run(go())
        self.assertEqual(r["callerMatch"], "jose")
        self.assertEqual(r["best"], "jose")
        self.assertFalse(r["wouldEnd"])
        self.assertEqual(r["errors"], 0)

    def test_would_end_after_two_low_windows(self):
        async def go():
            g = sg.SombraPortao("s1", fake([ok(0.8), ok(0.1), ok(0.2)]))
            g.comeco_do_turno(False)
            g.audio(JAN * 3)
            return await g.fim_do_turno()
        self.assertTrue(run(go())["wouldEnd"])

    def test_never_scores_without_tv_active_after_first_turn(self):
        async def go():
            p = fake([ok(0.9), ok(0.9)])
            g = sg.SombraPortao("s1", p)
            g.comeco_do_turno(False); g.audio(JAN); await g.fim_do_turno()
            g.comeco_do_turno(False); g.audio(JAN)
            r2 = await g.fim_do_turno()
            n = len(p.calls)
            g.comeco_do_turno(True); g.audio(JAN)
            r3 = await g.fim_do_turno()
            return r2, n, r3, len(p.calls)
        r2, n, r3, m = run(go())
        self.assertIsNone(r2)
        self.assertEqual(n, 1)
        self.assertEqual(m, 2)
        self.assertEqual(r3["callerMatch"], "jose")

    def test_post_failure_counted_not_raised(self):
        async def go():
            g = sg.SombraPortao("s1", fake([RuntimeError("x"), ok(0.9)]))
            g.comeco_do_turno(False)
            g.audio(JAN * 2)
            return await g.fim_do_turno()
        r = run(go())
        self.assertEqual(r["errors"], 1)
        self.assertEqual(r["callerMatch"], "jose")

    def test_unavailable_logged_once(self):
        async def go():
            un = {"available": False}
            g = sg.SombraPortao("s1", fake([un, un, un]))
            g.comeco_do_turno(False)
            g.audio(JAN * 3)
            await g.fim_do_turno()
        with mock.patch.object(sg.logger, "warning") as w:
            run(go())
        self.assertEqual(sum("speaker gate unavailable" in str(c) for c in w.call_args_list), 1)

    def test_speaker_gate_field_in_line(self):
        t = rc.Turnos("k", "s", "a", pi_turns=True)
        t.usuario("oi", 1.0)
        t.portao_voz({"windows": [0.7], "best": "jose", "callerMatch": "jose", "wouldEnd": False, "errors": 0})
        r = t.ela_parou(2.0)
        self.assertEqual(r["speakerGate"]["callerMatch"], "jose")
        t.usuario("de novo", 3.0)
        self.assertNotIn("speakerGate", t.ela_parou(4.0))


if __name__ == "__main__":
    unittest.main()
