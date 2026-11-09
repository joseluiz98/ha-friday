"""RAM por processo dentro do container do add-on (08/10). O Supervisor só dá o total do add-on e o
add-on SSH não enxerga os processos daqui; o bot enxerga. Teste: test_medir_ram.py."""
import os


def _kb(texto, campo):
    for linha in texto.splitlines():
        if linha.startswith(campo + ":"):
            return int(linha.split()[1])
    return 0


def por_processo(raiz="/proc"):
    """[{pid, cmd, rss_mb, swap_mb}], maior RSS primeiro. Processo que some no meio é ignorado."""
    saida = []
    for nome in os.listdir(raiz):
        if not nome.isdigit():
            continue
        try:
            with open(os.path.join(raiz, nome, "status")) as f:
                st = f.read()
            with open(os.path.join(raiz, nome, "cmdline"), "rb") as f:
                cmd = f.read().replace(b"\0", b" ").decode(errors="replace").strip()
        except OSError:
            continue
        saida.append({"pid": int(nome), "cmd": cmd[:80], "rss_mb": round(_kb(st, "VmRSS") / 1024),
                      "swap_mb": round(_kb(st, "VmSwap") / 1024)})
    return sorted(saida, key=lambda p: -p["rss_mb"])
