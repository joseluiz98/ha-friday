"""Atividade da voz para quem decide se dá para reiniciar (minor 13 da revisão de 19/10).

Grava /srv/friday/state/activity.json: conversas abertas, turnos do cérebro a caminho (dentro ou fora de uma
conversa) e `updated_at`, renovado a cada 20 s. A porta de status e o agente do HA leem este arquivo: duas sessões ao
mesmo tempo contam as duas, um turno do cérebro em segundo plano conta, e um bot morto (sem renovação há 90 s) não
segura o reinício. Falha de escrita nunca chega à voz.
"""
import json, logging, os, threading, time


class Activity:
    def __init__(self, path: str, clock=time.time):
        self.path, self.clock = path, clock
        self._lock = threading.Lock()
        self._sessions: dict[str, float] = {}
        self._brain = 0
        self.started_at = clock()
        self.write()

    def opened(self, session: str) -> None:
        with self._lock:
            self._sessions.setdefault(session, self.clock())
        self.write()

    def closed(self, session: str) -> None:
        with self._lock:
            self._sessions.pop(session, None)
        self.write()

    def brain_started(self) -> None:
        with self._lock:
            self._brain += 1
        self.write()

    def brain_finished(self) -> None:
        with self._lock:
            self._brain = max(0, self._brain - 1)
        self.write()

    def snapshot(self) -> dict:
        with self._lock:
            return {"pid": os.getpid(), "started_at": self.started_at, "updated_at": self.clock(),
                    "sessions": sorted(self._sessions), "brain_turns": self._brain}

    def write(self) -> None:
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.snapshot(), f)
            os.replace(tmp, self.path)
        except OSError:
            logging.getLogger(__name__).warning("activity: falha ao gravar %s", self.path)

    def start_heartbeat(self, every: float = 20) -> threading.Thread:
        def laco():
            while True:
                time.sleep(every)
                self.write()
        t = threading.Thread(target=laco, daemon=True, name="activity")
        t.start()
        return t
