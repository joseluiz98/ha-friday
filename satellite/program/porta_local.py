"""Porta local do satélite (spec §2.1): GET /status, GET/PUT /config. Só LAN, senha Bearer.
Base da futura integração no HA (spec §9.2). A senha nunca sai por aqui."""
import hmac, json, os, re
from aiohttp import web
import configuracao as c


_ID_MENSAGEM = re.compile(r"^[a-z0-9]{8,32}$")
ANUNCIO_MAX_BYTES = 16 * 1024 * 1024  # spec 2026-11-20: 500 caracteres em ritmo lento passam de 8 MB (F5)
ANUNCIOS_PADRAO = "/home/jose/anuncios"


def _idade(valor) -> int:
    """X-Announcement-Age: segundos inteiros desde o pedido ao bot (F6). Ausente ou inválido = 0."""
    if isinstance(valor, str) and valor.strip().isdigit():
        return int(valor.strip())
    return 0


def pasta_anuncios():
    return os.environ.get("FRIDAY_ANNOUNCE_DIR", ANUNCIOS_PADRAO)


def criar_app(cfg_caminho, estado_fn, ao_mudar_config, campainha_fn=None, palavras_fn=None, comando_fn=None,
              musica_fn=None, anuncio_fn=None, anuncio_dir=None, anuncio_tem_fn=None):
    async def autorizado(req):
        senha = c.carregar(cfg_caminho)["senha_porta_local"]
        dada = req.headers.get("Authorization", "").removeprefix("Bearer ")
        return hmac.compare_digest(dada, senha)

    async def estado(req):
        if not await autorizado(req):
            return web.Response(status=401)
        dados = estado_fn()
        if musica_fn is not None and req.query.get("musica") == "1":  # OTA (spec 2026-11-10 §6.7): música tocando?
            dados = dict(dados, musica=bool(await musica_fn()))
        return web.json_response(dados)

    async def ler_config(req):
        if not await autorizado(req):
            return web.Response(status=401)
        cfg = c.carregar(cfg_caminho)
        cfg.pop("senha_porta_local", None)
        return web.json_response(cfg)

    async def mudar_config(req):
        if not await autorizado(req):
            return web.Response(status=401)
        try:
            mudancas = await req.json()
            novo = c.validar_mudancas(c.carregar(cfg_caminho), mudancas)
        except (ValueError, json.JSONDecodeError) as e:
            return web.json_response({"erro": str(e)}, status=400)
        c.gravar(cfg_caminho, novo)
        ao_mudar_config(novo)
        novo = dict(novo); novo.pop("senha_porta_local", None)
        return web.json_response(novo)

    async def campainha(req):
        """Spec 2026-10-12 §3: o add-on avisa que há mensagem; só o id vem aqui, o texto chega pela conversa."""
        if not await autorizado(req):
            return web.Response(status=401)
        try:
            corpo = await req.json()
        except json.JSONDecodeError:
            corpo = None
        eid = corpo.get("mensagem") if isinstance(corpo, dict) else None
        if not isinstance(eid, str) or not _ID_MENSAGEM.match(eid):
            return web.json_response({"erro": "mensagem inválida"}, status=400)
        return web.json_response({"resposta": campainha_fn(eid)})

    async def anuncio(req):
        """Spec 2026-11-20: o add-on manda o WAV pronto (campainha e fala vêm do satélite) com o id no cabeçalho; o
        arquivo fica em <pasta>/<id>.wav (atômico) e o maestro decide se toca, enfileira ou cala."""
        if not await autorizado(req):
            return web.Response(status=401)
        aid = req.headers.get("X-Announcement-Id", "")
        if not _ID_MENSAGEM.match(aid):
            return web.json_response({"erro": "id inválido"}, status=400)
        corpo = await req.read()  # acima de client_max_size o aiohttp responde 413
        if len(corpo) > ANUNCIO_MAX_BYTES:  # client_max_size tem 1 KB de folga; o teto de verdade é este
            return web.json_response({"erro": "grande demais"}, status=413)
        if not corpo.startswith(b"RIFF"):
            return web.json_response({"erro": "não é WAV"}, status=400)
        pasta = anuncio_dir or pasta_anuncios()
        destino = os.path.join(pasta, aid + ".wav")
        # Reenvio do mesmo id (o POST do add-on estourou o prazo depois de a Pi receber): o arquivo que o maestro já
        # tem, na fila ou tocando, não é regravado nem apagado aqui; o maestro responde "na_fila".
        ja_tem = bool(anuncio_tem_fn and anuncio_tem_fn(aid))
        if not ja_tem:
            os.makedirs(pasta, exist_ok=True)
            with open(destino + ".tmp", "wb") as f:
                f.write(corpo)
            os.replace(destino + ".tmp", destino)
        try:
            resposta = anuncio_fn(aid, idade_s=_idade(req.headers.get("X-Announcement-Age")))
        except BaseException:  # sem dono para o arquivo: não deixa o WAV (até 16 MB) na SD da Pi
            if not ja_tem:
                try:
                    os.unlink(destino)
                except OSError:
                    pass
            raise
        return web.json_response({"resposta": resposta})

    async def comando(req):
        """Spec 2026-11-05: comandos de provisionamento do agente (play_phrases, ring, wake_word_test,
        open_presentation); a mesma tabela que a conversa usa (comandos.executar)."""
        if not await autorizado(req):
            return web.Response(status=401)
        try:
            corpo = await req.json()
        except json.JSONDecodeError:
            corpo = None
        if not isinstance(corpo, dict) or not isinstance(corpo.get("acao"), str):
            return web.json_response({"erro": "comando inválido"}, status=400)
        return web.json_response(comando_fn(corpo))

    def lista_palavras(cfg):
        """Spec 2026-10-24: sem caminhos de arquivo. `treinada` = o modelo carregou a palavra."""
        carregadas = palavras_fn()
        r = [{"nome": "friday", "modo": cfg["modo_friday"], "limiar": cfg["limiar_palavra"], "principal": True,
              "treinada": "friday" in carregadas}]
        for e in cfg["palavras_extras"]:
            r.append({"nome": e["nome"], "modo": e["modo"], "limiar": e["limiar"], "principal": False,
                      "treinada": e["nome"] in carregadas})
        return r

    async def ler_palavras(req):
        if not await autorizado(req):
            return web.Response(status=401)
        return web.json_response(lista_palavras(c.carregar(cfg_caminho)))

    async def mudar_palavra(req):
        """Troca o modo de uma palavra já cadastrada. Nunca cria palavra (isso é PUT /config, pelo Claude)."""
        if not await autorizado(req):
            return web.Response(status=401)
        nome = req.match_info["nome"]
        cfg = c.carregar(cfg_caminho)
        extras = cfg["palavras_extras"]
        if nome != "friday" and nome not in [e["nome"] for e in extras]:
            return web.json_response({"erro": "palavra inexistente"}, status=404)
        try:
            corpo = await req.json()
        except json.JSONDecodeError:
            corpo = None
        modo = corpo.get("modo") if isinstance(corpo, dict) else None
        if not isinstance(modo, str) or modo not in c.MODOS_PALAVRA:
            return web.json_response({"erro": f"modo é um de {list(c.MODOS_PALAVRA)}"}, status=400)
        if modo != "desligada" and nome not in palavras_fn():
            return web.json_response({"erro": "palavra não carregada (modelo ausente ou não treinado)"}, status=409)
        if nome == "friday":
            mudanca = {"modo_friday": modo}
        else:
            mudanca = {"palavras_extras": [dict(e, modo=modo) if e["nome"] == nome else e for e in extras]}
        try:
            novo = c.validar_mudancas(cfg, mudanca)
        except ValueError as e:
            return web.json_response({"erro": str(e)}, status=400)
        c.gravar(cfg_caminho, novo)
        ao_mudar_config(novo)
        return web.json_response(lista_palavras(novo))

    app = web.Application(client_max_size=ANUNCIO_MAX_BYTES + 1024)
    rotas = [web.get("/status", estado), web.get("/config", ler_config), web.put("/config", mudar_config)]
    if campainha_fn:
        rotas.append(web.post("/doorbell", campainha))
    if anuncio_fn:
        rotas.append(web.post("/announce", anuncio))
    if comando_fn:
        rotas.append(web.post("/command", comando))
    if palavras_fn:
        rotas += [web.get("/wakewords", ler_palavras), web.put("/wakewords/{nome}", mudar_palavra)]
    app.add_routes(rotas)
    return app


async def iniciar(cfg_caminho, estado_fn, ao_mudar_config, porta, campainha_fn=None, palavras_fn=None, comando_fn=None,
                  musica_fn=None, anuncio_fn=None, anuncio_dir=None, anuncio_tem_fn=None):
    runner = web.AppRunner(criar_app(cfg_caminho, estado_fn, ao_mudar_config, campainha_fn, palavras_fn, comando_fn,
                                     musica_fn, anuncio_fn, anuncio_dir, anuncio_tem_fn))
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", porta).start()
    return runner
