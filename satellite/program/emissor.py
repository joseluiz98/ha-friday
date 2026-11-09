"""Emissor de eventos para o agente local (spec 2026-10-29): fire-and-forget. `emitir` nunca bloqueia nem levanta:
só põe o evento numa fila limitada (50, descarta o mais velho) e uma thread própria faz o POST (timeout 0,3 s).
A captura, o laço asyncio e a porta local nunca esperam por rede."""
import collections, json, threading, urllib.request

CAMINHO = "/v1/local/event"
FILA_MAX = 50
TIMEOUT_S = 0.3


def _post(url, corpo, token):
    req = urllib.request.Request(url, data=json.dumps(corpo, ensure_ascii=False).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        r.read()


class Emissor:
    def __init__(self, url, token, enviar=None, iniciar_thread=True):
        self.url, self.token = url, token
        self._enviar = enviar or _post
        self._fila = collections.deque(maxlen=FILA_MAX)  # deque cheia descarta o mais velho; append é atômico
        self._acordar = threading.Event()
        self._thread, self._iniciar, self._trava = None, iniciar_thread, threading.Lock()

    def emitir(self, tipo, dados):
        try:
            if not self.url:
                return
            self._fila.append((tipo, dados))
            if self._iniciar:
                self._garantir_thread()
            self._acordar.set()
        except Exception:
            pass

    def _garantir_thread(self):
        if self._thread is not None:
            return
        with self._trava:
            if self._thread is None:
                self._thread = threading.Thread(target=self._laco, daemon=True, name="emissor")
                self._thread.start()

    def _laco(self):
        while True:
            self._acordar.wait()
            self._acordar.clear()
            while True:
                try:
                    tipo, dados = self._fila.popleft()
                except IndexError:
                    break
                try:  # o token nunca vai para o log; falha = descarta em silêncio
                    url = (self.url or "").rstrip("/") + CAMINHO
                    self._enviar(url, {"tipo": tipo, "dados": dados}, self.token)
                except Exception:
                    pass
