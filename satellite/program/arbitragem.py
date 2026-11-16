"""Cliente da disputa entre satélites (spec 2026-11-16 §5.1).

Duas rotas do bot: `POST /satellite_presence` (quantos OUTROS satélites estão online) e `POST /wake_claim` (aviso
de ativação, resposta `go`/`lost`). Regra de ouro: nenhuma falha deixa a Pi muda. Bot fora, versão antiga (404),
senha errada, timeout ou resposta estranha = a Pi age como hoje (`go`, motivo `timeout`) e a presença vira "sozinha".
O programa (Task 4) decide quando chamar; aqui só se fala com o bot. Log: uma linha JSON no stdout (journal)."""
import asyncio
import json
import time
import uuid

ESPERA_DISPUTA_S = 0.3
ESPERA_PRESENCA_S = 2
ESPERA_REGISTRO_S = 2
VEREDITOS = ("go", "lost")
FALLBACK = {"verdict": "go", "reason": "timeout"}


def novo_claim_id():
    """Id de uma ativação (o chamador gera; o bot o usa para contar a mesma ativação uma só vez)."""
    return uuid.uuid4().hex


async def _post_aiohttp(url, json, headers, timeout_s):
    """POST real, mesmo padrão do `_avisar_bot` (ssl=False). Devolve (status, corpo-dict; {} se não for JSON)."""
    import aiohttp  # só na Pi; os testes injetam `post`
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=json, ssl=False, headers=headers,
                          timeout=aiohttp.ClientTimeout(total=timeout_s)) as r:
            try:
                corpo = await r.json()
            except Exception:
                corpo = {}
            return r.status, corpo if isinstance(corpo, dict) else {}


class ClienteDisputa:
    def __init__(self, endereco, senha, post=None, clock=time.monotonic):
        self.endereco = endereco
        self._senha = senha
        self._post = post or _post_aiohttp
        self._clock = clock
        self.outros_online = 0

    def _headers(self):
        return {"Authorization": f"Bearer {self._senha}"}

    async def _enviar(self, rota, corpo, timeout_s):
        """(status, dict) ou levanta. O teto vale mesmo que o `post` injetado ignore `timeout_s`."""
        return await asyncio.wait_for(
            self._post(f"{self.endereco}{rota}", corpo, self._headers(), timeout_s), timeout_s)

    def precisa_disputar(self):
        return self.outros_online > 0

    async def presenca(self):
        """Sinal de presença: guarda quantos outros estão online. Qualquer falha = 0 (sozinha, age sem esperar)."""
        try:
            status, corpo = await self._enviar("/satellite_presence", {}, ESPERA_PRESENCA_S)
            n = corpo.get("others_online") if status == 200 else None
            ok = isinstance(n, int) and not isinstance(n, bool) and n >= 0
            self.outros_online = n if ok else 0
        except Exception:
            self.outros_online = 0

    async def laco_presenca(self, intervalo_s=10.0):
        """Para sempre; nunca levanta (só o cancelamento da tarefa sai)."""
        while True:
            try:
                await self.presenca()
            except asyncio.CancelledError:
                raise
            except BaseException:
                self.outros_online = 0
            await asyncio.sleep(intervalo_s)

    async def disputar(self, claim):
        """Avisa o bot e espera o veredito por até 0,3 s. Timeout/erro/não-200/resposta inválida = `go`."""
        t0 = self._clock()
        resultado = dict(FALLBACK)
        try:
            status, corpo = await self._enviar("/wake_claim", claim, ESPERA_DISPUTA_S)
            if status == 200 and corpo.get("verdict") in VEREDITOS:
                resultado = corpo
        except Exception:
            pass
        ms = round((self._clock() - t0) * 1000)
        print(json.dumps({"arbitration": resultado.get("reason"), "verdict": resultado["verdict"], "ms": ms}),
              flush=True)
        return resultado

    async def registrar(self, claim):
        """Aviso só para registro (sozinha, sem esperar). Nunca levanta."""
        try:
            await self._enviar("/wake_claim", dict(claim, record_only=True), ESPERA_REGISTRO_S)
        except Exception:
            pass
