"""Identificador e configuração do satélite (spec §2.1). Um arquivo só, nada fixo no código."""
import datetime, json, os, secrets, tempfile

PADROES = {
    "espera_inicial_s": 4, "continuacao_s": 5, "cerebro_fim_s": 8, "espera_resposta_s": 8, "limiar_palavra": 0.5,
    "limiar_voz_palavra": 0.6,  # a palavra só vale com voz humana junto (VAD); 0 desliga
    # 03/11/2026: quadros de 80 ms em que a porta de voz procura voz (máximo do VAD). 6 = 0,48 s, o de sempre; a nota
    # do modelo sobe no fim da palavra, quando a voz já passou (ensaio vad_gate_trial.py: 10 = 0,8 s recupera "Friday"
    # com música)
    "janela_voz_palavra": 6,
    # volume = posição do Jabra (0–11; None = usar o que o aparelho tem); volume_alertas = escala falada 1–10
    # (spec 2026-10-12 §1 e §2)
    "volume": None, "volume_alertas": 6, "volume_sons": -12, "nao_perturbe": False,
    "sons": {"inicio": "inicio_a.wav", "fim": "fim_a.wav", "erro": "erro_a.wav",
             "volume": "volume_a.wav", "alarme": "alarme_a.wav", "lembrete": "lembrete_a.wav",
             "nao_perturbe_ligou": "nao_perturbe_ligou.wav", "nao_perturbe_desligou": "nao_perturbe_desligou.wav",
             "aprendizagem_intro": "aprendizagem_intro.wav", "aprendizagem_mudar_lugar": "aprendizagem_mudar_lugar.wav", "aprendizagem_fala_livre": "aprendizagem_fala_livre.wav", "aprendizagem_fim": "aprendizagem_fim.wav", "aprendizagem_cancelada": "aprendizagem_cancelada.wav"},
    "endereco_friday": None, "pre_gravacao_s": 0.4,
    "porta_local": 8700,
    "comodo": None,  # cômodo onde o satélite fica (vai para a Friday em cada conversa); None = não informado
    "gravar_ativacoes": False,  # guarda ~3,5 s em volta de cada "Friday" detectada (análise de falsos)
    "verificador": None,  # spec 2026-10-15 D13: caminho do verificador pessoal; None = detector de hoje
    "limiar_verificador": 0.3,  # nota base a partir da qual o verificador decide
    # spec 2026-10-20: razão 2-6 kHz / 100-600 Hz (dB) a partir da qual a palavra é sussurro; None desliga.
    # 5,5 = calibração de 20/10 (dados_sussurro/calibracao_20261020.md)
    "whisper_min_ratio_db": 5.5,
    # spec 2026-10-21 §6.2/§11: ducking da música (% do controle `Music` enquanto se fala; 0–25) e quanto
    # esperar depois do fim da conversa/alerta para a música voltar a 100%
    "music_duck_percent": 15, "music_restore_s": 1.0,
    # spec 2026-10-21 §6.2: webhook do HA que recebe cada mudança do volume normal (espelho usado pelo Music
    # Assistant); None = sem ponte. Fica fora das _REGRAS: a URL é segredo, não muda pela porta.
    "volume_webhook_url": None,
    # spec 2026-10-29: agente local que recebe eventos (ativação, config, conversa); None desliga
    "agente_url": "http://127.0.0.1:8701",
    # spec 2026-10-29 (integração HA): mudo = corta a captura do Jabra por software (padrão: microfone aberto);
    # sons_turno = toca os sons de início e fim de turno (padrão: tocam, como sempre)
    "mudo": False, "sons_turno": True,
    # spec 2026-11-04: portão de eco, dBFS; None = desligado (o áudio passa inalterado). Ligar só depois de medir.
    "eco_portao_db": None,
    # 04/11 (teste do dono): fecha o microfone quando a pessoa para de falar e só reabre quando ela responde
    "close_mic_awaiting_reply": False,
    # 10/11: com o cérebro pendente, não fecha o microfone com a pessoa falando (espera a próxima pausa)
    "keep_listening_while_speaking": True,
    # 04/11: continuação no estilo Alexa. Sem continuação com música tocando nem depois de comando da casa; pausa de fim
    # de fala (s) que o DetectorFala e o VAD do Gemini esperam (0,8 = o de sempre)
    "no_follow_up_while_music": True, "no_follow_up_after_home_commands": True, "end_of_speech_pause_s": 0.8,
    "alarm_ramp_s": 0,  # alarme crescente (0 = desligado): segundos para o alarme subir ao volume de alertas
    "speech_max_s": 15,  # R13: teto de fala contínua (s) antes de a conversa fechar por `fala_longa`
    # spec 2026-10-24 (wakeword-retrain): palavras configuráveis. `modo_friday` e o `modo` de cada extra:
    # ativa (acorda) | sombra (só registra) | desligada (carregada, mas não ouvida). Config antiga = como hoje.
    "modo_friday": "ativa", "palavras_extras": [],
    # 02/11/2026: gravação contínua do ambiente até "AAAA-MM-DDTHH:MM" (hora local, até 24 h à frente); None = desligada
    "gravar_ambiente_ate": None,
    # 09/11/2026: janela diária "HH:MM-HH:MM" (hora local) guardada aqui; grava todo dia sem depender do HA nem da rede
    "gravar_ambiente_janela": None,
}
MODOS_PALAVRA = ("ativa", "sombra", "desligada")
# chave -> (tipos aceitos, mínimo, máximo); a senha nunca é alterável pela porta
_REGRAS = {
    "espera_inicial_s": ((int, float), 1, 30), "continuacao_s": ((int, float), 0, 60),
    "cerebro_fim_s": ((int, float), 0, 60),
    "limiar_palavra": ((int, float), 0.05, 0.99), "limiar_voz_palavra": ((int, float), 0, 0.99),
    "janela_voz_palavra": ((int,), 1, 25),
    "volume": ((int,), 0, 11), "volume_alertas": ((int,), 1, 10),
    "volume_sons": ((int, float), -40, 0), "nao_perturbe": ((bool,), None, None),
    "pre_gravacao_s": ((int, float), 0, 2), "gravar_ativacoes": ((bool,), None, None),
    "comodo": ((str,), 1, 40),  # texto: mínimo e máximo valem para o tamanho
    "espera_resposta_s": ((int, float), 2, 30),
    "verificador": ((str, type(None)), 1, 200), "limiar_verificador": ((int, float), 0.05, 0.99),
    "whisper_min_ratio_db": ((int, float, type(None)), -10, 40),
    "music_duck_percent": ((int,), 0, 25), "music_restore_s": ((int, float), 0, 10),
    "agente_url": ((str, type(None)), 1, 200),
    "mudo": ((bool,), None, None), "sons_turno": ((bool,), None, None),
    "speech_max_s": ((int, float), 5, 120), "alarm_ramp_s": ((int, float), 0, 120),
    "eco_portao_db": ((int, float, type(None)), -90, 0),
    "close_mic_awaiting_reply": ((bool,), None, None), "keep_listening_while_speaking": ((bool,), None, None),
    "no_follow_up_while_music": ((bool,), None, None), "no_follow_up_after_home_commands": ((bool,), None, None),
    "end_of_speech_pause_s": ((int, float), 0.5, 2.0),
}


SONS_DE_TURNO = ("inicio", "fim")


def som_permitido(cfg: dict, nome: str) -> bool:
    """`sons_turno` falso pula só os sons de início e fim de turno; alarme, volume, erro etc. seguem tocando."""
    return nome not in SONS_DE_TURNO or cfg.get("sons_turno", True) is not False


def validar_palavras_extras(lista) -> list:
    """spec 2026-10-24: cada extra é {nome, arquivo, limiar, modo[, paciencia, verificador]}; nome = arquivo sem .onnx, único e != "friday".
    Não checa se o arquivo existe: isso é da rota /wakewords (a validação roda também sem arquivo)."""
    def erro(motivo):
        return ValueError(f"palavras_extras: {motivo}")
    if not isinstance(lista, list):
        raise erro("precisa ser uma lista")
    saida, vistos = [], set()
    for e in lista:
        if not isinstance(e, dict):
            raise erro("cada item precisa ser um objeto")
        faltam = [k for k in ("nome", "arquivo", "limiar", "modo") if k not in e]
        if faltam:
            raise erro(f"campo faltando: {', '.join(faltam)}")
        nome, arquivo, limiar, modo = e["nome"], e["arquivo"], e["limiar"], e["modo"]
        if not isinstance(nome, str) or not isinstance(arquivo, str) or not nome or not arquivo:
            raise erro("nome e arquivo precisam ser texto")
        if nome != os.path.splitext(os.path.basename(arquivo))[0]:
            raise erro(f"nome '{nome}' difere do arquivo")
        if nome == "friday":
            raise erro("'friday' é reservado")
        if nome in vistos:
            raise erro(f"nome duplicado: {nome}")
        vistos.add(nome)
        if modo not in MODOS_PALAVRA:
            raise erro(f"modo é um de {MODOS_PALAVRA}")
        if isinstance(limiar, bool) or not isinstance(limiar, (int, float)) or not (0.05 <= limiar <= 0.99):
            raise erro("limiar fora de 0.05..0.99")
        item = {"nome": nome, "arquivo": arquivo, "limiar": float(limiar), "modo": modo}
        if "paciencia" in e:  # 02/11: quadros seguidos acima do limiar para disparar (opcional; sem ele, 1)
            p = e["paciencia"]
            if isinstance(p, bool) or not isinstance(p, int) or not (1 <= p <= 5):
                raise erro("paciencia é um inteiro de 1 a 5")
            item["paciencia"] = p
        if "verificador" in e:  # 02/11: verificador de voz próprio da extra (caminho do .pkl; opcional)
            v = e["verificador"]
            if not isinstance(v, str) or not (1 <= len(v) <= 200):
                raise erro("verificador é o caminho do .pkl (texto de 1 a 200)")
            item["verificador"] = v
        saida.append(item)
    return saida


def ler_ou_criar_id(caminho: str) -> str:
    if os.path.exists(caminho):
        with open(caminho) as f:
            return f.read().strip()
    sid = "sat-" + secrets.token_hex(3)
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    with open(caminho, "w") as f:
        f.write(sid + "\n")
    return sid


def gravar(caminho: str, cfg: dict):
    pasta = os.path.dirname(caminho) or "."
    fd, tmp = tempfile.mkstemp(dir=pasta, prefix=".config-")
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, caminho)


CASA = "/etc/friday-satelite/house.json"  # valores da casa gravados na imagem (endereço da VM); nunca no repo


def _casa(caminho: str) -> dict:
    try:
        with open(caminho) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def promover_versao(pasta: str) -> None:
    """install.sh grava FRIDAY_VERSION.pending; só ao arrancar o código novo está carregado, então só aqui ele vira
    FRIDAY_VERSION (que o agente informa à integração). Um reinício adiado deixa a versão antiga visível até lá."""
    pend = os.path.join(pasta, "FRIDAY_VERSION.pending")
    if os.path.exists(pend):
        os.replace(pend, os.path.join(pasta, "FRIDAY_VERSION"))


class ConfiguracaoIncompleta(RuntimeError):
    """Falta um valor da casa sem o qual o satélite não fala com a F.R.I.D.A.Y."""


def exigir_endereco(cfg: dict, casa: str = CASA) -> None:
    """Sem `endereco_friday` o satélite liga e nunca conecta; parar já, dizendo qual chave e qual arquivo."""
    if not cfg.get("endereco_friday"):
        raise ConfiguracaoIncompleta(
            f"endereco_friday ausente: grave-o em {casa} (a imagem o escreve no build) ou no config.json")


def carregar(caminho: str, casa: str = CASA) -> dict:
    cfg = dict(PADROES)
    cfg.update(_casa(casa))
    if os.path.exists(caminho):
        with open(caminho) as f:
            gravado = json.load(f)
        cfg.update(gravado)
        # sons: mescla, senão um config.json antigo (só inicio/fim/erro) apagaria os sons novos
        cfg["sons"] = dict(PADROES["sons"], **(gravado.get("sons") or {}))
    if not cfg.get("senha_porta_local"):
        cfg["senha_porta_local"] = secrets.token_urlsafe(32)
        gravar(caminho, cfg)
    elif not os.path.exists(caminho):
        gravar(caminho, cfg)
    return cfg


def dados_conexao(sid: str, cfg: dict, extras: dict = None) -> dict:
    """request_data da oferta WebRTC: origem, id e, se configurado, o cômodo. `extras`: {"entrega": id} ou
    {"timer": {...}} quando a própria Pi abre a conversa (spec 2026-10-12 §2 e §3)."""
    dados = {"origem": "satelite", "satelite_id": sid}
    if extras:
        dados.update(extras)
    if cfg.get("comodo"):
        dados["comodo"] = cfg["comodo"]
    if cfg.get("nao_perturbe"):
        dados["nao_perturbe"] = True  # a Friday sabe e não entrega resposta atrasada (D11)
    pausa = cfg.get("end_of_speech_pause_s", 0.8)
    if pausa != 0.8:  # 04/11: o bot ajusta a pausa de fim de fala do Gemini; em 0,8 nada muda
        dados["end_of_speech_pause_s"] = pausa
    return dados


def validar_mudancas(atual: dict, mudancas: dict) -> dict:
    if not isinstance(mudancas, dict):
        raise ValueError("corpo precisa ser um objeto")
    novo = dict(atual)
    for k, v in mudancas.items():
        if k == "palavras_extras":  # spec 2026-10-24
            novo[k] = validar_palavras_extras(v)
            continue
        if k == "gravar_ambiente_ate":  # 02/11 (ambiente.py)
            import ambiente
            novo[k] = ambiente.interpretar_ate(v, datetime.datetime.now())
            continue
        if k == "gravar_ambiente_janela":  # 09/11 (ambiente.py)
            import ambiente
            novo[k] = ambiente.interpretar_janela(v)
            continue
        if k == "modo_friday":
            if v not in MODOS_PALAVRA or not isinstance(v, str):
                raise ValueError(f"modo_friday: um de {MODOS_PALAVRA}")
            novo[k] = v
            continue
        if k == "speech_max_s" and v == 0 and not isinstance(v, bool):  # 10/11: 0 desliga o teto (R13)
            novo[k] = 0
            continue
        if k not in _REGRAS:
            raise ValueError(f"chave não alterável: {k}")
        tipos, minimo, maximo = _REGRAS[k]
        if v is None and type(None) in tipos:  # D13: "verificador": null desliga
            novo[k] = None
            continue
        if isinstance(v, bool) and bool not in tipos:
            raise ValueError(f"{k}: tipo inválido")
        if not isinstance(v, tipos):
            raise ValueError(f"{k}: tipo inválido")
        medida = len(v.strip()) if isinstance(v, str) else v
        if minimo is not None and not (minimo <= medida <= maximo):
            raise ValueError(f"{k}: fora de {minimo}..{maximo}")
        novo[k] = v
    return novo
