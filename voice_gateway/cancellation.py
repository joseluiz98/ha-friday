"""Cancelamento de uma sessão de voz pelo botão vermelho (spec 2026-11-04): reúne as tarefas de uma conversa
(pedidos ao cérebro, entregas em segundo plano) para cancelá-las de uma vez."""
import asyncio


class SessionCancel:
    def __init__(self):
        self._tasks = set()
        self._cancelled = False

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def track(self, task: asyncio.Task) -> asyncio.Task:
        """Guarda a tarefa até ela terminar. Depois de `cancel()`, uma tarefa nova nasce cancelada."""
        if self._cancelled:
            task.cancel()
            return task
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def cancel(self) -> int:
        """Cancela as tarefas ainda não terminadas e devolve quantas. Idempotente: a segunda chamada devolve 0."""
        if self._cancelled:
            return 0
        self._cancelled = True
        n = 0
        for t in list(self._tasks):
            if not t.done():
                t.cancel()
                n += 1
        self._tasks.clear()
        return n
