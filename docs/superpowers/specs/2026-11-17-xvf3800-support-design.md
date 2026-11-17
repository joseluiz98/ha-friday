# Suporte ao ReSpeaker XVF3800 no satélite (fase B) — desenho

17/11/2026. Decisões do dono nesta conversa; o que está marcado **(chamada do Claude)** é escolha técnica, corrigível.
Insumos medidos na bancada: `/config/docs/claude/xvf3800-requisitos.md` (§3–§7) e `satelite-sala-xvf3800.md`.

## Por quê
O satélite da sala (MK2) vai para o nicho do painel da TV hoje, com o ReSpeaker XVF3800 (cancelamento de eco em chip,
canal 1 linear, testado em 17/11: palavra "Friday" 14/14 a 2 m com música alta) e o kit 2.1 provisório. Hoje o programa
só funciona com o Jabra Speak 410. O dono quer o **mesmo** programa, imagem e OTA para as duas placas, e rastreabilidade
do risco conhecido da USB da Pi 3 com o XVF (travamento de 17/11 ~11:44, sem causa reproduzida; 2 h de ensaio depois sem
falha).

## Objetivo de hoje (definição de "rodando", dono)
O MK2 **boota e abre conversas de voz** com o XVF3800. Ajuste de limiar, recadastro de voz e retreino ficam para o dia
a dia (o dono espera taxa aceitável com o modelo atual).

## Objetivos
1. **Detecção da placa a cada partida** (abordagem A, escolhida pelo dono): Jabra (`0b0e:0410`) ou XVF3800 (`2886:001a`).
   **As duas ligadas → o XVF ganha** (dono). Trocar de placa = religar; ligar a placa com a Pi já de pé faz o programa
   subir sozinho, sem reboot.
2. **Sem placa compatível, o satélite segue vivo** (dono): a Pi boota, o agente (OTA, integração, sensores) funciona, só o
   programa de voz e o player esperam. O switch "Voice Program" mostra desligado e o motivo vai ao log ("nenhuma placa de
   áudio suportada"). Isso garante que um defeito desta fase se corrige por OTA.
3. **Áudio por placa**: `asound.conf` da placa escolhida; no XVF, gravação pelo **canal 1** (ASR, linear) convertida para
   16 kHz com o conversor `speexrate` (`libasound2-plugins`) e saída em **mono** (a referência do AEC é um canal).
4. **Controles por placa** no programa:
   - Volume 1–10: Jabra como hoje (PCM 0–11); XVF = controle `PCM` do card `Array`, valores 30, 33, 37, 40, 43, 47, 50,
     53, 57, 60 (−30 a 0 dB; o controle da placa termina em 0 dB e acima disso seria só corte digital).
   - Música (`Music`, softvol) e ducking no card da placa.
   - **Mudo no XVF**: o botão da placa corta o microfone em hardware; o programa lê o estado pelo pino X0D30
     (`GPO_READ_VALUES`, índice 1 = 1 com mudo) e o mudo pelo HA escreve no mesmo pino (`GPO_WRITE_VALUE [30,1]`/`[30,0]`):
     botão e HA nunca se contradizem. `GPI_VALUE_ALL` e o hidraw não servem (medido em 17/11).
   - Sem teclas de volume no XVF: volume só por voz e pelo HA.
   - Anel do XVF (já existe em `ring.py`) com a calibração de 17/11: `LED_OFFSET=5`, `LED_SENTIDO=+1`,
     `MUTE_LEDS=(10,11,0)`, `DND_LOW=0.25`.
5. **Ajustes da placa reaplicados a cada partida/enumeração** (o firmware não guarda nada sem `SAVE_CONFIGURATION`):
   saídas `AUDIO_MGR_OP_L 8 0` / `AUDIO_MGR_OP_R 7 3` e `AUDIO_MGR_SYS_DELAY` = valor do `config.json`
   (`xvf_sys_delay`, padrão 12 = fábrica; calibrado no lugar com o protocolo da nota §8 passo 2 e gravado pelo HA).
6. **Saída nunca presa num fluxo parado** **(chamada do Claude)**: um alimentador de silêncio contínuo no `dmix` da placa
   (processo mínimo, ~1% de CPU), para as duas placas. Motivo: com o player segurando a saída pausada a captura do XVF
   caiu a ~1/5 do tempo real (17/11). Com silêncio fluindo, o problema não aparece (2 h de ensaio).
7. **Vigia da USB, definitivo** (dono): detecta captura lenta/parada (taxa do canal de captura abaixo de 0,9 do tempo real
   em 3 janelas seguidas de 10 s, ou captura sem dados) e erro `-71` do USB no kernel; **reinicia a Pi** e, depois de
   voltar, registra a ocorrência: um contador `usb_recoveries` (`total_increasing`, Diagnóstico) e um evento no HA; a casa
   manda **push ao dono a cada ocorrência** (automação em `/config`, fora do repo) dizendo o que viu, quando e se o reboot
   recuperou. Vale no uso diário, não só em ensaio. **Nunca** usar `xvf_host REBOOT` (piora o estado, 17/11).
8. **DOA v0 com trava de feixe** (dono escolheu a opção 2): **só durante a conversa** (dono, 17/11: em repouso o anel segue apagado) um LED aponta para quem fala; quando a palavra
   dispara, o feixe trava na direção de quem a disse até a conversa fechar, e solta ao fechar e ao iniciar o programa
   (nunca fica travado depois de um crash). Teste mínimo: ativação básica; o teste real é o uso diário com a TV, com
   correções nos primeiros dias. Detalhes de comandos e formato: seção "Trava de feixe" (pesquisa de 17/11).
9. **O HA mostra a placa real**: modelo do aparelho "Raspberry Pi 3 + XVF3800" ou "+ Jabra Speak 410" (hoje é fixo
   "+ Jabra"); `mic` em `/etc/friday-satelite/mic` passa a ser atualizado na partida, não só no primeiro boot.

## Não objetivos (hoje)
Recadastro de voz (o dono faz pela voz, programa de aprendizagem existente; satélite novo já nasce sem verificador),
retreino da palavra, ajuste de limiar, DOA além da v0, caixa definitiva, outras placas.

## Anti-objetivos
- Uma placa ausente ou com defeito impedir o boot, o OTA ou a integração.
- O Jabra do MK1 mudar de comportamento (mesmo `asound.conf`, mesmos controles, mesma luz).
- Mudo pelo HA e pelo botão discordarem; anel ou switch mostrarem intenção em vez do estado real
  (regra do dono: switch mostra sempre o estado real).
- Push de madrugada sem necessidade: o push do vigia sai a cada ocorrência (decisão do dono), mas é uma por evento.

## Restrições
- Pi 3, 1 GB, um job pesado por vez; USB 2.0 `dwc_otg`. Firmware do XVF: USB v2.1.1 48k2ch (só 48 kHz, 2 canais).
- Entrega só por OTA (alpha → stable), satélite testável ao vivo só no alpha depois do merge.
- Nada da casa no repo (IP, nomes); push é configuração da casa.
- Comportamento do produto é decisão do dono; o desenho registra as dele.

## Abordagens consideradas
- **A. Placa detectada a cada partida (escolhida).** Passo de boot escolhe o `asound.conf`; o programa monta a camada de
  hardware da placa achada. Mais código hoje, mas cumpre "duas placas no mesmo satélite" e uma imagem para todos.
- B. Placa fixada no provisionamento (`firstboot` grava a placa). Menos código; trocar de placa exige reconfigurar e uma
  placa ausente deixa estado inválido.
- C. Só XVF provisório agora. Mais rápido, código descartável e MK1/MK2 divergentes; fere "não pular premissas".

## Desenho
### Partida e troca de placa
- Um serviço curto `friday-audio-select` (root, antes de `friday-satelite` e `sendspin-cli`, e disparado também por udev
  na chegada/saída de qualquer das duas placas) detecta a placa (XVF > Jabra > nenhuma), aponta `/etc/asound.conf` para
  `/etc/friday-satelite/asound/<placa>.conf` (os dois arquivos vêm no pacote OTA em `system/`) e grava
  `/etc/friday-satelite/audio_board` (`xvf3800` | `jabra` | `none`). Com placa nova, reinicia programa e player.
- `friday-satelite` e `sendspin-cli` só sobem com `audio_board` ≠ `none` (o `ExecCondition` só-Jabra provisório do MK1 sai).
- O alimentador de silêncio sobe junto com a placa.

### Programa
- Uma camada `placa` com duas implementações (Jabra = o código de hoje sem mudança de comportamento; XVF = novo), criada
  pela partida a partir de `audio_board`: volume, música, mudo (ler/escrever), botão (Jabra) ou leitura periódica do
  pino de mudo (XVF), anel (`ring.make_ring` já escolhe), reaplicação de ajustes do XVF.
- O canal de controle do XVF (USB vendor, `vendor/xvf_host.py`) é um só e serializado entre anel, mudo, ajustes e DOA.
- A leitura do mudo pelo pino a cada ~200 ms **(chamada do Claude)**; o anel vermelho e o switch seguem o estado lido.

### Vigia da USB
- **Onde mede (chamada do Claude):** o programa já lê a captura; ele publica a taxa (amostras/s) na porta local a cada
  janela, e o agente decide (o agente sobrevive ao programa travado; se o programa parar de publicar com a placa
  presente, conta como captura parada). O agente também lê o `dmesg`/kernel log por `-71`.
- Recuperação: reboot da Pi pelo caminho privilegiado que o agente já usa; antes do reboot grava a ocorrência em
  `~/.friday-agent/usb_recoveries.json`; depois do boot publica o contador e o evento (com o motivo e se a captura voltou).
- Limite de reboots em cadeia **(chamada do Claude)**: no máximo 3 em 1 h; depois disso só avisa (evento "esgotado") e não
  reinicia mais até a próxima hora, para não prender a Pi num laço.

### Integração (HA)
- Sensor `usb_recoveries` (Diagnóstico, `total_increasing`) e evento `usb_recovery` no satélite; descriptor com categoria
  (regra de posicionamento da v0.3.8). Modelo do aparelho pela placa real. Configuração `xvf_sys_delay` (número, Config).
- Push ao dono: automação da casa em `/config` sobre o evento (fora do repo).

### Respostas à revisão de construtor (17/11)
- **Teste no aparelho antes do código da trava:** o Claude tem SSH no satélite; o teste de 5 min roda no MK2 com o XVF,
  programa parado, antes de escrever a trava (o resto não depende dele). Se não der para rodar a tempo, a trava sai com
  as duas rotas atrás de um ajuste e se decide no alpha.
- **Mudo em hardware não para a captura:** medido em 17/11, com o botão a captura segue em tempo real com zeros exatos;
  o vigia não confunde mudo com travamento.
- **Vigia só no XVF (chamada do Claude):** o Jabra não muda (anti-objetivo). Só conta quando o programa está de pé e
  publicando taxa baixa ou parou de publicar sem ter sido parado; programa parado de propósito (switch, OTA, reinício do
  programa, ativação de release) não conta. Não espera a conversa fechar: captura parada já quebrou a conversa.
- **Mudo sobrevive a reboot e a replug (como hoje):** o estado de mudo já é gravado e reconstruído na partida
  (desenho do anel de 16/11); no XVF a partida escreve o pino X0D30 com o estado gravado antes de abrir a escuta.
- **Placa plugada não passa por cima do "Voice Program" desligado:** a intenção gravada continua valendo; o
  `friday-audio-select` só (re)inicia programa e player se a intenção for ligado.

### Trava de feixe (DOA v0)
Pesquisa completa: `docs/research/2026-11-17-xvf3800-beam-lock.md` (referência: o projeto ESPHome da comunidade
formatBCE, que faz o mesmo; comandos conferidos na tabela do `xvf_host.py` do firmware v2.1.1).
- **Leitura contínua da direção** **(chamada do Claude)**: enquanto o programa roda, ler `AEC_AZIMUTH_VALUES` (33/75,
  4 floats em radianos; índice 3 = feixe auto-select) e o indicador de fala a 5–10 Hz num buffer de ~3 s. É a mesma
  leitura que move o LED da direção durante a escuta (conversão para LED: `round(graus/30)` + `LED_OFFSET`, sentido +1).
- **Travar no disparo da palavra**: a palavra é reconhecida depois que termina; por isso a direção é a **média circular
  das amostras com fala do último 1,5 s**. Só trava com ≥ 3 amostras e ≥ 70% delas a até 30° do resultado; senão fica no
  modo adaptativo de hoje (melhor não travar do que travar na TV). Escritas: `AEC_FIXEDBEAMSAZIMUTH_VALUES [θ, θ]`,
  depois `AEC_FIXEDBEAMSONOFF 1`, depois ler de volta.
- **Soltar**: `AEC_FIXEDBEAMSONOFF 0` (e ler de volta) quando a conversa fecha, em qualquer erro, num teto de 90 s, ao
  fechar o programa e **sempre na partida** (nunca fica travado depois de um crash ou reboot).
- **Nunca** chamar `SAVE_CONFIGURATION` (desde o firmware 2.0.9 o feixe fixo é salvo e voltaria travado no boot).
- **Canal de controle único**: anel, mudo, ajustes e DOA passam por um único dono do USB, com fila/trava; nenhuma leitura
  no caminho da palavra sem prazo (a leitura do `xvf_host.py` pode esperar ~1 s pela placa).
- **Ponto a confirmar no aparelho antes de escrever o código da trava** (documentação da XMOS não responde): o nosso
  canal 1 sai do feixe auto-select (`AUDIO_MGR_OP_R 7 3`), que escolhe entre os feixes; não está claro se, com o modo
  fixo ligado, ele ainda escolhe o feixe livre (que segue a TV). Teste de 5 min (pesquisa §4.4): travar na posição A,
  falar da B, ver se `AEC_AZIMUTH_VALUES[3]` fica em A, gravar o canal 1. Se não ficar, a trava também roteia o canal 1
  para o feixe travado (`AUDIO_MGR_OP_R 7 0`) e devolve `7 3` ao soltar.
- **Regra da trava (dono, 17/11; a mesma do ESPHome):** trava na palavra, fica travada durante o **primeiro pedido**
  depois da palavra e **solta quando a pessoa para de falar** (fim da fala detectado pelo programa); no resto da
  conversa vale o comportamento de fábrica do XVF (feixe livre + seleção automática), que acompanha quem anda falando.
  As proteções de soltar (partida, fim da conversa, erro, teto de 90 s) continuam.
- **Switch "Beam lock"** (configuração no HA, ligado por padrão): desligado, nunca trava (só o comportamento de fábrica).
  O dono compara os dois no uso com a TV. Limitação conhecida da trava: quem anda **durante o primeiro pedido** sai do
  feixe. Evolução registrada: pesquisar como outros alto-falantes (sobretudo as Echos) tratam direção e ruído.
- Aceite de hoje: ativação básica (palavra de duas posições, conversa abre, trava e solta no log). Aceite real: uso
  diário com a TV; o dono garante correções nos primeiros dias.
- Achado da pesquisa: no repo, `ring.py` ainda tem `LED_OFFSET = 0`/`MUTE_LEDS = (0,)`; a calibração de 17/11 entra
  nesta mudança.

## Imagem e OTA
- A imagem já traz `libasound2-plugins`, a regra udev `99-respeaker.rules` e o pyusb; passa a trazer os dois `asound` e o
  `friday-audio-select`. Satélites já instalados recebem tudo pelo OTA (`system/`); uma migração troca o `/etc/asound.conf`
  fixo pelo apontamento e remove o drop-in `jabra-only.conf` provisório do MK1.
- `mic` e modelo no HA passam a refletir a placa da partida.

## O que fica para o construtor
Nomes internos, estrutura da camada `placa`, formato exato da taxa publicada, como o udev dispara o serviço, testes.

## Provas (aceite)
1. Suíte do repo verde (testes das duas placas com falsos).
2. MK1 com Jabra: nada muda (sons, volume, mudo, anel, música, conversa).
3. MK2 com XVF na sala: boota, abre conversa pela palavra, volume 1–10, mudo pelo botão e pelo HA (anel vermelho, switch
   real), música com ducking, ativação básica com a trava de feixe.
4. Sem placa: Pi e agente no ar, "Voice Program" desligado com o motivo; plugar a placa → programa sobe sozinho.
5. Vigia: falso de captura parada em teste → reboot simulado, contador e evento; no aparelho, só observação no uso diário.
