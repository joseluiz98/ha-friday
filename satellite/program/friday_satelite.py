"""friday-satelite: liga Jabra, palavra, maestro e conexão (spec 2026-10-05). Serviço systemd na Pi.
Registro: uma linha JSON por abertura no journald (sem áudio, nunca)."""
import asyncio, collections, datetime, json, os, subprocess, sys, tempfile, threading, time, wave
import aiohttp
import numpy as np
from openwakeword.model import Model
from openwakeword.vad import VAD
import ambiente
import cadastro
import volume_inicial
from ducking import MusicDucker, SENDSPIN_STATUS, sendspin_playing, should_duck
import comandos
import configuracao as cfgmod
from conexao import ConexaoFriday
from detectores import (DetectorBotaoTelefone, DetectorFala, DetectorMudo, DetectorPalavra, EpisodioVerificador, RejeicaoEpisodio,
                        MedidorEco, PicoDaFala, escolher_acao, portao_eco, trecho_do_episodio)
from hardware import AltoFalante, Anel, Botao, Captura, ControleMudo, Mixer, MusicMixer, TeclasVolume
from maestro import Ajustes, Maestro
import palavra_modelo
from ativacao_detalhes import payload as payload_ativacao
import net_recovery
import porta_local
import registro_palavra
import ring
from emissor import Emissor
import timers as tm
from whisper import noise_floor_rms, whisper_ratio_db
import volume as vol

CFG = "/etc/friday-satelite/config.json"
ID = "/etc/friday-satelite/id"
SONS = "/home/jose/satelite/sons"
TIMERS = "/home/jose/satelite/estado/timers.json"  # spec 2026-10-12 §2: sobrevive a reinício
MODELO_VERIFICADOR = "/home/jose/modelos/friday_verificador.pkl"  # spec 2026-10-15 D13
# D16: sem os WAVs das falas da aprendizagem, a Pi usa os sons de sempre
SONS_RESERVA = {"intro": "inicio", "mudar_lugar": "inicio", "fala_livre": "inicio", "fim": "fim", "cancelada": "erro"}


def para_apos_de(cfg: dict) -> int:
    """Pedaços de 80 ms sem fala que fecham a fala (pausa de fim de fala ajustável); lido a cada pedaço, a quente."""
    return max(1, round(cfg.get("end_of_speech_pause_s", 0.8) / 0.08))


class Satelite:
    def __init__(self, loop):
        self.loop = loop
        self.sendspin_timeout_s = 2.0  # consulta de música (04/11); injetável nos testes
        self.sid = cfgmod.ler_ou_criar_id(ID)
        self.cfg = cfgmod.carregar(CFG)
        self.medidor_eco = MedidorEco()  # spec 2026-11-04: nível do microfone enquanto ela fala (journal em ela_parou)
        self.emissor = Emissor(self.cfg.get("agente_url"), self.cfg.get("senha_porta_local", ""))  # spec 2026-10-29
        self.maestro = Maestro(self._ajustes(), mudo_inicial=None)  # revisão C1: mudo desconhecido até a captura decidir
        self.det_botao = DetectorBotaoTelefone()
        self.alto = AltoFalante()
        # spec 2026-11-05 §11: o anel é uma camada de estados com dois drivers (Jabra, XVF3800); nunca derruba o programa
        self.mixer = Mixer()
        self.anel = ring.make_ring(lambda: Anel(detector=self.det_botao), posicao_fn=self.mixer.escrever)
        self._rede_checada = None  # spec 2026-11-08: instante (monotonic) da última leitura do estado do netwatch
        self.contagem = ring.Contagem()
        self.conexao = None
        self._modelo_palavra = None  # spec 2026-10-24: o Model atual (a captura o (re)cria); a porta lê os nomes
        self.microfone_aberto = False
        self.conversa_sussurrada = False
        self.fala_em_andamento = False
        self.piso_conversa = 0.0  # piso de ruído do trecho da palavra (spec 2026-10-20), usado na conversa sussurrada
        self.recentes = collections.deque(maxlen=25)  # ~2 s de pedaços de 80 ms
        self.contexto = collections.deque(maxlen=375)  # 20/10: ~30 s antes de cada ativação (tentativas perdidas)
        self.ambiente = ambiente.GravadorAmbiente()  # 02/11: gravação contínua para o retreino (gravar_ambiente_ate)
        # spec 2026-10-12: volume (o gravado, ou o que o aparelho tem) e timers
        self.controle_mudo = ControleMudo()
        fabrica = self.cfg["volume"] is None  # 06/11: Mark novo começa em 65%, não no que o aparelho guardou
        if fabrica:
            print(json.dumps({"volume_inicial": volume_inicial.aplicar()}), flush=True)
        inicial = self.cfg["volume"] if not fabrica else self._ler_mixer()
        self._volume_aviso = None  # spec 2026-10-21 §6.2: posição mais nova a mandar ao espelho do HA
        self.volume = vol.ControleVolume(self.mixer.escrever, self._gravar_volume, inicial, self.cfg["volume_alertas"],
                                         avisar=self._avisar_volume)
        if fabrica and inicial is not None:
            self._gravar_volume(inicial)
        self.timers = tm.Timers(self._gravar_timers)
        # spec 2026-10-21 §6.2: ducking da música. O `softvol` Music só nasce no primeiro uso do PCM (prime);
        # start() põe 100%: depois de um crash a música não fica presa em 15%.
        self.music_mixer = MusicMixer()
        try:
            self.music_mixer.prime()
        except Exception as e:
            print(json.dumps({"music_prime_error": f"{type(e).__name__}: {e}"}), flush=True)
        self.ducker = MusicDucker(self.music_mixer.write, self.cfg["music_duck_percent"], self.cfg["music_restore_s"])
        self.ducker.start(time.monotonic())
        self._alarme = None  # tarefa que repete o som do alarme
        # spec 2026-10-15 §4.4: programa de aprendizagem de voz
        self.vozes = cadastro.Vozes()
        self._agendado = None  # {"nome", "id"} pedido na conversa
        self._cad = None  # gravação em andamento: {"nome", "id", "pasta", "gravador", "roteiro"}

    def _ler_mixer(self):
        try:
            return self.mixer.ler()
        except Exception as e:
            print(json.dumps({"volume_ilegivel": f"{type(e).__name__}: {e}"}), flush=True)
            return None

    def _gravar_volume(self, posicao):
        self.cfg = cfgmod.validar_mudancas(self.cfg, {"volume": posicao})
        cfgmod.gravar(CFG, self.cfg)

    def _gravar_timers(self, lista):
        os.makedirs(os.path.dirname(TIMERS), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(TIMERS), prefix=".timers-")
        with os.fdopen(fd, "w") as f:
            json.dump(lista, f, ensure_ascii=False)
        os.replace(tmp, TIMERS)

    def carregar_timers(self):
        try:
            with open(TIMERS) as f:
                dados = json.load(f)
        except (OSError, ValueError):
            dados = []
        recentes, velhos = self.timers.carregar(dados, time.time())
        for t in velhos:
            print(json.dumps({"timer": {"id": t["id"], "timer": t["nome"], "como_parou": "vencido_com_a_pi_desligada"}}),
                  flush=True)
        for t in recentes:
            self.evento("timer_venceu", t)

    def estado_publico(self):
        agora = time.time()
        return dict(self.maestro.publico(), volume=self.volume.atual,
                    palavras=self._modos_palavras(),  # spec 2026-10-24: {nome: modo}, para o futuro sensor do HA
                    timers=[{"nome": t["nome"], "duracao_s": t["duracao_s"], "falta_s": t["falta_s"]}
                            for t in self.timers.consultar(agora)])

    def _modos_palavras(self):
        cfg = self.cfg
        return dict([("friday", cfg.get("modo_friday", "ativa"))] +
                    [(e["nome"], e["modo"]) for e in cfg.get("palavras_extras", [])])

    def palavras_carregadas(self):
        m = self._modelo_palavra
        return palavra_modelo.carregadas(m) if m is not None else set()

    def _detectores_extras(self, palavra):
        """Um DetectorPalavra por extra que o modelo carregou (sem sussurro; spec 2026-10-24)."""
        carregadas = palavra_modelo.carregadas(palavra)
        return {e["nome"]: DetectorPalavra(e["limiar"], self.cfg["limiar_voz_palavra"], paciencia=e.get("paciencia", 1))
                for e in self.cfg.get("palavras_extras", []) if e["nome"] in carregadas}

    def _ajustes(self):
        return Ajustes(espera_inicial_s=self.cfg["espera_inicial_s"], continuacao_s=self.cfg["continuacao_s"],
                        cerebro_fim_s=self.cfg["cerebro_fim_s"], espera_resposta_s=self.cfg["espera_resposta_s"], speech_max_s=self.cfg["speech_max_s"],
                       close_mic_awaiting_reply=bool(self.cfg.get("close_mic_awaiting_reply", False)),
                       no_follow_up_while_music=self.cfg.get("no_follow_up_while_music", True) is not False,
                       no_follow_up_after_home_commands=self.cfg.get("no_follow_up_after_home_commands", True) is not False)

    async def _musica_agora(self) -> bool:
        """Há música tocando no Sendspin? Qualquer erro, saída estranha ou demora = não (nunca derruba o programa)."""
        try:
            p = await asyncio.create_subprocess_exec(*SENDSPIN_STATUS, stdout=asyncio.subprocess.PIPE,
                                                     stderr=asyncio.subprocess.DEVNULL)
            try:
                saida, _ = await asyncio.wait_for(p.communicate(), self.sendspin_timeout_s)
            except asyncio.TimeoutError:
                try:
                    p.kill()
                except Exception:
                    pass
                return False
            return sendspin_playing(saida.decode("utf-8", "replace"))
        except Exception:
            return False

    async def _marcar_musica(self):
        self.evento("musica", await self._musica_agora())

    # ---- eventos (sempre no laço asyncio)
    def evento(self, nome, valor=None):
        if nome != "tique":  # 06/10: rastro no journal para entender fechamentos (sem áudio)
            print(json.dumps({"evento": nome, "valor": valor, "estado": self.maestro.estado}), flush=True)
        for tipo, arg in self.maestro.evento(nome, time.monotonic(), valor):
            self._agir(tipo, arg)
        if nome == "ela_comecou" and self.cfg.get("no_follow_up_while_music", True):
            asyncio.ensure_future(self._marcar_musica())  # 04/11: assíncrono, fora do laço de captura
        if nome == "ela_parou":
            resumo = self.medidor_eco.resumo()
            if resumo:
                print(json.dumps({"eco_rms_db": resumo}), flush=True)
        if nome == "mudo" and valor is not None:  # 31/10: botão de mudo do Jabra chega ao HA na hora
            self.emissor.emitir("config", {"mudancas": {"mudo_botao": bool(valor)}, "origem": "botao"})

    @staticmethod
    async def _fechar_depois(conexao, atraso_s):
        try:
            await asyncio.sleep(atraso_s)
            await conexao.fechar()
        except Exception:
            pass  # conexão já descartada: nada a fazer

    def _som(self, nome):
        return f"{SONS}/{self.cfg['sons'][nome]}"

    def _agir(self, tipo, arg):
        if tipo == "som":
            if cfgmod.som_permitido(self.cfg, arg):  # spec 2026-10-29: sons_turno falso pula inicio/fim
                self.alto.tocar_wav(self._som(arg))
        elif tipo == "luz":
            self.anel.show(ring.ESTADO_DO_MAESTRO[arg])
        elif tipo == "desfazer_mudo":  # spec 2026-11-08: a rede voltou e o mudo veio da luz de erro; não ficar mudo
            if isinstance(self.anel, ring.Xvf3800Ring):  # sem caminho de software conhecido no código: só registra
                print(json.dumps({"undo_mute": "unsupported_xvf3800"}), flush=True)
            else:  # Jabra: escrever a luz religa o microfone mudo (medido em 06/10)
                self.anel.show(ring.ESTADO_DO_MAESTRO[self.maestro._luz_do_estado()])
                print(json.dumps({"undo_mute": "jabra"}), flush=True)
        elif tipo == "conectar":  # arg: None (palavra/botão) ou {"timer": ...}/{"entrega": id} (spec 2026-10-12)
            self.conexao = ConexaoFriday(self.cfg["endereco_friday"], cfgmod.dados_conexao(self.sid, self.cfg, arg),
                                         self._de_fora, self.alto.tocar_pcm48)
            self.conversa_sussurrada = bool(arg and arg.get("whisper"))
            self.fala_em_andamento = self.conversa_sussurrada  # a captura põe o DetectorFala "falando"  # D7: a fala também se mede pela razão
            if arg is None or arg.get("whisper") or arg.get("wake_word"):  # pré-gravação só quando alguém falou; na conversa aberta pela Pi ela teria o alarme
                n = int(self.cfg["pre_gravacao_s"] / 0.08) + 4  # +4 pedaços: a detecção chega ~0,3 s depois da palavra
                for pcm in list(self.recentes)[-n:]:
                    self.conexao.enviar(pcm)
            self.microfone_aberto = True
            self.emissor.emitir("conversa", {"estado": "aberta"})
            asyncio.ensure_future(self.conexao.abrir())
        elif tipo == "cortar":
            if self.conexao:
                self.conexao.enviar_silencio(1.5)
            self.microfone_aberto = False
        elif tipo == "microfone":
            self.microfone_aberto = bool(arg)
        elif tipo == "cancelar_conversa":
            # Botão vermelho (spec 2026-11-04): para o som na hora, avisa o bot (cancela ferramentas e cérebro) e
            # fecha a conexão depois de 0,3 s, para a mensagem sair. O `desconectar` seguinte não encontra conexão.
            self.alto.parar_ja()
            velha, self.conexao = self.conexao, None
            if velha:
                velha.silenciar()
                velha.enviar_mensagem("cancelar", {})
                asyncio.ensure_future(self._fechar_depois(velha, 0.3))
                self.emissor.emitir("conversa", {"estado": "fechada"})
            self.microfone_aberto = False
        elif tipo == "desconectar":
            self.microfone_aberto = False
            self.conversa_sussurrada = False
            if self.conexao:
                asyncio.ensure_future(self.conexao.fechar()); self.conexao = None
                self.emissor.emitir("conversa", {"estado": "fechada"})
        elif tipo == "registrar":
            print(json.dumps({"abertura": arg, "satelite": self.sid}), flush=True)
        elif tipo == "registrar_timer":
            print(json.dumps({"timer": arg, "satelite": self.sid}), flush=True)
        elif tipo == "registrar_espera":  # D4: quanto ela levou para começar a falar
            print(json.dumps({"espera": arg, "satelite": self.sid}), flush=True)
        elif tipo == "cadastro":
            self._acao_cadastro(arg)
        elif tipo == "alarme":
            self._parar_alarme()
            if arg == "tocar":
                self._alarme = asyncio.ensure_future(self._repetir_alarme())
        elif tipo == "volume_alerta":
            if arg:
                # alarme crescente (spec 2026-10-21 §11): lido da config atual a cada alarme (vale a quente);
                # só a campainha sobe no hardware — o alarme com música sobe o `Music` (music_ramp)
                sobe = self.maestro.estado == "alarme" and not self.maestro.musical_alarm
                self.volume.entrar_alerta(self.cfg.get("alarm_ramp_s", 0) if sobe else 0, time.monotonic())
            else:
                self.volume.sair_alerta()
        elif tipo == "request_music":  # R11: campainha tocando, a música vem da VM em até music_wait_s
            asyncio.ensure_future(self._pedir_musica(arg))
        elif tipo == "music_ramp":  # a música do alarme entrou: sobe de 10% a 100% se houver alarme crescente
            if self.cfg.get("alarm_ramp_s", 0) > 0:
                self.ducker.ramp_to(10, self.cfg["alarm_ramp_s"], time.monotonic())
        elif tipo == "aviso_nao_atendido":
            asyncio.ensure_future(self._avisar_nao_atendido(arg))
        elif tipo == "avisar_bot":
            if self.conexao:
                self.conexao.enviar_mensagem(arg, {})
        elif tipo == "frase":  # spec 2026-11-05: frase pré-gravada do primeiro boot
            self._tocar_frases([f"{SONS}/onboarding/{arg}.wav"])
        elif tipo == "onboarding":
            self.emissor.emitir("onboarding", arg)

    def _tocar_frases(self, caminhos):
        for c in caminhos:
            if os.path.isfile(c):
                self.alto.tocar_wav(c)
            else:
                print(json.dumps({"frase_ausente": os.path.basename(c)}), flush=True)

    def _efeitos_provisionamento(self, efeitos, resposta):
        """Efeitos em tupla de `comandos.executar` (play_phrases, ring, wake_word_test, open_presentation, set_area).
        Devolve a resposta, corrigida se o maestro recusou (ex.: mudo)."""
        for ef in efeitos:
            if not isinstance(ef, tuple):
                continue
            nome, arg = ef
            if nome == "tocar_frases":
                self._tocar_frases(arg)
            elif nome == "anel":
                self.anel.show(arg)
            elif nome == "contagem":
                self._contagem(*arg)
            elif nome == "teste_palavra":
                self.evento("teste_palavra", arg)
            elif nome == "apresentacao":
                self.evento("abrir_apresentacao", arg)
                if self.maestro.estado != "conectando":
                    resposta = dict(resposta, ok=False, erro="a conversa de apresentação não abriu (mudo ou ocupado)")
            elif nome == "onboarding":
                self.emissor.emitir("onboarding", arg)
        return resposta

    def comando_porta(self, cmd):
        """POST /command da porta local (agente do satélite): mesmos comandos da conversa, sem o id da conversa."""
        cmd = dict(cmd, id=cmd.get("id"))
        resposta, efeitos = comandos.executar(cmd, self.volume, self.timers, time.time(), self.vozes,
                                              sons_dir=SONS, estado_maestro=self.maestro.estado)
        if "bip_volume" in efeitos:
            self.alto.tocar_wav(self._som("volume"))
        resposta = self._efeitos_provisionamento(efeitos, resposta)
        print(json.dumps({"comando_porta": cmd.get("acao"), "ok": resposta.get("ok")}), flush=True)
        return resposta

    def _parar_alarme(self):
        if self._alarme:
            self._alarme.cancel()
            self._alarme = None
            self.alto.limpar()

    async def _repetir_alarme(self):
        caminho = self._som("alarme")
        with wave.open(caminho) as w:
            volta = w.getnframes() / w.getframerate()
        while True:
            self.alto.tocar_wav(caminho)
            await asyncio.sleep(volta)

    def _avisar_volume(self, posicao):
        """Ponte de volume (spec 2026-10-21 §6.2): manda a posição ao espelho no HA (`volume_webhook_url` da config;
        sem ela, nada). Pode vir de qualquer thread; uma rajada de apertos vira um envio só, com a posição final."""
        if not self.cfg.get("volume_webhook_url"):
            return
        pendente, self._volume_aviso = self._volume_aviso is not None, posicao
        if not pendente:
            self.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self._enviar_volume()))

    async def _enviar_volume(self):
        await asyncio.sleep(0.3)  # junta a rajada de apertos do Jabra
        posicao, self._volume_aviso = self._volume_aviso, None
        corpo = {"satellite": self.sid, "position": posicao, "percent": vol.descrever(posicao)["percentual"]}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(self.cfg["volume_webhook_url"], json=corpo,
                                  timeout=aiohttp.ClientTimeout(total=5)) as r:
                    resultado = r.status
        except Exception as e:
            resultado = type(e).__name__
        print(json.dumps({"volume_espelho": corpo["percent"], "resultado": resultado}), flush=True)

    async def _avisar_bot(self, corpo):
        """POST /satellite_alert (Telegram do dono; cadastro_voz também vira entrega falada). Sem o add-on, fica só
        no journal."""
        corpo = dict(corpo, satelite_id=self.sid)
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(f"{self.cfg['endereco_friday']}/satellite_alert", json=corpo, ssl=False,
                                  headers={"Authorization": f"Bearer {self.cfg['senha_porta_local']}"},
                                  timeout=aiohttp.ClientTimeout(total=15)) as r:
                    resultado = r.status
        except Exception as e:
            resultado = type(e).__name__
        print(json.dumps({"aviso_bot": corpo.get("tipo"), "resultado": resultado}), flush=True)

    async def _post_musica(self, query):
        """POST /satellite_media na VM (mesmo padrão do /satellite_alert): resposta {"ok": bool, "title"?}."""
        async with aiohttp.ClientSession() as s:
            async with s.post(f"{self.cfg['endereco_friday']}/satellite_media", json={"query": query}, ssl=False,
                              headers={"Authorization": f"Bearer {self.cfg['senha_porta_local']}"},
                              timeout=aiohttp.ClientTimeout(total=8)) as r:
                return await r.json() if r.status == 200 else {"ok": False, "status": r.status}

    async def _pedir_musica(self, arg):
        """Qualquer coisa que não seja `ok` (erro, prazo, HTTP != 200) = music_failed: o alarme segue como sempre."""
        try:
            ok = bool((await self._post_musica(arg["query"])).get("ok"))
        except Exception as e:
            print(json.dumps({"music_request_error": f"{type(e).__name__}: {e}"}), flush=True)
            ok = False
        self.evento("music_started" if ok else "music_failed", arg["timer_id"])

    async def _avisar_nao_atendido(self, t):
        """Spec 2026-10-12 D14: o add-on manda ao Telegram do dono."""
        await self._avisar_bot({"tipo": "timer_nao_atendido", "nome": t.get("nome"),
                                "duracao_s": t.get("duracao_s"), "venceu_em": t.get("termino")})

    def comando(self, cmd):
        volume_antes = self.volume.normal
        resposta, efeitos = comandos.executar(cmd, self.volume, self.timers, time.time(), self.vozes,
                                              sons_dir=SONS, estado_maestro=self.maestro.estado)
        resposta = self._efeitos_provisionamento(efeitos, resposta)
        if self.volume.normal != volume_antes:
            self.emissor.emitir("config", {"mudancas": {"volume": self.volume.normal}, "origem": "voz"})
        if "bip_volume" in efeitos:
            self.alto.tocar_wav(self._som("volume"))
        if "cadastro" in efeitos:  # §4.4: o maestro só aceita com a conversa aberta
            self._agendado = {"nome": resposta["nome"], "id": resposta["id_pessoa"]}
            self.evento("cadastro_agendado", self._agendado)
            if self.maestro.publico()["cadastro"]:
                self.vozes.ocupado = True
            else:
                resposta = {"id": cmd.get("id"), "ok": False, "erro": "a conversa já tinha terminado; nada foi agendado"}
        print(json.dumps({"comando": cmd.get("acao"), "ok": resposta.get("ok")}), flush=True)
        if self.conexao:
            self.conexao.enviar_mensagem("resposta", resposta)

    def campainha(self, entrega_id):
        resposta, acoes = self.maestro.campainha(entrega_id, time.monotonic())
        print(json.dumps({"campainha": entrega_id, "resposta": resposta, "estado": self.maestro.estado}), flush=True)
        for tipo, arg in acoes:
            self._agir(tipo, arg)
        return resposta

    def apertou_volume(self, delta):
        antes = self.volume.normal
        self.volume.apertou(delta, time.monotonic())
        if self.volume.normal != antes:
            self.emissor.emitir("config", {"mudancas": {"volume": self.volume.normal}, "origem": "botao"})
        print(json.dumps({"botao_volume": delta, "posicao": self.volume.atual}), flush=True)

    def _som_aprendizagem(self, nome):
        if nome == "bipe":
            return self._som("volume")
        caminho = f"{SONS}/{self.cfg['sons'].get('aprendizagem_' + nome, '')}"
        return caminho if os.path.isfile(caminho) else self._som(SONS_RESERVA[nome])

    @staticmethod
    def _duracao_wav(caminho):
        with wave.open(caminho) as w:
            return w.getnframes() / w.getframerate()

    def _acao_cadastro(self, arg):
        if arg == "iniciar":
            a, agora = self._agendado, datetime.datetime.now()
            pasta = self.vozes.pasta_nova(a["id"], agora)
            dur = {k: self._duracao_wav(self._som_aprendizagem(k)) for k in ("intro", "bipe", "mudar_lugar",
                                                                            "fala_livre", "fim")}
            self._cad = {"nome": a["nome"], "id": a["id"], "pasta": pasta, "gravador": cadastro.Gravador(pasta),
                         "roteiro": cadastro.Roteiro(dur, time.monotonic())}
            print(json.dumps({"aprendizagem": "inicio", "id": a["id"]}), flush=True)
            self.emissor.emitir("conversa", {"estado": "aberta"})  # 05/11: para o HA, a gravação continua a conversa
        elif arg in ("cancelar", "interromper"):
            c, self._cad = self._cad, None
            if c:
                c["gravador"].fechar()
                self.vozes.descartar(c["pasta"])
            self.alto.limpar()
            self.vozes.ocupado = False
            print(json.dumps({"aprendizagem": arg, "id": c["id"] if c else None}), flush=True)
            if c:
                self.emissor.emitir("conversa", {"estado": "fechada"})
            if arg == "cancelar":
                self.alto.tocar_wav(self._som_aprendizagem("cancelada"))
            elif c:  # D20: o timer interrompeu; o dono fica sabendo pelo aviso
                asyncio.ensure_future(self._avisar_bot({"tipo": "cadastro_voz", "nome": c["nome"], "aprovado": False,
                                                        "motivo": "a gravação foi interrompida por um timer",
                                                        "numeros": {}}))
        elif arg == "cancelar_agendado":
            self.vozes.ocupado = False
            if self.maestro.estado != "alarme":
                self.alto.tocar_wav(self._som_aprendizagem("cancelada"))
            print(json.dumps({"aprendizagem": "cancelar_agendado"}), flush=True)

    def _tique_cadastro(self):
        c = self._cad
        if not c:
            return
        for acao, v in c["roteiro"].tique(time.monotonic()):
            if acao == "som":
                self.alto.tocar_wav(self._som_aprendizagem(v))
            elif acao == "janela":
                c["gravador"].abrir(v)
            elif acao == "fechar_janela":
                c["gravador"].fechar()
            elif acao == "terminar":
                self._cad = None
                c["gravador"].finalizar()  # 16/10: sessão contínua + marcas (o treino roda o modelo no contínuo)
                self.evento("cadastro_fim")
                self.emissor.emitir("conversa", {"estado": "fechada"})
                threading.Thread(target=self._treinar, args=(c["id"], c["nome"], c["pasta"]), daemon=True).start()

    def _treinar(self, ident, nome, pasta):
        """Processo separado com nice 19: os laços da extração não disputam o GIL com a captura, e uma falta de
        memória mata só o treino (Pi 3, 1 GB)."""
        antes = {x["id"]: self.vozes.aceitas_de(x["id"]) for x in self.vozes.listar() if x["id"] != ident}
        args = json.dumps({"ident": ident, "nome": nome, "pasta": pasta, "limiar_voz": self.cfg["limiar_voz_palavra"],
                           "antes": antes}, ensure_ascii=False)
        try:
            p = subprocess.run(["nice", "-n", "19", sys.executable, os.path.join(os.path.dirname(__file__),
                                "treino_verificador.py"), "--executar", args], capture_output=True, text=True,
                               timeout=3600)
            linhas = [l for l in p.stdout.splitlines() if l.startswith("{")]
            if p.returncode != 0 or not linhas:
                raise RuntimeError(f"código {p.returncode}: {(p.stderr or '').strip()[-200:]}")
            r = json.loads(linhas[-1])
        except Exception as e:
            r = {"aprovado": False, "motivo": f"erro no treino: {type(e).__name__}: {e}", "numeros": {}}
        self.loop.call_soon_threadsafe(self._fim_do_treino, ident, nome, pasta, r)

    def _fim_do_treino(self, ident, nome, pasta, r):
        aprovado = bool(r.get("aprovado"))
        try:
            if aprovado:
                os.replace(r["modelo_tmp"], MODELO_VERIFICADOR)
                self.vozes.aprovar(ident, nome, pasta, {"aceitas": (r.get("numeros") or {}).get("aceitas")},
                                   datetime.datetime.now())
                novo = cfgmod.validar_mudancas(self.cfg, {"verificador": MODELO_VERIFICADOR,
                                                          "limiar_verificador": r["limiar_verificador"],
                                                          "limiar_palavra": r["limiar_palavra"]})
                cfgmod.gravar(CFG, novo)
                self.ao_mudar_config(novo)
            else:
                self.vozes.guardar_reprovado(ident, pasta)  # 16/10: o último reprovado fica para diagnóstico
        except Exception as e:
            aprovado, r = False, dict(r, motivo=f"erro ao guardar: {type(e).__name__}: {e}")
            self.vozes.descartar(pasta)
        self.vozes.ocupado = False
        n = r.get("numeros") or {}
        print(json.dumps({"aprendizagem": "fim", "id": ident, "aprovado": aprovado, "motivo": r.get("motivo"),
                          "numeros": n, "limiares": [r.get("limiar_verificador"), r.get("limiar_palavra")]},
                         ensure_ascii=False), flush=True)
        asyncio.ensure_future(self._avisar_bot({"tipo": "cadastro_voz", "nome": nome, "aprovado": aprovado,
                                                "motivo": r.get("motivo"),
                                                "numeros": {"nova": n.get("nova"),
                                                            "falsas_barradas": n.get("falsas_barradas")}}))

    def _contagem(self, atraso, segundos):
        if segundos > 0:
            self.contagem.iniciar(time.monotonic(), atraso, segundos)
        elif self.contagem.cancelar():
            self._fim_contagem()

    def _fim_contagem(self):
        """O anel e o volume voltam ao que eram (no Jabra a contagem desenha com os LEDs de volume)."""
        self.anel.reapply()
        if self.volume.atual is not None:
            self.mixer.escrever(self.volume.atual)

    def tique(self):
        self.evento("tique")
        agora = time.monotonic()
        if self._rede_checada is None or agora - self._rede_checada >= 5:  # spec 2026-11-08: estado do netwatch, a cada 5 s
            self._rede_checada = agora
            v = net_recovery.read_exhausted()
            if v != self.maestro.rede_esgotada:
                self.evento("rede_esgotada", v)
                print(json.dumps({"net_exhausted": v}), flush=True)
        self._tique_cadastro()
        fr = self.contagem.tique(time.monotonic())
        if fr is not None:
            self.anel.countdown(fr)
            if fr <= 0:
                self._fim_contagem()
        for t in self.timers.vencidos(time.time()):
            self.evento("timer_venceu", t)
            self.emissor.emitir("timer", {"id": t.get("id"), "nome": t.get("nome"), "duracao_s": t.get("duracao_s")})
        self.volume.tique(time.monotonic())

    def duck_tique(self, agora=None):
        """Roda numa thread própria a cada 50 ms (não no tique de 0,2 s, para abaixar em < 300 ms, e para o
        amixer nunca travar o laço asyncio)."""
        agora = time.monotonic() if agora is None else agora
        self.ducker.update(should_duck(self.maestro.estado, self.alto.ultimo_envio, agora), agora)

    def duck_laco(self):
        while True:
            try:
                self.duck_tique()
            except Exception as e:
                print(json.dumps({"music_duck_error": f"{type(e).__name__}: {e}"}), flush=True)
            time.sleep(0.05)

    def _de_fora(self, nome, valor):
        if nome == "comando":  # spec 2026-10-12 §4: não passa pelo maestro
            self.comando(valor)
            return
        if nome == "nao_perturbe":  # pedido por voz: grava na config (sobrevive a reinício, D11)
            self.cfg = cfgmod.validar_mudancas(self.cfg, {"nao_perturbe": bool(valor)})
            cfgmod.gravar(CFG, self.cfg)
            self.emissor.emitir("config", {"mudancas": {"nao_perturbe": bool(valor)}, "origem": "voz"})
        self.evento(nome, valor)

    def ao_mudar_config(self, novo):
        mudancas = {k: v for k, v in novo.items() if k not in ("senha_porta_local", "volume_webhook_url")
                    and self.cfg.get(k) != v}
        mudou_np = bool(novo.get("nao_perturbe")) != self.maestro.nao_perturbe
        mudou_volume = novo.get("volume") is not None and novo.get("volume") != self.volume.normal
        mudou_mudo = bool(novo.get("mudo")) != bool(self.cfg.get("mudo"))
        self.cfg = novo
        if mudou_mudo:
            self._aplicar_mudo()
        self.emissor.url = novo.get("agente_url")
        if mudancas:  # PUT /config pela porta local
            self.emissor.emitir("config", {"mudancas": mudancas, "origem": "porta"})
        self.maestro.a = self._ajustes()
        self.volume.alertas_numero = novo["volume_alertas"]
        self.ducker.duck_percent, self.ducker.restore_s = novo["music_duck_percent"], novo["music_restore_s"]
        if mudou_volume:  # PUT /config (futura integração no HA)
            self.volume.definir(novo["volume"])
        if mudou_np:  # PUT /config pela porta local
            self.evento("nao_perturbe", bool(novo.get("nao_perturbe")))

    def _aplicar_mudo(self, inicial=False):
        """Mudo por software (spec 2026-10-29): falha do amixer nunca derruba o programa.

        `inicial` (arranque): restaura o ÚLTIMO estado gravado (`mudo` na config persistida); com mudo
        desligado não escreve nada, deixando a captura do aparelho como está (comportamento de antes)."""
        if inicial and not self.cfg.get("mudo"):
            return
        try:
            if not self.controle_mudo.aplicar(bool(self.cfg.get("mudo"))):
                print(json.dumps({"mudo_erro": "amixer"}), flush=True)
        except Exception as e:
            print(json.dumps({"mudo_erro": type(e).__name__}), flush=True)

    def _nome_gravacao(self, prefixo=""):
        return f"/home/jose/gravacoes/ativacoes/{prefixo}{datetime.datetime.now():%Y%m%d_%H%M%S}.wav"

    def _gravar_ativacao(self, antes, depois, nome):
        os.makedirs(os.path.dirname(nome), exist_ok=True)
        with wave.open(nome, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes(b"".join(antes + depois))
        print(json.dumps({"ativacao_gravada": nome}), flush=True)

    # ---- threads de hardware
    def captura(self):
        cap = Captura()
        aviso = lambda t: print(t, flush=True)
        chave_modelo = palavra_modelo.chave(self.cfg)
        palavra, com_verificador = palavra_modelo.criar(Model, self.cfg, aviso)
        self._modelo_palavra, dets_extras = palavra, self._detectores_extras(palavra)
        espiao, ep_verif = palavra_modelo.espiar(palavra), EpisodioVerificador()  # 20/10: "Friday" barrado
        rej_f, rej_extras = RejeicaoEpisodio(), {n: RejeicaoEpisodio() for n in dets_extras}  # spec 2026-11-03
        print(json.dumps({"detector_palavra": "com verificador" if com_verificador else "sem verificador",
                          "registra_barradas": espiao is not None,
                          "palavras": sorted(palavra_modelo.carregadas(palavra))}), flush=True)
        vad = VAD()
        dp = DetectorPalavra(self.cfg["limiar_palavra"], self.cfg["limiar_voz_palavra"],
                             limiar_sussurro=self.cfg.get("whisper_min_ratio_db"))
        df, dm = DetectorFala(), DetectorMudo(desconhecido=True)
        reg = registro_palavra.Registro()
        pico, reg_falas = PicoDaFala(), registro_palavra.Registro(registro_palavra.ARQUIVO_FALAS)  # 20/10
        pulou, gravando, vozes, pont_ant = False, None, collections.deque(maxlen=self.cfg.get("janela_voz_palavra", 6)), 0.0
        razao_ep, piso_ep, calculou = None, 0.0, False  # spec 2026-10-20 D3: a razão sai uma vez por episódio
        while True:
            pcm = cap.ler()  # EOFError → sai do processo → systemd religa
            x = np.frombuffer(pcm, dtype=np.int16)
            self.recentes.append(pcm)
            self.contexto.append(pcm)
            try:  # 02/11: nunca derruba a escuta (disco cheio etc.): desliga a gravação e segue
                self.ambiente.alimentar(pcm, datetime.datetime.now(), self.cfg.get("gravar_ambiente_ate"),
                                        self.cfg.get("gravar_ambiente_janela"))  # 09/11: janela diária local
            except OSError as e:
                print(json.dumps({"ambiente_erro": str(e)}), flush=True)
                self.ambiente.fechar()
                self.cfg["gravar_ambiente_ate"] = self.cfg["gravar_ambiente_janela"] = None  # só na memória
            quadro = (None, False, 0.0, 0.0, False)  # inferência pulada: não entra no pico
            extras_pred = {}  # spec 2026-10-24: nota de cada palavra extra não desligada neste quadro
            dp.limiar, dp.limiar_voz = self.cfg["limiar_palavra"], self.cfg["limiar_voz_palavra"]
            dp.limiar_sussurro = self.cfg.get("whisper_min_ratio_db")
            if palavra_modelo.chave(self.cfg) != chave_modelo:  # D13: cadastro aprovado ou PUT /config
                chave_modelo = palavra_modelo.chave(self.cfg)
                palavra, com_verificador = palavra_modelo.criar(Model, self.cfg, aviso)
                self._modelo_palavra, dets_extras = palavra, self._detectores_extras(palavra)
                espiao, ep_verif = palavra_modelo.espiar(palavra), EpisodioVerificador()
                rej_f, rej_extras = RejeicaoEpisodio(), {n: RejeicaoEpisodio() for n in dets_extras}
                print(json.dumps({"detector_palavra": "com verificador" if com_verificador else "sem verificador",
                                  "palavras": sorted(palavra_modelo.carregadas(palavra))}), flush=True)
            cad = self._cad
            if cad:  # §4.4: a gravação da aprendizagem recebe o mesmo áudio da captura
                cad["gravador"].alimentar(pcm)
            voz = float(vad.predict(x, frame_size=640))
            if vozes.maxlen != self.cfg.get("janela_voz_palavra", 6):  # 03/11: muda a quente pela porta
                vozes = collections.deque(vozes, maxlen=self.cfg.get("janela_voz_palavra", 6))
            vozes.append(voz)
            mudo = dm.alimentar(pcm)
            if mudo is not None:
                self.loop.call_soon_threadsafe(self.evento, "mudo", mudo)
            if gravando is not None:
                gravando[1].append(pcm)
                if len(gravando[1]) >= 19:  # ~1,5 s depois da detecção
                    threading.Thread(target=self._gravar_ativacao, args=gravando, daemon=True).start()
                    gravando = None
            # 06/10: com o microfone aberto a palavra é ignorada; pular a inferência libera CPU para a voz dela
            # (a falta dela dava "underrun" e voz robótica). Ao voltar, zera o modelo para não pegar o salto.
            # Durante a aprendizagem a palavra também é ignorada.
            if self.microfone_aberto or cad:
                pulou = True
            else:
                if pulou:
                    palavra.reset(); pulou = False
                pred = palavra.predict(x)
                pont = float(pred["friday"])
                modo_f = self.cfg.get("modo_friday", "ativa")  # spec 2026-10-24: ativa | sombra | desligada
                ja_evento, nomes_f = False, []  # nomes_f: gravações escolhidas neste quadro (spec 2026-11-03)
                if modo_f == "desligada":
                    # a inferência segue (as extras precisam dela); a Friday não é ouvida nem registrada
                    if espiao is not None:
                        espiao.ler()
                else:
                    if pont < 0.2:
                        razao_ep, calculou = None, False
                    elif (pont >= dp.limiar and dp.armado and not calculou and dp.limiar_sussurro is not None):
                        trecho = b"".join(self.recentes)  # ~2 s até a palavra; roda só aqui, uma vez por episódio
                        razao_ep, piso_ep, calculou = whisper_ratio_db(trecho), noise_floor_rms(trecho), True
                    disparou = dp.alimentar(pont, max(vozes), razao_ep)
                    consultado, nota_v = espiao.ler() if espiao is not None else (False, 0.0)
                    quadro = (pont, consultado, nota_v, max(vozes), disparou)
                    if espiao is not None:
                        b = ep_verif.alimentar(consultado, nota_v, max(vozes), disparou)
                        if b:  # 20/10: o modelo base passou de limiar_verificador e o verificador rejeitou
                            print(json.dumps({"palavra_barrada_verificador": b["nota"], "voz": b["voz"]}), flush=True)
                            nome = None
                            if self.cfg.get("gravar_ativacoes") and gravando is None:
                                nome = self._nome_gravacao("barrada_")
                                gravando = (list(self.recentes), [], nome)
                            nomes_f.append(nome)
                            reg.escrever(registro_palavra.linha("barrada_verificador", b["nota"], b["voz"], nome))
                    acao = escolher_acao(modo_f, disparou, False)
                    if acao == "evento":
                        ja_evento = True
                        print(json.dumps({"palavra_nota": round(pont, 2), "voz": round(max(vozes), 2)}), flush=True)
                        nome = None
                        if self.cfg.get("gravar_ativacoes") and gravando is None:
                            nome = self._nome_gravacao()
                            gravando = (list(self.recentes), [], nome)
                            ctx = os.path.join(os.path.dirname(nome), "contexto_" + os.path.basename(nome))
                            threading.Thread(target=self._gravar_ativacao, args=(list(self.contexto), [], ctx),
                                             daemon=True).start()
                        sussurro = dp.ultimo_sussurro
                        self.piso_conversa = piso_ep
                        reg.escrever(registro_palavra.linha("sussurro" if sussurro else "ativou", pont, max(vozes),
                                                            nome, whisper_ratio_db=razao_ep))
                        nomes_f.append(nome)
                        self.emissor.emitir("ativacao", payload_ativacao(
                            "friday", pont, "conversa", modo="ativa", limiar=dp.limiar, limiar_voz=dp.limiar_voz,
                            voz=max(vozes), nota_verificador=nota_v if consultado else None,
                            limiar_verificador=self.cfg.get("limiar_verificador") if consultado else None,
                            razao_sussurro_db=razao_ep, sussurro=sussurro, gravacao=nome))
                        self.loop.call_soon_threadsafe(self.evento, "palavra", {"whisper": sussurro, "wake_word": "friday"})
                    elif acao == "sombra":  # spec 2026-10-24: pontua e registra; sem evento, som nem luz
                        print(json.dumps({"palavra_sombra": "friday", "nota": round(pont, 2)}), flush=True)
                        nome = None
                        if self.cfg.get("gravar_ativacoes") and gravando is None:
                            nome = self._nome_gravacao("sombra_friday_")
                            gravando = (list(self.recentes), [], nome)
                        reg.escrever(registro_palavra.linha("sombra", pont, max(vozes), nome, palavra="friday"))
                        nomes_f.append(nome)
                        self.emissor.emitir("ativacao", payload_ativacao(
                            "friday", pont, "sombra", modo="sombra", limiar=dp.limiar, limiar_voz=dp.limiar_voz,
                            voz=max(vozes), nota_verificador=nota_v if consultado else None,
                            limiar_verificador=self.cfg.get("limiar_verificador") if consultado else None,
                            razao_sussurro_db=razao_ep, sussurro=dp.ultimo_sussurro, gravacao=nome))
                    elif pont >= dp.limiar > pont_ant:  # cruzou o limiar mas sem voz humana (06/10: música da Echo)
                        print(json.dumps({"palavra_sem_voz": round(pont, 2), "voz": round(max(vozes), 2)}),
                              flush=True)
                        nome = None
                        if self.cfg.get("gravar_ativacoes") and gravando is None:
                            nome = self._nome_gravacao("sem_voz_")
                            gravando = (list(self.recentes), [], nome)
                        nomes_f.append(nome)
                        reg.escrever(registro_palavra.linha("sem_voz", pont, max(vozes), nome,
                                                            whisper_ratio_db=razao_ep))
                    q = dp.pegar_quase()
                    if registro_palavra.quase_vale(q, dp.limiar, dp.limiar_voz):  # spec 2026-10-15 D8
                        print(json.dumps({"palavra_quase": q["nota"], "voz": q["voz"]}), flush=True)
                        nome = None
                        if self.cfg.get("gravar_ativacoes") and gravando is None:
                            nome = self._nome_gravacao("quase_")
                            gravando = (list(self.recentes), [], nome)
                        nomes_f.append(nome)
                        reg.escrever(registro_palavra.linha("quase", q["nota"], q["voz"], nome))
                    # spec 2026-11-03: causa real da rejeição do episódio (a gravação pode ser escolhida no quadro que
                    # abre o episódio, por isso anota antes e depois de alimentar)
                    for n_ in nomes_f:
                        if n_ is not None:
                            rej_f.anotar_gravacao(n_)
                    r_ = rej_f.alimentar(pont, max(vozes), dp.limiar, dp.limiar_voz, disparou, consultado=consultado,
                                         nota_verificador=nota_v if consultado else None,
                                         sussurro=bool(dp.limiar_sussurro is not None and razao_ep is not None
                                                       and razao_ep >= dp.limiar_sussurro), razao=razao_ep)
                    for n_ in nomes_f:
                        if n_ is not None:
                            rej_f.anotar_gravacao(n_)
                    if r_ and modo_f == "ativa":
                        self.emissor.emitir("ativacao", payload_ativacao(
                            "friday", r_["nota"], "barrada", modo="ativa", limiar=dp.limiar, limiar_voz=dp.limiar_voz,
                            voz=r_["voz"], causa=r_["causa"], nota_verificador=r_["nota_verificador"],
                            limiar_verificador=self.cfg.get("limiar_verificador"),
                            razao_sussurro_db=r_["razao_sussurro_db"], gravacao=r_["gravacao"]))
                # spec 2026-10-24: palavras extras (o extrator é compartilhado; cada uma tem detector e modo próprios)
                for e in self.cfg.get("palavras_extras", []):
                    det, nome_e = dets_extras.get(e["nome"]), e["nome"]
                    if det is None or e["modo"] == "desligada":
                        continue
                    nota_e = float(pred.get(nome_e, 0.0))
                    extras_pred[nome_e] = nota_e
                    det.limiar, det.limiar_voz = e["limiar"], self.cfg["limiar_voz_palavra"]
                    det.paciencia = e.get("paciencia", 1)
                    disp = det.alimentar(nota_e, max(vozes))
                    rej_e, nomes_e = rej_extras.setdefault(nome_e, RejeicaoEpisodio()), []
                    acao = escolher_acao(e["modo"], disp, ja_evento)
                    if acao == "evento":
                        ja_evento = True
                        print(json.dumps({"palavra_nota": round(nota_e, 2), "voz": round(max(vozes), 2),
                                          "palavra": nome_e}), flush=True)
                        nome = None
                        if self.cfg.get("gravar_ativacoes") and gravando is None:
                            nome = self._nome_gravacao(f"{nome_e}_")  # prefixo próprio: não mistura com as da Friday
                            gravando = (list(self.recentes), [], nome)
                        nomes_e.append(nome)
                        reg.escrever(registro_palavra.linha("ativou", nota_e, max(vozes), nome, palavra=nome_e))
                        self.emissor.emitir("ativacao", payload_ativacao(
                            nome_e, nota_e, "conversa", modo="ativa", limiar=det.limiar, limiar_voz=det.limiar_voz,
                            voz=max(vozes), paciencia=e.get("paciencia"), gravacao=nome))
                        self.loop.call_soon_threadsafe(self.evento, "palavra", {"whisper": False, "wake_word": nome_e})
                    elif acao == "sombra":
                        print(json.dumps({"palavra_sombra": nome_e, "nota": round(nota_e, 2)}), flush=True)
                        nome = None
                        if self.cfg.get("gravar_ativacoes") and gravando is None:
                            nome = self._nome_gravacao(f"sombra_{nome_e}_")
                            gravando = (list(self.recentes), [], nome)
                        nomes_e.append(nome)
                        reg.escrever(registro_palavra.linha("sombra", nota_e, max(vozes), nome, palavra=nome_e))
                        self.emissor.emitir("ativacao", payload_ativacao(
                            nome_e, nota_e, "sombra", modo="sombra", limiar=det.limiar, limiar_voz=det.limiar_voz,
                            voz=max(vozes), paciencia=e.get("paciencia"), gravacao=nome))
                    q = det.pegar_quase()  # 26/10: o "quase" das extras (antes só a Friday registrava)
                    if registro_palavra.quase_vale(q, det.limiar, det.limiar_voz):
                        print(json.dumps({"palavra_quase": q["nota"], "voz": q["voz"], "palavra": nome_e}), flush=True)
                        nome = None
                        if self.cfg.get("gravar_ativacoes") and gravando is None:
                            nome = self._nome_gravacao(registro_palavra.prefixo_quase_extra(nome_e))
                            gravando = (list(self.recentes), [], nome)
                        nomes_e.append(nome)
                        reg.escrever(registro_palavra.linha("quase", q["nota"], q["voz"], nome, palavra=nome_e))
                    for n_ in nomes_e:
                        if n_ is not None:
                            rej_e.anotar_gravacao(n_)
                    r_ = rej_e.alimentar(nota_e, max(vozes), det.limiar, det.limiar_voz, disp)
                    for n_ in nomes_e:
                        if n_ is not None:
                            rej_e.anotar_gravacao(n_)
                    if (r_ and r_["causa"] == "paciencia" and e["modo"] == "ativa" and r_["gravacao"] is None
                            and self.cfg.get("gravar_ativacoes") and gravando is None):
                        # 09/11: a palavra passou num quadro e faltou o seguinte (10:40 com música). O episódio só se
                        # resolve no fim, então o clipe sai do contexto: o episódio inteiro mais ~2 s antes.
                        nome = self._nome_gravacao(registro_palavra.prefixo_paciencia_extra(nome_e))
                        gravando = (trecho_do_episodio(self.contexto, r_["quadros"]), [], nome)
                        r_["gravacao"] = nome
                        reg.escrever(registro_palavra.linha("paciencia", r_["nota"], r_["voz"], nome, palavra=nome_e))
                    if r_ and e["modo"] == "ativa":
                        self.emissor.emitir("ativacao", payload_ativacao(
                            nome_e, r_["nota"], "barrada", modo="ativa", limiar=det.limiar, limiar_voz=det.limiar_voz,
                            voz=r_["voz"], causa=r_["causa"], paciencia=e.get("paciencia"), gravacao=r_["gravacao"]))
                if modo_f == "desligada" and extras_pred:  # só as extras precisam do trecho; a Friday não é registrada
                    quadro = (pont, False, 0.0, max(vozes), False)
                pont_ant = pont
            prob_fala = voz
            if self.fala_em_andamento:
                self.fala_em_andamento = False
                df.iniciar_falando()
            limiar_ws = self.cfg.get("whisper_min_ratio_db")
            if self.conversa_sussurrada and limiar_ws is not None:  # D7: sussurro quase não tem VAD; só nesta conversa
                r = whisper_ratio_db(pcm, floor_rms=self.piso_conversa, min_frames=2)
                if r is not None and r >= limiar_ws:
                    prob_fala = 1.0
            df.para_apos = para_apos_de(self.cfg)
            fala = df.alimentar(prob_fala)
            t = pico.alimentar(fala, *quadro, extras=extras_pred or None)
            if t:
                reg_falas.escrever(registro_palavra.linha_fala(t))
            if fala:
                self.loop.call_soon_threadsafe(self.evento, fala)
            if self.microfone_aberto and self.conexao:
                # spec 2026-11-04: "ela falando" = escreveu no aplay há menos de 0,3 s. O portão (padrão None = desligado)
                # só zera o que está abaixo do limiar nesse intervalo; o medidor sempre vê o áudio cru.
                ult = self.alto.ultimo_envio
                ela_falando = ult is not None and time.monotonic() - ult < 0.3
                if ela_falando:
                    self.medidor_eco.alimentar(pcm)
                self.loop.call_soon_threadsafe(self.conexao.enviar, portao_eco(pcm, ela_falando, self.cfg.get("eco_portao_db")))

    def botao(self):
        b = Botao(self.det_botao)
        while True:
            cor = b.esperar_toque()
            self.loop.call_soon_threadsafe(self.evento, "botao", cor)

    def teclas(self):
        """Botões +/− do Jabra. Falha aqui NÃO derruba o programa: sem ela só se perde a sincronia do volume."""
        try:
            t = TeclasVolume()
            while True:
                d = t.esperar()
                self.loop.call_soon_threadsafe(self.apertou_volume, d)
        except Exception as e:
            print(json.dumps({"teclas_volume_parou": f"{type(e).__name__}: {e}"}), flush=True)


async def principal():
    loop = asyncio.get_running_loop()
    s = Satelite(loop)
    n = s.vozes.limpar_novos()  # Review Focus 4: gravação ou treino interrompidos por um reinício
    if n:
        print(json.dumps({"aprendizagem": "pastas_novas_apagadas", "n": n}), flush=True)
    s.anel.show("off")
    s._aplicar_mudo(inicial=True)  # restaura o último mudo gravado; sem mudo, não força a captura
    s.volume.iniciar()  # M5: aplica o volume gravado (o último real, da voz ou do botão)
    s.evento("nao_perturbe_inicial", s.cfg["nao_perturbe"])  # arranque: aplica o estado, sem som de confirmação
    morreu = asyncio.Event()

    def em_thread(fn):
        def corpo():
            try:
                fn()
            except Exception as e:
                print(json.dumps({"erro_fatal": f"{type(e).__name__}: {e}"}), flush=True)
                loop.call_soon_threadsafe(morreu.set)
        threading.Thread(target=corpo, daemon=True).start()

    em_thread(s.captura); em_thread(s.botao)
    threading.Thread(target=s.teclas, daemon=True).start()
    threading.Thread(target=s.duck_laco, daemon=True).start()
    await porta_local.iniciar(CFG, s.estado_publico, s.ao_mudar_config, s.cfg["porta_local"], s.campainha,
                             palavras_fn=s.palavras_carregadas, comando_fn=s.comando_porta)
    print(json.dumps({"iniciado": s.sid}), flush=True)
    s.carregar_timers()
    while not morreu.is_set():
        s.tique()
        try:
            await asyncio.wait_for(morreu.wait(), timeout=0.2)
        except asyncio.TimeoutError:
            pass
    sys.exit(2)


if __name__ == "__main__":
    asyncio.run(principal())
