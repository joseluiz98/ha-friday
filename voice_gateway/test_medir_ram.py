"""cd /srv/friday/app && python3 -m unittest test_medir_ram -v"""
import os, tempfile, unittest
import medir_ram as mr


def _proc(raiz, pid, nome, cmd, rss_kb, swap_kb):
    d = os.path.join(raiz, str(pid)); os.makedirs(d)
    with open(os.path.join(d, "status"), "w") as f:
        f.write(f"Name:\t{nome}\nVmRSS:\t{rss_kb} kB\nVmSwap:\t{swap_kb} kB\n")
    with open(os.path.join(d, "cmdline"), "wb") as f:
        f.write(cmd.replace(" ", "\0").encode())


class TestMedirRam(unittest.TestCase):
    def test_lista_processos_em_mb_maior_primeiro(self):
        with tempfile.TemporaryDirectory() as raiz:
            _proc(raiz, 10, "node", "node /app/openclaw gateway", 1024 * 900, 1024 * 100)
            _proc(raiz, 20, "python3", "python3 bot.py", 1024 * 300, 0)
            os.makedirs(os.path.join(raiz, "self"))  # não numérico: ignorado
            r = mr.por_processo(raiz)
        self.assertEqual(r[0], {"pid": 10, "cmd": "node /app/openclaw gateway", "rss_mb": 900, "swap_mb": 100})
        self.assertEqual(r[1]["rss_mb"], 300)

    def test_processo_que_sumiu_nao_quebra(self):
        with tempfile.TemporaryDirectory() as raiz:
            os.makedirs(os.path.join(raiz, "30"))  # sem status (morreu no meio da leitura)
            self.assertEqual(mr.por_processo(raiz), [])


if __name__ == "__main__":
    unittest.main()
