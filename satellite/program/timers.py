"""Timers do satélite (spec 2026-10-12 §2). Lógica pura, com o término em relógio de parede (time.time()) para
sobreviver a reinício. Quem grava o arquivo é uma função injetada. Testes: test_timers.py."""
import secrets
import unicodedata

MAX_TIMERS = 10
MAX_SEGUNDOS = 24 * 3600
TOLERANCIA_REINICIO_S = 600  # vencido com a Pi desligada há menos disso: toca ao religar
MAX_NOME = 40
MAX_MUSIC = 80  # spec 2026-10-21 R11: texto da busca de música do alarme


def _chave(nome):
    t = unicodedata.normalize("NFKD", nome or "").encode("ascii", "ignore").decode()
    return " ".join(t.lower().split())


def _valido(x):
    return (isinstance(x, dict) and isinstance(x.get("id"), str)
            and isinstance(x.get("termino"), (int, float)) and not isinstance(x.get("termino"), bool)
            and isinstance(x.get("duracao_s"), int) and (x.get("nome") is None or isinstance(x.get("nome"), str)))


class Timers:
    def __init__(self, gravar):
        self._gravar = gravar  # fn(lista de dicts): grava o arquivo de timers
        self._lista = []

    def _salvar(self):
        self._gravar([dict(x) for x in self._lista])

    def criar(self, segundos, nome, agora: float, music=None) -> dict:
        if not isinstance(segundos, int) or isinstance(segundos, bool) or segundos < 1:
            raise ValueError("duração em segundos inteiros, de 1 s a 24 h")
        if segundos > MAX_SEGUNDOS:
            raise ValueError("acima de 24 h não é timer, é lembrete")
        if len(self._lista) >= MAX_TIMERS:
            raise ValueError(f"já há {MAX_TIMERS} timers ativos, o máximo")
        nome = (nome or "").strip() or None
        if nome is not None and len(nome) > MAX_NOME:
            raise ValueError(f"nome com mais de {MAX_NOME} caracteres")
        if music is not None:
            if not isinstance(music, str) or not 1 <= len(music.strip()) <= MAX_MUSIC or "\n" in music or "\r" in music:
                raise ValueError(f"música do alarme: texto de 1 a {MAX_MUSIC} caracteres, numa linha")
            music = music.strip()
        usados = {y["id"] for y in self._lista}
        tid = "t" + secrets.token_hex(3)
        while tid in usados:  # ~1 em 3 milhões; repetir faria o cancelar por id apagar dois timers
            tid = "t" + secrets.token_hex(3)
        x = {"id": tid, "nome": nome, "duracao_s": segundos, "termino": agora + segundos}
        if music is not None:
            x["music"] = music
        self._lista.append(x)
        self._salvar()
        return dict(x)

    def consultar(self, agora: float, nome=None, id=None) -> list:
        lista = sorted(self._lista, key=lambda x: x["termino"])
        if id:
            lista = [x for x in lista if x["id"] == id]
        elif nome:
            lista = [x for x in lista if _chave(x["nome"]) == _chave(nome)]
        return [dict(x, falta_s=max(0, round(x["termino"] - agora))) for x in lista]

    def cancelar(self, nome=None, todos=False, id=None) -> list:
        if todos:
            alvo = list(self._lista)
        elif id:  # o id é de um timer só; vale mais que o nome (14/10: dois sem nome ou dois "arroz" não se distinguiam)
            alvo = [x for x in self._lista if x["id"] == id]
        elif nome:
            alvo = [x for x in self._lista if _chave(x["nome"]) == _chave(nome)]
            if len(alvo) > 1:  # 14/10: dois "arroz" saíam juntos, em silêncio; o id escolhe um
                raise ValueError("há mais de um timer com esse nome: diga qual (pelo id) ou peça todos")
        elif len(self._lista) <= 1:
            alvo = list(self._lista)
        else:
            raise ValueError("há mais de um timer ativo: diga qual ou peça todos")
        ids = {x["id"] for x in alvo}
        self._lista = [x for x in self._lista if x["id"] not in ids]
        if alvo:
            self._salvar()
        return [dict(x) for x in alvo]

    def vencidos(self, agora: float) -> list:
        venc = sorted((x for x in self._lista if x["termino"] <= agora), key=lambda x: x["termino"])
        if venc:
            ids = {x["id"] for x in venc}
            self._lista = [x for x in self._lista if x["id"] not in ids]
            self._salvar()
        return [dict(x) for x in venc]

    def carregar(self, dados, agora: float):
        """Na partida: (vencidos há menos de 10 min, vencidos há mais). Os ativos voltam à lista."""
        itens = [dict(x) for x in dados if _valido(x)] if isinstance(dados, list) else []
        self._lista = [x for x in itens if x["termino"] > agora][:MAX_TIMERS]
        venc = sorted((x for x in itens if x["termino"] <= agora), key=lambda x: x["termino"])
        recentes = [x for x in venc if agora - x["termino"] < TOLERANCIA_REINICIO_S]
        velhos = [x for x in venc if agora - x["termino"] >= TOLERANCIA_REINICIO_S]
        return recentes, velhos
