"""Programa de aprendizagem de voz na Pi (spec 2026-10-15 D14–D21): nome → id, roteiro guiado por sons, gravador
das janelas e cadastros guardados por pessoa em /home/jose/vozes/<id>/ (700, fora da limpeza de 7 dias).
Testes: test_cadastro.py."""
import json, os, re, shutil, threading, unicodedata, wave

RAIZ = "/home/jose/vozes"
N_TENTATIVAS, INTERVALO_S, FALA_LIVRE_S, MUDAR_APOS = 10, 6.0, 30.0, 5
FOLGA_S = 0.05        # a janela abre depois que o som terminou: a voz dela não entra na amostra
TEMPO_MUDAR_S = 4.0   # para a pessoa mudar de lugar depois da fala
DURACOES_PADRAO = {"intro": 8.0, "bipe": 0.07, "mudar_lugar": 6.0, "fala_livre": 7.0, "fim": 5.0}
GRAVACAO_PREVISTA_S = 120
_LETRAS = re.compile(r"^[^\W\d_](?:[^\W\d_]|[ \-])*$")


def nome_valido(nome):
    """(nome limpo, id). 1–20 caracteres de letras, espaço ou hífen; o id é o nome sem acento, em minúsculas."""
    if not isinstance(nome, str):
        raise ValueError("nome precisa ser texto")
    limpo = " ".join(nome.split())
    if not 1 <= len(limpo) <= 20 or not _LETRAS.match(limpo):
        raise ValueError("nome: de 1 a 20 letras, espaço ou hífen")
    sem_acento = unicodedata.normalize("NFKD", limpo).encode("ascii", "ignore").decode().lower()
    ident = re.sub(r"[^a-z]+", "_", sem_acento).strip("_")
    if not ident:
        raise ValueError("nome sem letras")
    return limpo, ident


class Roteiro:
    """Linha do tempo da gravação, em segundos do relógio monotônico. `duracoes`: segundos de cada som."""

    def __init__(self, duracoes, inicio):
        self.passos = self._montar(duracoes, inicio)
        self._i = 0

    @staticmethod
    def _montar(d, t):
        p = [(t, "som", "intro")]
        t += d["intro"] + 0.5
        for k in range(1, N_TENTATIVAS + 1):
            if k == MUDAR_APOS + 1:
                p.append((t, "som", "mudar_lugar"))
                t += d["mudar_lugar"] + TEMPO_MUDAR_S
            p.append((t, "som", "bipe"))
            p.append((t + d["bipe"] + FOLGA_S, "janela", f"tentativa_{k:02d}"))
            p.append((t + INTERVALO_S, "fechar_janela", None))
            t += INTERVALO_S
        p.append((t, "som", "fala_livre"))
        t += d["fala_livre"] + 0.3
        p.append((t, "janela", "fala_livre"))
        p.append((t + FALA_LIVRE_S, "fechar_janela", None))
        t += FALA_LIVRE_S
        p.append((t, "som", "fim"))
        p.append((t + d["fim"], "terminar", None))
        return p

    def tique(self, agora):
        saida = []
        while self._i < len(self.passos) and self.passos[self._i][0] <= agora:
            _, acao, valor = self.passos[self._i]
            saida.append((acao, valor))
            self._i += 1
        return saida

    @property
    def duracao_s(self):
        return self.passos[-1][0] - self.passos[0][0]

    @property
    def terminou(self):
        return self._i >= len(self.passos)


class Gravador:
    """Recebe os pedaços de 80 ms da captura (outra thread) e grava a janela aberta em <pasta>/<nome>.wav.
    16/10: guarda também a sessão contínua desde a 1ª janela (sessao.wav) e as marcas de cada janela, em pedaços
    (marcas.json): o modelo base precisa do áudio contínuo; recortes com começo a frio perdem nota."""

    def __init__(self, pasta):
        self.pasta = pasta
        os.makedirs(pasta, mode=0o700, exist_ok=True)
        os.chmod(pasta, 0o700)
        self._nome, self._pcm, self._trava = None, [], threading.Lock()
        self._sessao, self._marcas, self._ini = None, [], None

    def abrir(self, nome):
        self.fechar()
        with self._trava:
            self._nome, self._pcm = nome, []
            if self._sessao is None:
                self._sessao = []
            self._ini = len(self._sessao)

    def alimentar(self, pcm):
        with self._trava:
            if self._sessao is not None:
                self._sessao.append(pcm)
            if self._nome:
                self._pcm.append(pcm)

    def fechar(self):
        with self._trava:
            nome, pcm, self._nome, self._pcm = self._nome, self._pcm, None, []
            if nome:
                self._marcas.append({"nome": nome, "ini": self._ini, "fim": len(self._sessao)})
        if not nome:
            return None
        caminho = os.path.join(self.pasta, nome + ".wav")
        with wave.open(caminho, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes(b"".join(pcm))
        return caminho

    def finalizar(self):
        """Fecha a janela aberta e grava sessao.wav + marcas.json. None se nenhuma janela abriu."""
        self.fechar()
        with self._trava:
            sessao, marcas, self._sessao = self._sessao, list(self._marcas), None
        if not sessao:
            return None
        caminho = os.path.join(self.pasta, "sessao.wav")
        with wave.open(caminho, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes(b"".join(sessao))
        with open(os.path.join(self.pasta, "marcas.json"), "w") as f:
            json.dump(marcas, f)
        return caminho


class Vozes:
    """Cadastros por pessoa: <raiz>/<id>/atual/ (gravações aprovadas) e <raiz>/<id>/cadastro.json.
    Pastas que começam com "_" (negativos, cache) não são pessoas. `ocupado`: gravação ou treino em andamento."""

    def __init__(self, raiz=RAIZ):
        self.raiz, self.ocupado = raiz, False

    def _pessoas(self):
        if not os.path.isdir(self.raiz):
            return []
        return sorted(d for d in os.listdir(self.raiz)
                      if not d.startswith("_") and os.path.isdir(os.path.join(self.raiz, d)))

    def _ficha(self, ident):
        try:
            with open(os.path.join(self.raiz, ident, "cadastro.json")) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def listar(self):
        r = []
        for d in self._pessoas():
            c = self._ficha(d)
            if c:
                r.append({"nome": c.get("nome"), "id": d, "desde": c.get("desde")})
        return r

    def aceitas_de(self, ident):
        c = self._ficha(ident)
        return c.get("aceitas") if c else None

    def pasta_nova(self, ident, agora):
        return os.path.join(self.raiz, ident, f"novo_{agora:%Y%m%d_%H%M%S}")

    def aprovar(self, ident, nome, pasta_nova, numeros, agora):
        destino = os.path.join(self.raiz, ident, "atual")
        if os.path.isdir(destino):
            shutil.rmtree(destino)
        os.rename(pasta_nova, destino)
        ficha = dict({"nome": nome, "id": ident, "desde": agora.strftime("%Y-%m-%d %H:%M")}, **numeros)
        with open(os.path.join(self.raiz, ident, "cadastro.json"), "w") as f:
            json.dump(ficha, f, ensure_ascii=False)

    def descartar(self, pasta_nova):
        shutil.rmtree(pasta_nova, ignore_errors=True)

    def guardar_reprovado(self, ident, pasta_nova):
        """16/10: o último cadastro reprovado de cada pessoa fica em <id>/reprovado (para diagnóstico; 700, só na Pi)."""
        destino = os.path.join(self.raiz, ident, "reprovado")
        if os.path.isdir(destino):
            shutil.rmtree(destino)
        os.rename(pasta_nova, destino)

    def limpar_novos(self):
        """Na partida (D: Pi reiniciada no meio): apaga as gravações que nunca passaram pelo portão."""
        n = 0
        for d in self._pessoas():
            for x in os.listdir(os.path.join(self.raiz, d)):
                if x.startswith("novo_"):
                    shutil.rmtree(os.path.join(self.raiz, d, x), ignore_errors=True)
                    n += 1
        return n
