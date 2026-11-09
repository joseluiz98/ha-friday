"""Comandos que a Friday manda ao satélite dentro da conversa (spec 2026-10-12 §4). Lógica pura: devolve a
resposta (que volta ao bot com o mesmo id) e os efeitos que o programa executa (ex.: tocar o bip de volume)."""
import os, re
import volume as v
import cadastro
import ring

_CHAVE_FRASE = re.compile(r"^[a-z0-9_]{1,40}$")
_ID_AREA = re.compile(r"^[a-z0-9_]{1,64}$")


def _timer_publico(x, agora):
    return {"id": x["id"], "nome": x["nome"], "duracao_s": x["duracao_s"], "falta_s": max(0, round(x["termino"] - agora))}


def _volume(cmd, ctl):
    dados = {k: cmd[k] for k in ("numero", "percentual", "ajuste") if cmd.get(k) is not None}
    if len(dados) != 1:
        raise ValueError("informe só um: numero, percentual ou ajuste")
    if "numero" in dados:
        k = ctl.definir(v.posicao_de_numero(dados["numero"]))
    elif "percentual" in dados:
        k = ctl.definir(v.posicao_de_percentual(dados["percentual"]))
    elif dados["ajuste"] in ("mais", "menos"):
        k = ctl.passo(+1 if dados["ajuste"] == "mais" else -1)
    else:
        raise ValueError("ajuste é 'mais' ou 'menos'")
    return v.descrever(k)


def _frases(cmd, sons_dir):
    chaves = cmd.get("keys")
    if not isinstance(chaves, list) or not chaves or len(chaves) > 40 \
            or not all(isinstance(k, str) and _CHAVE_FRASE.match(k) for k in chaves):
        raise ValueError("keys é uma lista de chaves de frase")
    caminhos = []
    for k in chaves:
        c = os.path.join(sons_dir or "", "onboarding", k + ".wav")
        if sons_dir and os.path.isfile(c):
            caminhos.append(c)
        else:
            print(f'{{"frase_ausente": "{k}"}}', flush=True)  # pula e registra
    return caminhos


def executar(cmd: dict, ctl, timers, agora: float, vozes=None, sons_dir=None, estado_maestro=None):
    acao, efeitos = cmd.get("acao"), []
    try:
        if acao == "volume":
            r = _volume(cmd, ctl)
            efeitos.append("bip_volume")
        elif acao == "timer_criar":
            x = timers.criar(cmd.get("duracao_s"), cmd.get("nome"), agora, music=cmd.get("music"))
            r = {"timer": _timer_publico(x, agora), "ativos": len(timers.consultar(agora))}
        elif acao == "timer_consultar":
            r = {"timers": [_timer_publico(x, agora) for x in timers.consultar(agora, cmd.get("nome"), cmd.get("timer_id"))]}
        elif acao == "timer_cancelar":
            try:
                feitos = timers.cancelar(nome=cmd.get("nome"), todos=bool(cmd.get("todos")), id=cmd.get("timer_id"))
            except ValueError as e:
                if not (cmd.get("todos") or cmd.get("timer_id")):
                    # ambíguo (sem alvo, ou nome repetido): devolve os candidatos, para ela decidir pelo id ou perguntar
                    # com opções reais
                    return {"id": cmd.get("id"), "ok": False, "erro": str(e),
                            "timers": [_timer_publico(x, agora) for x in timers.consultar(agora, cmd.get("nome") or None)]}, []
                raise
            if cmd.get("timer_id") and not feitos and not cmd.get("todos"):
                raise ValueError("nenhum timer ativo com esse id")
            if cmd.get("nome") and not feitos and not (cmd.get("todos") or cmd.get("timer_id")):
                # revisão final 15/10: nome sem par voltava ok com a lista vazia e ela dizia que cancelou; os ativos vão
                # junto para ela ver o nome real ("o arroz") ou o id
                return {"id": cmd.get("id"), "ok": False, "erro": "nenhum timer ativo com esse nome",
                        "timers": [_timer_publico(x, agora) for x in timers.consultar(agora)]}, []
            r = {"cancelados": [_timer_publico(x, agora) for x in feitos]}
        elif acao == "vozes_consultar":  # spec 2026-10-15 §4.4
            r = {"vozes": [{"nome": x["nome"], "desde": x["desde"]} for x in (vozes.listar() if vozes else [])]}
        elif acao == "cadastro_voz":
            if vozes is None:
                raise ValueError("o programa de aprendizagem de voz não está disponível")
            if vozes.ocupado:
                raise ValueError("já há uma gravação ou um treino de voz em andamento")
            nome, ident = cadastro.nome_valido(cmd.get("nome"))
            r = {"nome": nome, "id_pessoa": ident, "gravacao_s": cadastro.GRAVACAO_PREVISTA_S}
            efeitos.append("cadastro")
        elif acao == "play_phrases":  # spec 2026-11-05: frases pré-gravadas do primeiro boot
            caminhos = _frases(cmd, sons_dir)
            r = {"played": len(caminhos)}
            efeitos.append(("tocar_frases", caminhos))
        elif acao == "ring":
            if cmd.get("state") not in ring.ESTADOS:
                raise ValueError(f"state é um de {list(ring.ESTADOS)}")
            r = {}
            efeitos.append(("anel", cmd["state"]))
        elif acao == "ring_countdown":  # 06/11: confirmação do reset de fábrica; seconds 0 cancela
            seg, atraso = cmd.get("seconds", 0), cmd.get("delay_s", 0)
            for v in (seg, atraso):
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 60:
                    raise ValueError("seconds e delay_s entre 0 e 60")
            r = {}
            efeitos.append(("contagem", (atraso, seg)))
        elif acao == "wake_word_test":
            t = cmd.get("timeout_s", 30)
            if isinstance(t, bool) or not isinstance(t, (int, float)) or not 5 <= t <= 120:
                raise ValueError("timeout_s entre 5 e 120")
            if estado_maestro is not None and estado_maestro != "parada":
                raise ValueError("o teste da palavra só começa com o satélite parado")
            r = {}
            efeitos.append(("teste_palavra", t))
        elif acao == "open_presentation":
            fatos = cmd.get("facts")
            if not isinstance(fatos, dict):
                raise ValueError("facts é um objeto")
            if estado_maestro is not None and estado_maestro != "parada":
                raise ValueError("a apresentação só abre com o satélite parado")
            r = {}
            efeitos.append(("apresentacao", fatos))
        elif acao == "set_area":  # chega do bot, na conversa de apresentação
            area = cmd.get("area_id")
            if not isinstance(area, str) or not _ID_AREA.match(area):
                raise ValueError("area_id inválido")
            r = {}
            efeitos.append(("onboarding", {"resultado": "area_selected", "area_id": area}))
        else:
            raise ValueError(f"ação desconhecida: {acao}")
    except ValueError as e:
        return {"id": cmd.get("id"), "ok": False, "erro": str(e)}, []
    return dict({"id": cmd.get("id"), "ok": True}, **r), efeitos
