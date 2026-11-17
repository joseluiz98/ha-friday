# Sensores de diagnóstico e CPU/memória como no Gerenciador de Tarefas — design

**Data:** 17/11/2026 · **Status:** aprovado pelo dono (17/11) · **Plano:**
`docs/superpowers/plans/2026-11-17-diagnostic-sensors.md`

## Problema

A página de cada aparelho da F.R.I.D.A.Y. no Home Assistant mistura o que um morador usa (mudo, volume, próximo
timer) com engenharia (temperatura da CPU, IP, backups do Proxmox). Quem abre o satélite para tirar o mudo vê
primeiro "Throttled" e "Up since". Além disso, "CPU load" no satélite e na VM é load average dividido pelos núcleos:
passa de 100%, conta tarefas presas em I/O e não é o que alguém espera de "CPU".

## Decisões do dono (17/11)

1. **Regra, para satélite, VM e todo agente ou integração futuros:** a seção principal só tem o que um morador usa ou
   quer ver; hardware, rede, versões e manutenção/infra vão para **Diagnóstico**. Um teste do repositório garante a
   regra: todo descriptor de todo agente declara `category` ou está numa lista explícita de chaves de usuário (por
   tipo de agente). Um descriptor novo que esquecer a categoria quebra o CI. A regra fica escrita no repositório, onde
   quem escreve agente vai ler.
2. **Satélite, fica na principal:** `mute`, `do_not_disturb`, `volume`, `next_timer`, `next_reminder`, eventos
   (`activation`, `timer`, `reminder`), `room_temperature`, `room_humidity`. **Vai para Diagnóstico:** uso de CPU,
   temperatura da CPU, `throttled`, `undervoltage_now`, `heat_limited`, uso de memória, `sd_usage`, `uptime_since`,
   `ip_address`, `network_interface`, `wifi_signal`, `program_state` (os que já são diagnóstico continuam).
3. **Servidor, fica na principal:** switch `voice`, `cost_today_brl`, `cost_month_brl`, `cost_month_projection_brl`,
   `cost_month_over_budget`, botão `restart_friday`. **Vai para Diagnóstico:** CPU/disco/memória da VM, memória e
   temperatura do NVMe do host Proxmox, `last_backup`, `backup_oldest_guest`, `backup_storage_used_pct`,
   `image_latest`, `lucid_last_run`, `brain_last_dream`, `brain_ready`, `conversation_open`, botão `reboot_vm`.
4. **CPU como no Gerenciador de Tarefas do Windows:** o "CPU load" por load average sai do satélite e da VM e entra
   **"CPU Usage"** (nome exato): % do tempo ocupado, média de todos os núcleos, pela diferença de `/proc/stat` entre
   duas amostras, com iowait contado como ocioso, sempre entre 0 e 100. A primeira amostra depois de iniciar não tem
   diferença: fica desconhecida (None), nunca 0. O load average sai do valor principal; pode sobreviver só como
   atributo do CPU Usage.
5. **Memória como no Windows:** **"Memory Usage"** (nome exato) em % = (MemTotal − MemAvailable) / MemTotal, no
   satélite (substitui `memory_available` em MB) e na VM (a fórmula já é essa; muda o nome). A memória do host Proxmox
   também vira % usado. Os valores em MB viram atributos. "CPU Usage" e "Memory Usage" são as palavras do dono; os
   outros nomes seguem em inglês.
6. **Contador de subtensão novo (satélite):** quantas mensagens "Undervoltage detected" o kernel registrou desde o
   boot, `state_class: total_increasing` (as estatísticas do HA somam entre reboots: histórico permanente), em
   Diagnóstico. Vale para MK1, MK2 e os próximos.
7. **Entrega:** pelo release OTA alpha normal (agentes e integração juntos); nada de deploy manual. Merge e tag só com
   o OK do dono.

## Desenho

### Onde a categoria é decidida

A integração já traduz `descriptor.category` em `EntityCategory` (`custom_components/friday/entity.py`). Então a
mudança de seção é dado do agente, não código da integração. Exceções que a própria integração cria: `reboot_vm`
(`RebootVmButton`, passa a `DIAGNOSTIC`), `next_reminder`/`reminder` e o clima do cômodo (continuam na principal),
`restart_pending` (já diagnóstico).

**O HA atualiza a categoria de entidade já registrada.** Conferido no código do HA 2026.10.4 (a versão dos testes):
`EntityPlatform` chama `entity_registry.async_get_or_create(..., entity_category=entity.entity_category)`, e para um
`unique_id` existente esse método repassa `entity_category` a `_async_update_entity`. Logo, mudar a categoria no
descriptor move a entidade sem migração; um teste de integração prova isso na nossa versão.

### CPU Usage

Cálculo comum aos dois agentes, em `agents/common/friday_agent_core/cpu.py`: linha agregada `cpu` de `/proc/stat`,
total = `user..steal` (os 8 primeiros campos; `guest`/`guest_nice` já estão dentro de `user`/`nice`), ocioso =
`idle + iowait`, uso = 100 × Δ(total − ocioso) / Δtotal, arredondado a 0,1 e limitado a 0–100. Δtotal ≤ 0 ou linha
ilegível → None. `steal` conta como ocupado, igual ao `sensor.cpu_utilizacao` da casa.

### Identidade das entidades (decisão minha)

Mudar a **semântica** de uma entidade com `state_class: measurement` mistura, nas estatísticas de longo prazo, load
average antigo e uso novo no mesmo gráfico, e mudar a unidade (MB → %) faz o HA recusar as estatísticas até alguém
corrigir à mão. Por isso:

| Antes (chave) | Depois (chave) | Como |
|---|---|---|
| satélite `cpu_load` | `cpu_usage` "CPU Usage" | chave nova; a antiga vai para `RETIRED_KEYS` |
| satélite `memory_available` (MB), `memory_total` (MB) | `memory_usage` "Memory Usage" (%), MB nos atributos | chave nova; as duas antigas para `RETIRED_KEYS` |
| VM `vm_cpu_load_pct` | `vm_cpu_usage_pct` "CPU Usage" | chave nova; a antiga para `RETIRED_KEYS` |
| VM `vm_memory_pct` "VM memory" | mesma chave, "Memory Usage", MB nos atributos | mesma entidade: fórmula e unidade não mudam |
| host `host_available_mb` (MB) | `host_memory_usage_pct` "Proxmox host memory usage" (%), MB nos atributos | chave nova; a antiga para `RETIRED_KEYS` |

As estatísticas antigas ficam no banco como "entidade não existe mais" (Ferramentas do desenvolvedor →
Estatísticas): não apagamos nada; o dono limpa quando quiser. Nenhuma chave nova termina com uma chave aposentada
(`_remove_retired_entities` compara por sufixo do `unique_id`); um teste trava isso.

### Contador de subtensão

`journalctl -k -b -o cat --no-pager --show-cursor [--after-cursor C]` (o usuário do agente é do grupo `adm`),
contando linhas com `Undervoltage detected`. Leitura incremental pelo cursor, a cada amostra de 30 s. Estado
`{boot_id, cursor, count}` em `~/.friday-agent/undervoltage.json`: um restart do agente (todo OTA reinicia) retoma do
cursor em vez de recontar — uma recontagem menor, se o journald tiver girado, o HA leria como reset e somaria em
dobro. Boot novo (`/proc/sys/kernel/random/boot_id` diferente) zera. Sem permissão de ler o kernel log a saída vem
vazia e sem cursor: aí o valor é None (desconhecido), nunca 0.

### Compatibilidade durante a janela do OTA

O protocolo não muda (v1 já tem `category` e `<key>_attributes`).

- **Integração nova, agente velho:** o agente velho ainda anuncia `cpu_load` etc. sem categoria. A integração apaga o
  registro dessas chaves no setup e a plataforma o recria a partir do hello: a página fica como hoje até o agente
  atualizar. Quando o agente novo manda a lista nova de capacidades, a integração recarrega e o registro antigo some.
- **Integração velha, agente novo:** as chaves novas aparecem já em Diagnóstico (a integração velha já entende
  `category`); as antigas ficam "indisponíveis" até a integração nova chegar.

### Consumidores fora do repositório (`/config`)

| Onde | Usa | Mudança |
|---|---|---|
| `packages/friday_producao.yaml` `friday_ram_guard` | `sensor.friday_server_proxmox_host_available_memory` (MB) | gatilho `numeric_state` com `attribute: available_mb` de `sensor.friday_server_host_memory_usage_pct`; textos com `state_attr` |
| `ui_lovelace_minimalist/dashboard/home-views/system-health.yaml` (card do satélite) | `sensor.friday_sat_8295e1_cpu_load` | lê `cpu_usage`, com `cpu_load` de reserva enquanto o OTA não chega |
| `tools/friday_voz/test_producao.py` | — | asserção nova do gatilho da guarda |

Os demais consumidores (`brain_ready`, `vm_memory`, `nvme_temperature`, `lucid_dreaming_last_run`,
`proxmox_last_backup`, `backup_*`, `program_state`, `image_latest` no card de OTA) mantêm o `entity_id`: mudar de
seção não quebra automação nem dashboard.

## Fora do escopo

Apagar estatísticas antigas; mexer em `sensor.cpu_utilizacao` do HA; novas métricas além do contador de subtensão.

## Respostas do dono (17/11, depois do primeiro rascunho)

- **`room_pressure`:** suportada e mostrada exatamente como `room_temperature`/`room_humidity` (seção principal,
  mesmo tratamento de `device_class`/`state_class`); quem não quiser desativa na interface. Entra no allowlist de
  usuário do satélite.
- **Versão:** o release é **v0.3.8** (a última tag é v0.3.7; o bump anterior para 0.3.8 foi revertido).

## Liga/desliga que vira dois raios (decidido: opção A, 17/11)

**Sintoma:** às vezes um booleano da integração aparece como interruptor e às vezes como dois botões de raio
(ligar/desligar), e o dono detesta os raios. Evidência de 17/11: com o programa do MK1 parado (sem Jabra; o
`ExecCondition` só deixa o serviço subir com o Jabra), todos os switches do satélite (`mute`, `do_not_disturb`,
`ducking`, `whisper_mode`, `follow_up_mode`, `turn_sounds`) estão `unknown`, sem `assumed_state`, enquanto
`voice_enabled` (que o agente lê de um arquivo próprio) está `on`.

**O que o nosso código faz (conferido):** `FridaySwitch.is_on` devolve `None` quando a chave não está no estado →
o HA grava `unknown`; `available` = agente conectado e `descriptor.available` → continua `True`. Um switch
disponível com estado `unknown` é exatamente o caso da hipótese.

**Hipótese a confirmar no código do frontend da nossa versão:** a interface desenha os dois botões quando o estado é
`unknown` **ou** quando `assumed_state` é verdadeiro; com `unavailable` ela desenha o interruptor cinza e travado.

**Opções consideradas:**

| Opção | O que aparece com o programa parado | Custo |
|---|---|---|
| A. `unavailable` quando o valor falta (o switch diz `available = False` se a chave não está no estado ou se o programa está parado) | interruptor cinza, sem raios; automações veem `unavailable` | a pessoa não consegue mudar o ajuste enquanto o programa está parado (hoje o comando também não teria efeito) |
| B. manter o último valor conhecido (o agente guarda o último estado do programa e o reenvia) | interruptor no último valor | mostra um valor que pode não ser verdade; precisa de memória no agente |
| C. não mudar nada | dois raios | o que o dono não quer |

**Decisão do dono (17/11): A.** Um switch sem valor fica `unavailable` (cinza, sem raios). A regra vale para todo
booleano controlável que a integração expõe, inclusive no servidor (`voice`, `ota_auto_install`). Tarefa 10 do plano.

## "Voice Program" mostra a intenção, não o programa (decisão do dono em aberto)

**Achado (17/11):** o switch `voice_enabled` ("Voice Program") do satélite publica o arquivo de intenção
`~/.friday-agent/voice-enabled` (`agents/satellite/friday_satellite_agent/power.py`, lido em `__main__.py`), não se
o programa está rodando. Hoje ele mostra `on` com o programa parado: o `ExecCondition` do systemd (só com Jabra) pula
o serviço, e `program_state` está `off`. Quem olha a página acha que a voz está ligada.

**Opções (o dono decide):**

| Opção | O que muda | Custo |
|---|---|---|
| (a) o switch continua sendo a intenção, e um sinal de divergência aparece: atributo `running` no switch e um `binary_sensor` de problema "Voice program not running" (Diagnóstico), ligado quando a intenção é `on` e `program_state` está `off` há mais de N minutos | a intenção fica clara e a falha também | uma entidade a mais; escolher N |
| (b) o switch mostra se o programa roda de fato | o que se vê é a verdade | ao ligar com o programa impedido de subir, o switch volta sozinho para `off`, sem explicar por quê |

**Recomendação do coordenador:** (a). **Fora da v0.3.8**, a menos que o dono responda antes de a execução começar
(Tarefa 11 do plano, bloqueada).
