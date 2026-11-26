"""Canal USB único do ReSpeaker XVF3800 (spec 2026-11-17-xvf3800-support).

Anel, mudo, ajustes da partida e DOA falam com a placa por aqui: um lock só (o `vendor/xvf_host.py` não é seguro entre
threads), prazo curto e nunca levanta — uma falha de USB vira uma linha de log e `False`/`None`, e a próxima chamada
reabre a placa. O firmware não guarda nada (# nunca SAVE_CONFIGURATION: desde o 2.0.9 ele gravaria o feixe travado), por
isso `apply_startup` reaplica tudo a cada partida do programa, começando por soltar a trava de feixe.
"""
import os
import sys
import struct
import threading
import time

VID_SEEED, PID_XVF3800 = 0x2886, 0x001A
SYS_DELAY_PADRAO = 12  # amostras a 16 kHz, valor de fábrica (calibrar no lugar pelo HA)
CTRL_LEITURA = 0xC0    # CTRL_IN | CTRL_TYPE_VENDOR | CTRL_RECIPIENT_DEVICE (pyusb), sem importar pyusb aqui
STATUS_OK, STATUS_RETENTAR = 0, 64
FORMATO = {"float": "f", "radians": "f", "int32": "i", "uint16": "H", "uint8": "B"}
PINO_MUDO = 30         # X0D30: 1 = microfone cortado (medido 17/11)


def abrir_vendor(timeout_ms: int = 1000):
    """O `xvf_host.find` do vendor, com prazo curto (o padrão do vendor é 100 s)."""
    pasta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
    sys.path.insert(0, pasta)
    try:
        import xvf_host  # vendor: pyusb + libusb_package
    finally:
        sys.path.pop(0)
    xvf_host.print = lambda *a, **k: None  # o vendor imprime cada leitura; calado só ele (nunca o stdout do processo)
    dev = xvf_host.find(VID_SEEED, PID_XVF3800)
    if dev is None:
        raise RuntimeError("XVF3800 não encontrado")
    dev.TIMEOUT = timeout_ms
    dev.PARAMETERS = xvf_host.PARAMETERS  # para `read_bounded` (a tabela do vendor)
    return dev


LOG_INTERVALO_S = 60.0  # a mesma falha repetida (placa fora, 8 leituras/s do DOA) vira uma linha por minuto


class XvfLink:
    def __init__(self, dev_factory=abrir_vendor, log=None, lock_timeout_s: float = 2.0, relogio=time.monotonic):
        self._fabrica, self._log = dev_factory, log or (lambda m: print(m, file=sys.stderr, flush=True))
        self._lock = threading.Lock()
        self._lock_timeout, self._relogio = lock_timeout_s, relogio
        self._dev = None
        self._ultimo_log = {}
        self.falhas_leitura = 0  # leituras limitadas que falharam (rastreabilidade do programa)

    def _avisar(self, chave, msg):
        agora = self._relogio()
        if agora - self._ultimo_log.get(chave, -1e9) >= LOG_INTERVALO_S:
            self._ultimo_log[chave] = agora
            self._log(msg)

    def _chamar(self, metodo, nome, *args):
        if not self._lock.acquire(timeout=self._lock_timeout):  # USB degradado nunca prende quem chama
            self._avisar(("ocupado", nome), f"xvf3800: {metodo} {nome}: canal ocupado, pulado")
            return False, None
        try:
            if self._dev is None:
                self._dev = self._fabrica()
            return True, getattr(self._dev, metodo)(nome, *args)
        except Exception as e:  # noqa: BLE001 - USB nunca derruba o programa
            self._dev = None  # reabre na próxima
            self._avisar(("falha", metodo, nome, type(e).__name__),
                         f"xvf3800: {metodo} {nome} falhou ({type(e).__name__}: {e})")
            return False, None
        finally:
            self._lock.release()

    def write(self, nome: str, valores) -> bool:
        return self._chamar("write", nome, list(valores))[0]

    def read(self, nome: str):
        ok, v = self._chamar("read", nome)
        return list(v) if ok and v is not None else None

    def read_bounded(self, nome: str, max_retries: int = 5, timeout_ms: int = 300):
        """Leitura de registrador com prazo: no máximo 1 + `max_retries` transferências de `timeout_ms` (o `read` do
        vendor tenta até 100 vezes). Mesmo lock e mesma reabertura do `_chamar`; devolve a lista ou None, nunca levanta."""
        if not self._lock.acquire(timeout=self._lock_timeout):
            self._avisar(("ocupado", nome), f"xvf3800: read_bounded {nome}: canal ocupado, pulado")
            self.falhas_leitura += 1
            return None
        try:
            if self._dev is None:
                self._dev = self._fabrica()
            info = getattr(self._dev, "PARAMETERS", {}).get(nome)
            if info is None:
                self._avisar(("desconhecido", nome), f"xvf3800: read_bounded {nome}: registrador desconhecido")
                self.falhas_leitura += 1
                return None
            resid, cmdid, n, _, tipo = info[:5]
            fmt = FORMATO[tipo]
            tamanho = n * struct.calcsize("<" + fmt)
            for _ in range(1 + max_retries):
                r = bytes(self._dev.dev.ctrl_transfer(CTRL_LEITURA, 0, 0x80 | cmdid, resid, tamanho + 1, timeout_ms))
                if r[0] == STATUS_OK:
                    return list(struct.unpack("<" + fmt * n, r[1:1 + tamanho]))
                if r[0] != STATUS_RETENTAR:
                    break
                time.sleep(0.01)
            self._avisar(("leitura", nome), f"xvf3800: read_bounded {nome}: sem resposta válida")
        except Exception as e:  # noqa: BLE001 - USB nunca derruba o programa
            self._dev = None  # reabre na próxima
            self._avisar(("falha", "read_bounded", nome, type(e).__name__),
                         f"xvf3800: read_bounded {nome} falhou ({type(e).__name__}: {e})")
        finally:
            self._lock.release()
        self.falhas_leitura += 1
        return None


def apply_startup(link: XvfLink, cfg: dict, mudo: bool) -> None:
    """Na partida do programa: solta a trava (nunca fica presa depois de um crash), saídas 8 0 / 7 3, atraso do
    `config.json` e o pino de mudo com o estado gravado."""
    link.write("AEC_FIXEDBEAMSONOFF", [0])
    link.write("AEC_FIXEDBEAMSELEVATION_VALUES", [0.0, 0.0])
    link.write("AUDIO_MGR_OP_L", [8, 0])
    link.write("AUDIO_MGR_OP_R", [7, 3])
    link.write("AUDIO_MGR_SYS_DELAY", [int(cfg.get("xvf_sys_delay", SYS_DELAY_PADRAO))])
    link.write("GPO_WRITE_VALUE", [PINO_MUDO, 1 if mudo else 0])
