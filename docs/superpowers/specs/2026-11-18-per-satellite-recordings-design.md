# Gravações por satélite: origem rastreável do MK ao treino

Data: 18/11/2026. Dono: o dono do projeto. Status: desenho em aprovação.

## Por quê

Com o MK2 (XVF3800, sala) ao lado do MK1 (Jabra, escritório), as ativações e as gravações de voz usadas para treinar
as palavras passam a vir de dois microfones diferentes. Para treinar e avaliar direito é preciso saber, de qualquer
gravação, de qual satélite ela veio, do satélite até o ciclo de treino. Hoje isso só vale enquanto o arquivo está na
pasta do satélite, e o MK2 nem grava.

## O que existe hoje (levantado em 18/11)

| Elo | Origem? | Situação |
|---|---|---|
| Evento de ativação no HA (`event.friday_sat_<id>_activation`) | sim, uma entidade por satélite | o card do System Health recebe só a do MK1 e não diz o MK em cada linha |
| WAV de cada ativação (`~/gravacoes/ativacoes/` na Pi; toca pelo device em `/api/friday/recording/<device>/<nome>`) | sim | MK2 com `gravar_ativacoes: false` (padrão de fábrica em `configuracao.py`): não grava nada |
| Ambiente 08:00–22:00 (`~/gravacoes/ambiente/`, `ambiente.py`) | — | ligado por `input_boolean.friday_ambient_recording` + automação + `rest_command.friday_satellite_ambient_set`, este com o IP e a senha do MK1 fixos (`/config/packages/friday_wakewords.yaml`). MK2: zero arquivos |
| Envio noturno Pi → VM (`satellite/sync/sync_recordings.sh` → `incoming/<sat_id>/`, rrsync só-escrita) | sim, pela pasta | o MK2 falha toda noite com `Host key verification failed`: a imagem não traz a chave de host da VM. `incoming/<sat-mk2>/` vazia desde 06/11 |
| Consumo na VM | — | `featcache_nightly.sh`, `featcache_build.sh`, `vad_gate_rerun.sh` e o carimbo `wakeword_sync_last_ok` do agente do servidor leem só `FRIDAY_SAT_OFFICE` (MK1). `harvest.sh` e `analysis/run.sh` leem só `/config/.friday/mk1` |
| Dataset e cache | **não** | nomes `20261015_180746.wav`, `contexto_*.wav`, `ambiente_AAAAMMDD_HHMMSS.wav`, cache `ambAAAAMMDD` e as linhas de `eventos_palavra.ndjson` não trazem o satélite; ao sair da pasta, a origem some |
| Vigia `wakeword_pipeline_late` (`/config/packages/friday_producao.yaml`, 09:30) | — | olha um carimbo de envio (o do MK1) e um de cache, globais |

## Objetivos

1. Toda gravação (ativação, ambiente, cache de ambiente, clipe de dataset) tem origem derivável: o `sat_id`.
2. Cada MK tem as suas chaves de gravação, na engrenagem (Advanced) do satélite na integração.
3. O MK2 grava ativações e ambiente a partir desta versão.
4. O envio noturno funciona em qualquer satélite pareado, MK2 incluído.
5. Os consumidores e os vigias da VM e do HA cobrem todos os satélites, **respeitando a adoção**: um satélite com a
   gravação desligada não é cobrado.
6. O card de ativações do System Health mostra o MK de cada linha e filtra por satélite.

## Não objetivos

- Mudar como a Pi grava (formato, janela 08:00–22:00, duração dos blocos, pré-gravação).
- Retreinar palavras ou mudar o conteúdo dos ciclos já feitos.
- Um card de status (CPU, temperatura) para o MK2 no System Health (pode vir depois, fora daqui).
- Escolher o canal do XVF para o ambiente: grava o que a escuta da palavra já recebe (canal 1, ASR).

## Anti-objetivos (falharia mesmo funcionando)

- Um ciclo antigo perder o pareamento de clipes (`--pin-from`/`carry_over.py`): um clipe já em prova não pode virar
  treino porque mudou de nome.
- Vigia que avisa sobre satélite que não adotou a gravação, ou que se cala quando um satélite que adotou para.
- Aceitar às cegas a chave de host da VM (gravação da casa indo para quem se fizer passar pela VM na rede).
- Configuração que só vale para um IP ou um satélite fixo (o que quebrou aqui).

## Restrições

- Fonte única no repo `ha-friday`; nada em produção; chega por release OTA (merge e tag só com o OK do dono).
- Mudança no satélite vai para a imagem nova (mestres do repo), não só para as Pis atuais.
- Comportamento do satélite: decidido pelo dono nesta conversa (abaixo); o resto, padrão "como hoje".
- Pi 3: nada de job pesado novo na Pi; o trabalho pesado segue na VM.
- Testes do programa só com `TMPDIR` privado; código, commits e nomes em inglês; docs em pt-BR.
- Valores da casa (ids, IPs) fora do repo.

## Abordagens consideradas

- **A. Tudo dentro da integração (escolhida pelo dono).** O agente do satélite anuncia as chaves como ajustes
  avançados; a migração liga o MK2; o pareamento entrega a chave de host da VM; a VM percorre todos os satélites; o
  `sat_id` entra no nome ao sair da pasta do satélite; o card descobre os satélites sozinho. Mexe em satélite, VM e
  integração; vale para os satélites novos.
- **B. Só no HA, em YAML** (uma chave, uma automação e um `rest_command` por MK; card com duas entidades). Rápida, mas
  presa a IP e senha de cada Pi, não vale para satélites novos e não resolve o envio do MK2 nem a origem no dataset.
  Descartada.

## Decisões do dono

1. Uma chave por MK (não uma global). Duas chaves: gravar ambiente (janela 08:00–22:00) e gravar ativações.
2. As chaves ficam na engrenagem do satélite, em **Advanced** (como o canal de atualização e os modos das palavras),
   não na página do device.
3. O MK2 nasce gravando (as duas ligadas).
4. Os vigias existentes do envio e do cache seguem funcionando, respeitando a adoção de cada MK.

## Desenho

### 1. Chaves por satélite (agente do satélite + integração)

- Dois ajustes novos no agente do satélite (`agents/satellite/friday_satellite_agent/capabilities.py`), `switch`
  com `hidden=True`:
  - `record_ambient` ⇄ `gravar_ambiente_janela`: ligado = `"08:00-22:00"`, desligado = `null`. Qualquer janela não
    nula conta como ligado. Ao desligar, também zera `gravar_ambiente_ate`.
  - `record_activations` ⇄ `gravar_ativacoes`.
- A integração os lista em Advanced (entram em `ADVANCED_FIXED`, `custom_components/friday/settings.py`), com rótulo
  e descrição em inglês e pt-BR nas traduções.
- Padrão de fábrica (`configuracao.py`) passa a ser: `gravar_ativacoes: true`, `gravar_ambiente_janela:
  "08:00-22:00"`. Vale para a imagem nova.
- Migração `0007` (satélite): liga as duas onde a config tem `gravar_ativacoes` falso/ausente **e**
  `gravar_ambiente_janela` nula/ausente, ou seja, num satélite que nunca adotou nada (o MK2). Um satélite que já tem
  qualquer uma ligada (o MK1) não é tocado. Idempotente, mesmo molde da `0006`.
- Aposentar no HA (config da casa, não o repo): `input_boolean.friday_ambient_recording`, a automação
  `friday_ambient_recording_window` e o `rest_command.friday_satellite_ambient_set`. A Pi já guarda a janela e
  reaplica sozinha todo dia, então o reenvio no arranque do HA deixa de ser necessário. O estado atual do MK1
  (`08:00-22:00`) fica como está.

### 2. Envio noturno em qualquer satélite

- A VM entrega ao satélite a sua chave pública de host SSH por um canal em que o satélite já confia:
  - satélite novo: na resposta do pareamento (`provisioning.py` do servidor → `provision.py` do satélite);
  - satélite já pareado (o MK2): o agente do satélite busca essa chave no servidor pela conexão autenticada que já
    usa (ou a integração a repassa); o builder escolhe o caminho, desde que não seja confiança cega
    (`StrictHostKeyChecking=accept-new`/`ssh-keyscan` sem verificação estão fora).
- O satélite grava a chave num `known_hosts` próprio do envio (`~/.ssh/known_hosts_satsync`), e o
  `sync_recordings.sh` usa `UserKnownHostsFile` apontando para ele, com `StrictHostKeyChecking=yes`.
- O script grava, só com o envio inteiro OK, um carimbo local (`~/gravacoes/.sync_ok_local` ou equivalente fora de
  `gravacoes/` se o rsync o levaria junto: decisão do builder) que o agente do satélite expõe (seção 4).

### 3. VM: todos os satélites e origem no nome

- `featcache_nightly.sh` percorre `incoming/*/gravacoes/ambiente` de todos os satélites. O cache de um dia de um
  satélite passa a se chamar `amb<AAAAMMDD>_<sat_id>` (ex.: `amb20261119_<sat-mk2>`). Na instalação (`install.sh`)
  os caches antigos do MK1 (`amb<AAAAMMDD>`) são renomeados uma vez para o nome novo com o `sat_id` do
  `FRIDAY_SAT_OFFICE`; as ferramentas que listam caches por nome (`ablation_eval.sh`, `replay_sexta.py` e afins)
  passam a aceitar o nome novo. Retenção de 30 dias segue por satélite.
- O disparo noturno acorda com a marca `.sync_ok` de **qualquer** satélite (hoje é um `.path` só do MK1) e processa
  o que estiver pendente de todos, um dia por vez, `nice 19`. O carimbo global `wwt-featcache.ok` continua (o vigia
  usa); além dele, o script registra o último dia cacheado de cada satélite (para o vigia por adoção, seção 4).
  Custo: ~80 min de extrator por dia de ambiente por satélite; com dois, ~2 h 40 min por noite na VM.
- `featcache_build.sh` e `vad_gate_rerun.sh` ganham `--sat <sat_id>` (padrão: `FRIDAY_SAT_OFFICE`, como hoje).
- `harvest.sh` e `analysis/run.sh` ganham `--sat <sat_id>` e acham o endereço da Pi em `/share/friday/satellites.json`
  (padrão: o MK1, como hoje). O que sai da Pi recebe o prefixo `<sat_id>__` no nome
  (ex.: `<sat-mk2>__20261119_101500.wav`) e o manifesto do ciclo registra o campo `sat` de cada clipe.
- **Compatibilidade de ciclos:** um nome sem prefixo é do MK1 (legado). O pareamento de prova/treino entre ciclos
  (`build_dataset.py --pin-from`, `carry_over.py`) trata `<sat-mk1>__X` e `X` como o mesmo clipe. Teste obrigatório:
  um clipe do MK1 já em prova num ciclo antigo continua em prova quando reaparece com prefixo.
- Ciclos já construídos não são reescritos.

### 4. Vigias respeitando a adoção

- **Envio (por satélite):** o agente de cada satélite anuncia o sensor de diagnóstico "Recordings sync last OK"
  (timestamp do carimbo local da seção 2), com atributos `record_ambient` e `record_activations` (estado atual das
  chaves). Satélite sem nenhuma das duas ligada reporta valor nulo (não cobrado).
- **Cache (por satélite com ambiente adotado):** o sensor do servidor "Wake word feature cache last OK" mantém o
  carimbo global e ganha o atributo `satellites` = `{sat_id: último dia cacheado}`.
- O sensor do servidor "Wake word recordings sync last OK" (só o MK1) sai; nada fora do vigia o usa (conferido em
  18/11 em `/config`). Entra nas notas como mudança.
- `wakeword_pipeline_late` (09:30, config da casa) passa a: para cada satélite com alguma chave ligada, cobrar o envio
  com mais de 26 h; para cada satélite com `record_ambient` ligado, cobrar o cache se o último dia cacheado for
  anterior a ontem. Um push só, com o nome do device (MK1/MK2) e o que atrasou. Satélite fora do ar (`unavailable`)
  é cobrado como hoje ("sem registro"); satélite que desligou as chaves, não.

### 5. Card de ativações (frontend da integração)

- `entity` passa a ser opcional: sem ela, o card usa todas as entidades `event.friday_*_activation`.
- Cada linha ganha um selo com o nome do device (o `name_by_user`, senão o nome), ex.: MK1, MK2.
- Com mais de um satélite, uma fileira de chips por satélite filtra as linhas (todos ligados no início), ao lado dos
  chips de tipo e período que já existem.
- No System Health (`/config/ui_lovelace_minimalist/dashboard/home-views/system-health.yaml`), o card perde a
  `entity` fixa. O device `<sat-mk2>` é renomeado para "MK2" no HA (como o MK1 já foi), para o selo dizer MK2.

## Para o builder decidir

- O caminho exato da chave de host para o satélite já pareado (seção 2), dentro da restrição de não confiar às cegas.
- Onde mora o carimbo local do envio e como o agente o lê.
- Mecânica do disparo da VM por várias marcas (vários `.path`, um `.path` por diretório gerado no `install.sh`,
  ou um timer de reserva).
- A forma do registro do último dia cacheado por satélite.
- Estrutura dos testes, desde que cubram: migração `0007` (MK2 liga, MK1 intocado, idempotente), mapeamento das
  chaves ⇄ config, compatibilidade de nomes legados nos ciclos, consumidores da VM com dois satélites, regras do
  vigia por adoção e o card (selo e filtro).

## Provas depois do release

- MK2: gravações em `~/gravacoes/ativacoes/` e `~/gravacoes/ambiente/` no mesmo dia; envio da madrugada OK e
  `incoming/<sat-mk2>/` com arquivos; cache `amb<dia>_<sat-mk2>` na noite seguinte.
- MK1: nada muda no que grava; cache antigo renomeado e lido pelas ferramentas.
- Engrenagem → Advanced dos dois MKs mostra as duas chaves com o estado real.
- Card no System Health com MK1 e MK2, selo e filtro.
- Vigia das 09:30: desligar o ambiente do MK2 por um dia não gera push do cache do MK2.
