# Gravações por satélite — plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** cada MK grava (ativações e ambiente) por chave própria em Advanced, envia à VM, e toda gravação leva o `sat_id` até o treino; vigias e card cobrem todos os satélites respeitando a adoção.

**Architecture:** chaves como ajustes avançados do agente do satélite (padrão de fábrica ligado + migração 0007); chave de host da VM entregue pelo pareamento e reaplicada a satélites já pareados; VM percorre `incoming/*`; nomes ganham `<sat_id>` ao sair da pasta do satélite; card descobre os satélites.

**Tech Stack:** Python 3 (agentes, integração HA, ferramentas), bash (sync, migração, VM), JS puro (card), unittest/pytest, `node --test` no frontend.

**Spec:** `docs/superpowers/specs/2026-11-18-per-satellite-recordings-design.md`

**Execução (pedido do dono):** tarefas 3, 4 e 5 por agente Opus; 1, 2 e 6 por agente Sonnet; sem revisão intermediária; revisão final do Claude principal. A tarefa 7 é config da casa, feita pelo Claude principal depois do release.

## Global Constraints

- Código, nomes, commits em inglês; docs em pt-BR. Commits terminam com as linhas de atribuição da sessão.
- Nada da casa no repo: sem ids reais de satélite, IPs ou nomes de pessoas (o `secret-sweep` do pre-commit recusa). Nos testes use `sat-0a1b2c`, `sat-1d2e3f`.
- Nunca `--no-verify`. Testes do programa do satélite só com `TMPDIR` privado: `TMPDIR=$(mktemp -d -p <pasta privada>) python3 -m unittest discover -p 'test_*.py'` em `satellite/program`.
- Suite completa: `scripts/test-all.sh` (roda sob `TMPDIR` privado e falha em vazamento).
- Janela de ambiente fixa: `"08:00-22:00"`. Chaves: `record_ambient` ⇄ `gravar_ambiente_janela`; `record_activations` ⇄ `gravar_ativacoes`.
- Nome legado sem prefixo = MK1 (`FRIDAY_SAT_OFFICE`). Prefixo de clipe: `<sat_id>__`. Cache: `amb<AAAAMMDD>_<sat_id>`.
- Confiança cega na chave de host proibida (`accept-new`, `ssh-keyscan` sem verificação).

## Review Focus

1. Satélite com `gravar_ambiente_janela` personalizada (ex.: `"09:00-18:00"`): o switch mostra ligado e ligar de novo não sobrescreve a janela. → teste na Tarefa 2.
2. Config sem as chaves (satélite antigo) e config com `gravar_ativacoes: true` só (adoção parcial): a 0007 não liga nada. → teste na Tarefa 1.
3. `incoming/<sat>/` sem pasta `gravacoes/ambiente` (MK2 antes do 1º envio): o cache noturno não falha e registra a passagem do satélite. → teste na Tarefa 4.
4. Clipe legado do MK1 já em prova reaparece com prefixo: continua em prova; clipe do MK2 com o mesmo nome base do MK1 é outro clipe. → teste na Tarefa 5.
5. `set_sync_host_key` com chave malformada ou host vazio: recusa sem gravar nada, `known_hosts_satsync` anterior intacto. → teste na Tarefa 3.

---

### Task 1: Padrão de fábrica e migração 0007 (Sonnet)

**Files:**
- Modify: `satellite/program/configuracao.py` (padrões `gravar_ativacoes`, `gravar_ambiente_janela`)
- Create: `satellite/migrations/0007_recordings_default.sh`
- Test: `tests/agents/test_ota_migrations.py`, `satellite/program/test_configuracao.py` (se o padrão estiver coberto)

**Interfaces:** Produces: padrão `gravar_ativacoes: True`, `gravar_ambiente_janela: "08:00-22:00"`.

- [ ] **Step 1: testes que falham** em `tests/agents/test_ota_migrations.py`, no molde dos da 0006:
  - `test_0007_turns_both_on_when_nothing_adopted`: config `{"gravar_ativacoes": false, "gravar_ambiente_janela": null}` → depois `gravar_ativacoes is True` e `gravar_ambiente_janela == "08:00-22:00"`, modo e dono do arquivo preservados.
  - `test_0007_keys_absent_counts_as_not_adopted`: config `{}` → as duas ligadas.
  - `test_0007_leaves_partial_adoption_alone`: `{"gravar_ativacoes": true, "gravar_ambiente_janela": null}` e `{"gravar_ativacoes": false, "gravar_ambiente_janela": "09:00-18:00"}` → arquivo byte a byte igual.
  - `test_0007_idempotent` e `test_0007_no_config_is_ok` (rc 0, mensagem).
  - Teste do padrão: `configuracao.PADRAO["gravar_ativacoes"] is True` e `["gravar_ambiente_janela"] == "08:00-22:00"` (use o nome real do dicionário de padrões).
- [ ] **Step 2:** rodar, ver falhar.
- [ ] **Step 3:** escrever a 0007 no molde exato da `0006_sexta_feira_default.sh` (mesmo cabeçalho de comentário, `FRIDAY_OTA_ROOT`, `.tmp` + `os.replace`, modo/dono preservados); alterar os dois padrões em `configuracao.py` com comentário datado 18/11. Conferir se a lista de migrações precisa de registro (ver `agents/common/friday_agent_core/ota/migrations.py`) e se algum manifesto de pacote (`ota/release.json` `package.satellite`) lista as migrações por nome.
- [ ] **Step 4:** testes da migração + suite do programa (TMPDIR privado) passam.
- [ ] **Step 5:** commit `feat(satellite): record activations and ambient by default; migration 0007`.

### Task 2: Chaves em Advanced e sensor de envio (Sonnet)

**Files:**
- Modify: `agents/satellite/friday_satellite_agent/capabilities.py`, `agents/satellite/friday_satellite_agent/__main__.py` (passar o carimbo)
- Modify: `custom_components/friday/settings.py` (`ADVANCED_FIXED`), `custom_components/friday/strings.json`, `translations/en.json`, `translations/pt-BR.json`
- Test: `tests/agents/test_satellite_capabilities.py` (ou o arquivo de testes existente de `capabilities`), `tests/integration/test_settings*.py`

**Interfaces:**
- Produces (protocolo do satélite):
  - switches `hidden=True` `record_ambient` ("Record ambient audio", ícone `mdi:waveform`) e `record_activations` ("Record activations", `mdi:record-rec`), `category="config"`;
  - sensor `recordings_sync_last_ok` ("Recordings sync last OK", `device_class="timestamp"`, `category="diagnostic"`), valor ISO UTC do carimbo local ou `None`, e `recordings_sync_last_ok_attributes = {"record_ambient": bool, "record_activations": bool}`. Valor `None` quando as duas chaves estão desligadas.
  - Carimbo local: arquivo `~/.friday_sync_ok` (mtime = último envio inteiro OK; escrito pela Tarefa 3). Caminho por env `FRIDAY_SAT_SYNC_STAMP`, padrão `/home/jose/.friday_sync_ok`.
- `Capabilities.state(cfg, wakewords, status, sync_ok_mtime: float | None = None)`.

- [ ] **Step 1: testes que falham:**
  - `state`: `gravar_ambiente_janela="08:00-22:00"` → `record_ambient True`; `"09:00-18:00"` → True; `None` → False. `gravar_ativacoes` → `record_activations`.
  - `to_program("record_ambient", True)` com janela nula → `{"config": {"gravar_ambiente_janela": "08:00-22:00"}}`; com janela `"09:00-18:00"` → `{"config": {}}` (não muda a janela); `False` → `{"config": {"gravar_ambiente_janela": None, "gravar_ambiente_ate": None}}`. `record_activations` → `{"config": {"gravar_ativacoes": v}}`.
  - Sensor: com uma chave ligada e mtime 1763500000 → ISO UTC desse instante e atributos corretos; as duas desligadas → `None`; sem carimbo → `None` com chave ligada (atributos presentes).
  - Integração: `advanced_keys` inclui `record_ambient` e `record_activations` quando anunciados; `validate_advanced` aceita bool e recusa string.
- [ ] **Step 2:** ver falhar.
- [ ] **Step 3:** implementar; `__main__.py` lê o mtime do carimbo a cada publicação de estado (sem I/O extra além de um `os.stat`). Traduções: pt-BR "Gravar áudio do ambiente" (descrição: "Grava das 08:00 às 22:00 para treinar as palavras; vai para a VM de madrugada") e "Gravar ativações" ("Guarda ~3,5 s em volta de cada ativação para análise"); en equivalentes.
- [ ] **Step 4:** testes dos agentes e da integração passam (`scripts/test-all.sh` ou os subconjuntos usados no repo).
- [ ] **Step 5:** commit `feat(satellite): per-satellite recording switches in Advanced and sync sensor`.

### Task 3: Chave de host da VM e envio confiável (Opus)

**Files:**
- Modify: `agents/server/friday_server_agent/provisioning.py` (`register_sync_key` devolve a chave de host)
- Modify: `custom_components/friday/provisioning.py` (`_step_sync_key` repassa; recuperação de satélite já pareado)
- Modify: `agents/satellite/friday_satellite_agent/provision.py` + `__main__.py` (comando `set_sync_host_key`; `provision_info` com `sync_host_known`)
- Modify: `satellite/sync/sync_recordings.sh`
- Test: `tests/agents/test_server_provisioning.py`, `tests/agents/test_satellite_provision*.py`, `tests/integration/test_provisioning*.py`, teste do script (`tests/agents/test_sync_recordings.py`, novo, com `ssh`/`rsync` falsos no PATH)

**Interfaces:**
- Server: `register_sync_key(...)` → `{"ok": True, "changed": bool, "host_key": "ssh-ed25519 AAAA..."}`; chave lida de `FRIDAY_SSH_HOST_KEY` (padrão `/etc/ssh/ssh_host_ed25519_key.pub`, o agente roda como root no host da VM); sem arquivo → `host_key` ausente e log de erro (o passo continua ok).
- Satélite: comando `set_sync_host_key(host: str, key: str) -> {"ok": bool, "error"?: str}`; valida `key` com o mesmo regex de chave pública do servidor e `host` como hostname/IP; grava `~/.ssh/known_hosts_satsync` (`<host> <tipo> <base64>`, 0600, atômico). `provision_info()` ganha `sync_host_known: bool`.
- Integração: `_step_sync_key` envia `set_sync_host_key` com `host` = hostname do servidor que o satélite usa (o mesmo de `endereco_friday`; o satélite pode resolver sozinho se `host` vier vazio — decisão do implementador, documentada) e `key` = `host_key`. Para satélites já pareados: quando um satélite conecta e `provision_info().sync_host_known` é falso, rodar `_step_sync_key` de novo (idempotente), sem refazer os outros passos.
- `sync_recordings.sh`: `-o UserKnownHostsFile=$HOME/.ssh/known_hosts_satsync -o StrictHostKeyChecking=yes`; sem o arquivo, sai com rc 3 e mensagem clara (não tenta); com o envio inteiro OK, `touch $HOME/.friday_sync_ok` (fora de `gravacoes/`).

- [ ] **Step 1: testes que falham** cobrindo: retorno do `host_key` (com arquivo de teste por env) e ausência tolerada; `set_sync_host_key` válido grava o arquivo, inválido (chave malformada, host vazio, host com espaço) recusa e mantém o arquivo anterior; `provision_info` reflete `sync_host_known`; integração repassa a chave no pareamento e na reconexão de satélite sem chave, e não repassa quando `sync_host_known` é verdadeiro; script: sem `known_hosts_satsync` → rc 3 sem chamar rsync; com ele → opções de ssh presentes e carimbo tocado só com rc 0.
- [ ] **Step 2:** ver falhar.
- [ ] **Step 3:** implementar. Ler como o satélite resolve o host hoje (`sync_recordings.sh`, linhas do `HOST=`) e usar a mesma fonte.
- [ ] **Step 4:** testes + `scripts/test-all.sh` passam.
- [ ] **Step 5:** commit `fix(satellite): pin the VM host key for the nightly upload`.

### Task 4: VM — todos os satélites no cache e no sensor (Opus)

**Files:**
- Modify: `tools/wakeword/wwt/featcache_nightly.sh`, `tools/wakeword/wwt/install.sh`
- Create: `tools/wakeword/wwt/friday-wwt-featcache.service`, `tools/wakeword/wwt/friday-wwt-featcache.timer` (as units passam a morar no repo; o `.path` é gerado pelo `install.sh`)
- Modify: `tools/wakeword/training/featcache_build.sh`, `tools/wakeword/training/vad_gate_rerun.sh`, `tools/wakeword/training/ablation_eval.sh`, `ablation_report.py`, `replay_sexta.py` e afins que leem `amb<data>`
- Modify: `agents/server/friday_server_agent/__main__.py`, `collectors.py`
- Test: `tests/tools/test_featcache_nightly.py` (novo; docker/oc falsos no PATH, diretórios temporários por env), `tests/tools/test_install_scripts.py`, `tests/agents/test_server_collectors*.py`

**Interfaces:**
- `featcache_nightly.sh` lê `FRIDAY_WAKEWORD_INCOMING` (padrão `/srv/wakeword-data/incoming`), `FRIDAY_WWT_CACHE` (padrão `/srv/wwt-featcache`), `FRIDAY_WWT_STATE` (padrão `/srv/friday/state`). Para cada `incoming/<sat>/`: retenção de 30 dias do áudio, cache de cada dia anterior a hoje sem `amb<AAAAMMDD>_<sat>.json`, limpeza do cache sem áudio do mesmo satélite; ao fim do satélite (sem falha), grava em `$FRIDAY_WWT_STATE/wwt-featcache.json` `{"<sat>": "<ISO agora>"}` (merge, atômico). Diretório sem `gravacoes/ambiente` conta como passagem OK. Ao fim de todos, `touch wwt-featcache.ok` como hoje. Falha de um dia: avisa no Telegram como hoje e para (sem carimbo global).
- `install.sh`: (a) renomeia uma vez `amb<AAAAMMDD>.*` → `amb<AAAAMMDD>_<FRIDAY_SAT_OFFICE>.*` em `$FRIDAY_WWT_CACHE` (idempotente; lê `local.env`); (b) instala service e timer (`OnCalendar=*-*-* 07:00`, `Persistent=true`) e gera `friday-wwt-featcache.path` com um `PathChanged=<incoming>/<sat>/.sync_ok` por diretório existente, em `${FRIDAY_SYSTEMD_DIR:-/etc/systemd/system}`; `systemctl daemon-reload` e `enable --now` do path e do timer só quando `FRIDAY_SYSTEMD_DIR` não foi trocado (testes). Conferir como `scripts/deploy.sh wwt`/OTA roda o `install.sh` (root?) antes de escrever em `/etc`.
- Server agent: sai `wakeword_sync_last_ok` (descritor, métrica e `WAKEWORD_SYNC_STAMP`); `wakeword_cache_last_ok` continua e ganha `wakeword_cache_last_ok_attributes = {"satellites": {<sat>: <ISO>}}` lido de `wwt-featcache.json` (arquivo ausente/inválido → `{}`).
- `featcache_build.sh`, `vad_gate_rerun.sh`: `--sat <sat_id>` (padrão `FRIDAY_SAT_OFFICE`). Ferramentas que listam caches aceitam `amb<data>_<sat>` e mantêm o legado `amb<data>` (= MK1).

- [ ] **Step 1: testes que falham:** dois satélites com áudio em dias diferentes → caches com os nomes novos, um por satélite e dia; satélite sem `gravacoes/ambiente` → passagem registrada, sem erro; retenção por satélite; registro por satélite e carimbo global; falha simulada do extrator num satélite → sem carimbo global e sem entrada nova desse satélite; `install.sh` renomeia legado, é idempotente e gera um `PathChanged` por satélite; sensor do servidor sem `wakeword_sync_last_ok` e com o atributo `satellites`.
- [ ] **Step 2:** ver falhar.
- [ ] **Step 3:** implementar (um dia por vez, `nice 19`, como hoje).
- [ ] **Step 4:** testes + `scripts/test-all.sh` passam.
- [ ] **Step 5:** commit `feat(wakeword): nightly cache and health for every satellite`.

### Task 5: Origem no dataset (Opus)

**Files:**
- Modify: `tools/wakeword/training/harvest.sh`, `harvest_list_pi.py`, `tools/wakeword/analysis/run.sh`
- Modify: `tools/wakeword/training/build_dataset.py`, `dataset_rules.py`, `carry_over.py`, e o que mais compara nomes de clipe entre ciclos
- Test: `tools/wakeword/training/test_harvest_lib.py`, testes de `build_dataset`/`carry_over` existentes ou novos ao lado

**Interfaces:**
- `harvest.sh <modelo> <work> [--sat <sat_id>] [--host <ip>]` e `analysis/run.sh --since AAAAMMDD [--sat <sat_id>] [--host <ip>]`: endereço da Pi = `--host`, senão `ip` de `<sat_id>` em `/share/friday/satellites.json`, senão (sem `--sat`) o `/config/.friday/mk1` de hoje. Sem `--sat`, o `sat_id` é `FRIDAY_SAT_OFFICE` (`local.env`).
- Todo arquivo copiado da Pi para o work recebe `<sat_id>__` no nome; o manifesto do ciclo ganha `"sat"` por clipe.
- Função única `clip_key(name: str, office: str) -> str` (em `dataset_rules.py`): `<sat>__X` → `<sat>__X`; `X` sem prefixo → `<office>__X`. Toda comparação de identidade de clipe entre ciclos (`--pin-from`, `carry_over.py`, regras de vazamento) usa `clip_key`.

- [ ] **Step 1: testes que falham:** `clip_key` nos dois formatos; clipe legado `20261015_180746.wav` em prova num manifesto antigo e `sat-0a1b2c__20261015_180746.wav` num novo (office `sat-0a1b2c`) → mesmo lado; `sat-1d2e3f__20261015_180746.wav` → clipe diferente; manifesto novo traz `sat`; `carry_over.py` acusa vazamento (rc 3) entre legado e prefixado do mesmo clipe em lados opostos.
- [ ] **Step 2:** ver falhar.
- [ ] **Step 3:** implementar.
- [ ] **Step 4:** testes passam (`cd tools/wakeword/training && python3 -m pytest -q` ou o comando usado no repo) + `scripts/test-all.sh`.
- [ ] **Step 5:** commit `feat(wakeword): carry the satellite id into harvested clips and cycles`.

### Task 6: Card com MK e filtro por satélite (Sonnet)

**Files:**
- Modify: `custom_components/friday/frontend/friday-activations-card.js`, `activations-format.js`
- Test: `tests/frontend/activations-format.test.mjs`

**Interfaces:** em `activations-format.js`:
- `activationEntities(states: object) -> string[]` — todas as `event.friday_*_activation`, ordenadas.
- `satelliteLabel(hass, entityId) -> string` — `name_by_user` do device, senão `name`, senão o `entityId`.
- `rowsFromHistory` mantém `entityId` em cada linha (já mantém).

- [ ] **Step 1: testes que falham:** `activationEntities` acha duas entidades e ignora `event.friday_sat_x_timer`; `satelliteLabel` prefere `name_by_user`, cai para `name`, depois `entityId`.
- [ ] **Step 2:** ver falhar (`node --test tests/frontend/`).
- [ ] **Step 3:** card: `entity` opcional (sem ela, `activationEntities(hass.states)` a cada `hass` novo, recarregando se a lista mudar); selo com `satelliteLabel` em cada linha; com mais de uma entidade, fileira de chips por satélite (todos ligados no início) que filtra as linhas; `getStubConfig` devolve `{}`.
- [ ] **Step 4:** `node --test tests/frontend/` passa.
- [ ] **Step 5:** commit `feat(frontend): activations card shows and filters by satellite`.

### Task 7: Config da casa e docs (Claude principal, depois do release)

Não é do repo; feita só depois do release instalado.

- [ ] `/config/packages/friday_wakewords.yaml`: remover `input_boolean.friday_ambient_recording`, `rest_command.friday_satellite_ambient_set` e a automação `friday_ambient_recording_window` (`.bak.<data>` antes).
- [ ] `/config/packages/friday_producao.yaml` `wakeword_pipeline_late`: por satélite (`sensor.friday_sat_*_recordings_sync_last_ok`), cobrar envio > 26 h ou sem registro quando algum atributo `record_*` é verdadeiro, e sempre quando `unavailable`; cobrar cache quando `record_ambient` e a entrada do `sat_id` em `state_attr('sensor.friday_server_wakeword_cache_last_ok', 'satellites')` falta ou tem > 26 h. Push único com o nome do device. Atualizar `cd /config/tools/friday_voz && python3 -m unittest -q test_producao`.
- [ ] System Health: tirar `entity` do `custom:friday-activations-card`; `ui_lovelace_minimalist.reload`.
- [ ] Docs: `docs/claude/friday-producao.md` (pipeline das palavras por satélite), CLAUDE.md (linha do `friday_wakewords.yaml`), memória do projeto.
