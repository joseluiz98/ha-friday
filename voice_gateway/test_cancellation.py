"""cd ~/addressee-stage/app && python3 -B -m unittest -q test_cancellation"""
import asyncio
import unittest

from cancellation import SessionCancel


class TestSessionCancel(unittest.TestCase):
    def test_cancel_cancels_unfinished_tracked_tasks(self):
        async def main():
            c = SessionCancel()
            a = c.track(asyncio.create_task(asyncio.sleep(10)))
            b = c.track(asyncio.create_task(asyncio.sleep(10)))
            feita = c.track(asyncio.create_task(asyncio.sleep(0)))
            await feita
            await asyncio.sleep(0)
            n = c.cancel()
            await asyncio.gather(a, b, return_exceptions=True)
            return n, a.cancelled(), b.cancelled(), feita.cancelled(), c.cancelled
        n, a, b, f, flag = asyncio.run(main())
        self.assertEqual(n, 2)
        self.assertTrue(a and b)
        self.assertFalse(f)
        self.assertTrue(flag)

    def test_cancel_is_idempotent(self):
        async def main():
            c = SessionCancel()
            t = c.track(asyncio.create_task(asyncio.sleep(10)))
            first = c.cancel()
            await asyncio.gather(t, return_exceptions=True)
            return first, c.cancel()
        self.assertEqual(asyncio.run(main()), (1, 0))

    def test_track_after_cancel_cancels_immediately(self):
        async def main():
            c = SessionCancel()
            c.cancel()
            t = c.track(asyncio.create_task(asyncio.sleep(10)))
            await asyncio.gather(t, return_exceptions=True)
            return t.cancelled()
        self.assertTrue(asyncio.run(main()))

    def test_finished_tasks_are_forgotten(self):
        async def main():
            c = SessionCancel()
            t = c.track(asyncio.create_task(asyncio.sleep(0)))
            await t
            await asyncio.sleep(0)
            return len(c._tasks)
        self.assertEqual(asyncio.run(main()), 0)


class TestFiacaoNoBot(unittest.TestCase):
    """Texto do bot.py (não importa o Pipecat): a fiação do cancelamento (Review Focus 4)."""

    @classmethod
    def setUpClass(cls):
        import os
        cls.src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.py")).read()

    def test_segundo_plano_nao_nasce_depois_do_cancelamento(self):
        i = self.src.index("def seguir_em_segundo_plano")
        corpo = self.src[i:i + 500]
        self.assertLess(corpo.index("cancelamento.cancelled"), corpo.index("estado.pendentes += 1"))

    def test_cerebro_e_entrega_passam_pelo_rastreio(self):
        self.assertIn("cancelamento.track(_manter(asyncio.create_task(chamar_cerebro(", self.src)
        self.assertIn("cancelamento.track(asyncio.create_task(entregar(", self.src)

    def test_mensagem_cancelar_cancela_e_fecha(self):
        i = self.src.index('msg.type == "cancelar"')
        trecho = self.src[i:i + 400]
        self.assertIn("cancelamento.cancel()", trecho)
        self.assertIn("await runner.cancel()", trecho)

    def test_entrega_cancelada_vai_ao_registro_sem_push(self):
        i = self.src.index("except asyncio.CancelledError:\n            if not cancelamento.cancelled")
        trecho = self.src[i:self.src.index("except Exception:", i)]
        self.assertIn("cancelado pelo botão vermelho", trecho)
        self.assertNotIn("push_ao_jose", trecho)


if __name__ == "__main__":
    unittest.main()
