"""Registro das conversas da Friday no NDJSON do CV (spec 2026-10-05 §4): mesmos campos, mode=friday.

Um turno vai da primeira transcrição do usuário (ou da entrega do cérebro) até ela parar de falar.
Só texto, nunca áudio. Lógica pura: roda sem o Pipecat.
"""
import json, logging, os, queue, threading
from datetime import datetime, timezone


def _iso(agora: float) -> str:
    return datetime.fromtimestamp(agora, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
        f"{int(agora * 1000) % 1000:03d}Z"


def _linha(chave, sessao, alvo, prompt, resposta, ok, erro, dur_ms, ferramentas, turno, agora, whisper=False,
           wake_word=None):
    r = {"ts": _iso(agora), "prompt": prompt, "response": resposta, "ok": ok, "error": erro,
         "durationMs": dur_ms, "sessionKey": chave, "sessionId": sessao, "speakTarget": alvo,
         "warmState": None, "mode": "friday", "tools": ferramentas, "turnNumber": turno, "resumed": False}
    if whisper:  # 22/10: modo sussurro (spec 2026-10-20); sem o campo = conversa normal (retrocompatível)
        r["whisper"] = True
    if wake_word:  # 28/10: a palavra que abriu a conversa, só no 1º turno (Jarvis Logbook)
        r["wakeWord"] = wake_word
    return r


class Turnos:
    def __init__(self, session_key: str, session_id: str, speak_target: str, whisper: bool = False,
                 wake_word: str | None = None):
        self.chave, self.sessao, self.alvo = session_key, session_id, speak_target
        self.whisper = bool(whisper)
        self.wake_word = wake_word
        self.numero = 0
        self._novo()

    def _novo(self):
        self._usuario, self._ela, self._ferramentas, self._t_usuario = [], [], [], None
        self._ids = set()

    def usuario(self, texto: str, agora: float):
        self._usuario.append(texto)
        self._t_usuario = agora

    def entrega_do_cerebro(self, pedido: str, agora: float):
        self._usuario.append(f"[resposta do cérebro a: {pedido}]")
        self._t_usuario = agora

    def ela(self, texto: str):
        self._ela.append(texto)

    def ferramenta(self, nome: str, resumo: str, chamada_id=None):
        if chamada_id is not None:
            if chamada_id in self._ids:
                return  # o mesmo quadro passa nas duas direções
            self._ids.add(chamada_id)
        self._ferramentas.append({"name": nome, "summary": resumo[:200]})

    def ela_parou(self, agora: float):
        if not self._ela and not self._usuario:
            return None
        self.numero += 1
        dur = int(round((agora - self._t_usuario) * 1000)) if self._t_usuario else None
        r = _linha(self.chave, self.sessao, self.alvo, "".join(self._usuario).strip(),
                   "".join(self._ela).strip(), True, None, dur, self._ferramentas, self.numero, agora,
                   self.whisper, self.wake_word if self.numero == 1 else None)
        self._novo()
        return r


def linha_avulsa(chave, sessao, alvo, pedido, resposta, erro, agora, whisper=False):
    return _linha(chave, sessao, alvo, f"[resposta do cérebro a: {pedido}]", resposta, False, erro, None, [], None,
                  agora, whisper)


def gravar(registro: dict, pasta: str):
    os.makedirs(pasta, mode=0o700, exist_ok=True)
    caminho = os.path.join(pasta, registro["ts"][:10] + ".ndjson")
    novo = not os.path.exists(caminho)
    _anexar(caminho, json.dumps(registro, ensure_ascii=False) + "\n")
    if novo:
        os.chmod(caminho, 0o600)


def _anexar(caminho: str, texto: str) -> None:
    """Anexa num único write. Se o arquivo terminar sem quebra de linha (escrita cortada pelo Samba ou do outro
    escritor), começa numa linha nova para não colar o registro numa linha quebrada (minor 12 de 19/10)."""
    dado = texto.encode("utf-8")
    with open(caminho, "a+b") as f:
        f.seek(0, os.SEEK_END)
        if f.tell() > 0:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                dado = b"\n" + dado
        f.write(dado)


def descarregar_reserva(pasta: str, reserva: str) -> int:
    """Passa para a pasta compartilhada o que ficou guardado na reserva local (spec 2026-10-18 §4.9).
    Faz append em arquivo de mesmo nome (o Claude Voice grava no mesmo) e só apaga o da reserva depois de completo.
    Linhas que o destino já tem (um descarregamento anterior que anexou mas não chegou a apagar a reserva) não
    são anexadas de novo (minor 12 de 19/10)."""
    try:
        nomes = sorted(n for n in os.listdir(reserva) if n.endswith(".ndjson"))
    except OSError:
        return 0
    total = 0
    for nome in nomes:
        origem = os.path.join(reserva, nome)
        with open(origem, "r", encoding="utf-8") as f:
            conteudo = f.read()
        if conteudo:
            destino = os.path.join(pasta, nome)
            novo = not os.path.exists(destino)
            ja = set()
            if not novo:
                with open(destino, "r", encoding="utf-8", errors="replace") as f:
                    ja = set(f.read().splitlines())
            faltam = [x for x in conteudo.splitlines() if x and x not in ja]
            if faltam:
                _anexar(destino, "\n".join(faltam) + "\n")
                if novo:
                    os.chmod(destino, 0o600)
            total += len(faltam)
        os.remove(origem)
    return total


def gravar_com_reserva(registro: dict, pasta: str, reserva: str) -> str:
    """Grava na pasta compartilhada; se ela estiver fora, guarda na reserva local. Nunca cria a pasta.
    Devolve "pasta" ou "reserva"."""
    if os.path.isdir(pasta) and os.access(pasta, os.W_OK):
        try:
            descarregar_reserva(pasta, reserva)
            gravar(registro, pasta)
            return "pasta"
        except OSError:
            pass
    gravar(registro, reserva)
    return "reserva"


def spool_has_files(reserva: str) -> bool:
    try:
        return any(n.endswith(".ndjson") for n in os.listdir(reserva))
    except OSError:
        return False


def drain_if_back(pasta: str, reserva: str) -> int:
    """Descarrega a reserva se houver o que descarregar e a pasta compartilhada estiver de volta."""
    if spool_has_files(reserva) and os.path.isdir(pasta) and os.access(pasta, os.W_OK):
        return descarregar_reserva(pasta, reserva)
    return 0


class GravadorEmFila:
    """Uma única thread grava; `enviar` nunca bloqueia (com o Samba travado, um write pode levar ~1 min).
    Enquanto houver algo na reserva (gravado lá ou achado ao iniciar), tenta descarregá-la a cada `idle_s` sem
    registros: ela desce quando o compartilhamento volta, mesmo sem conversa nova. Com a reserva vazia, só espera."""

    def __init__(self, pasta: str, reserva: str, gravar=gravar_com_reserva, ao_gravar=None, idle_s: float = 60,
                 drain=drain_if_back):
        self._pasta, self._reserva, self._gravar, self._ao_gravar = pasta, reserva, gravar, ao_gravar
        self._idle_s, self._drain = idle_s, drain
        self._spool_pending = spool_has_files(reserva)
        self._fila = queue.Queue()
        self._thread = threading.Thread(target=self._laco, name="gravador-conversas", daemon=True)
        self._thread.start()

    def enviar(self, registro: dict) -> None:
        self._fila.put_nowait(registro)

    def _laco(self):
        while True:
            try:
                registro = self._fila.get(timeout=self._idle_s if self._spool_pending else None)
            except queue.Empty:
                try:
                    self._drain(self._pasta, self._reserva)
                except Exception:
                    logging.getLogger(__name__).exception("falha ao descarregar a reserva de conversas")
                self._spool_pending = spool_has_files(self._reserva)
                continue
            try:
                destino = self._gravar(registro, self._pasta, self._reserva)
                self._spool_pending = destino == "reserva"
                if self._ao_gravar:
                    self._ao_gravar(destino)
            except Exception:
                logging.getLogger(__name__).exception("falha ao gravar o registro da conversa")
            finally:
                self._fila.task_done()

    def esperar(self, timeout: float) -> bool:
        feito = threading.Event()
        threading.Thread(target=lambda: (self._fila.join(), feito.set()), daemon=True).start()
        return feito.wait(timeout)
