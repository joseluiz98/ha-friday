# Sensores de diagnóstico e CPU/memória "Gerenciador de Tarefas" — plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a página de cada aparelho da F.R.I.D.A.Y. mostra na seção principal só o que um morador usa; o resto vai para
Diagnóstico, com um teste que impede regressão; "CPU Usage" e "Memory Usage" em % como no Windows; contador de
subtensão no satélite.

**Architecture:** a seção vem de `category` no descriptor de cada agente (a integração já a traduz para
`EntityCategory`). Um módulo comum `friday_agent_core/cpu.py` calcula o uso de CPU por delta de `/proc/stat` para os
dois agentes. Chaves cuja semântica ou unidade muda ganham chave nova, e as antigas entram em `RETIRED_KEYS` da
integração.

**Tech Stack:** Python 3.13 (agentes, pytest), integração HA (pytest-homeassistant-custom-component 0.13.375, HA
2026.10.4), YAML do HA em `/config`.

**Spec:** `docs/superpowers/specs/2026-11-17-diagnostic-sensors-design.md` (ler antes de cada tarefa).

**Repo / branch:** `/config/dev/ha-friday-wt-diag`, branch `feat/diagnostic-sensors` (de `origin/master` 02d9dde).
Outras worktrees do mesmo repo são de outras sessões: não tocar.

## Global Constraints

- Nomes exatos: `"CPU Usage"` (satélite e VM), `"Memory Usage"` (satélite e VM), `"Proxmox host memory usage"`,
  `"Undervoltage events"`. Demais nomes não mudam.
- Chaves novas: satélite `cpu_usage`, `memory_usage`, `undervoltage_events`; servidor `vm_cpu_usage_pct`,
  `host_memory_usage_pct`. `vm_memory_pct` mantém a chave.
- Chaves aposentadas (para `RETIRED_KEYS`): `cpu_load`, `memory_available`, `memory_total`, `vm_cpu_load_pct`,
  `host_available_mb`.
- CPU: total = soma dos 8 primeiros campos da linha `cpu` (user nice system idle iowait irq softirq steal); ocioso =
  idle + iowait; uso = 100 × Δ(total − ocioso) / Δtotal, `round(…, 1)`, limitado a [0, 100]; primeira amostra, Δtotal
  ≤ 0 ou linha ilegível → `None`.
- Memória: % = (MemTotal − MemAvailable) / MemTotal × 100, `round(…, 1)`; atributos `available_mb` e `total_mb`
  (inteiros, MB = kB // 1024 no satélite e na VM; bytes // 1048576 no host).
- Atributos do CPU Usage: `load_1m`, `load_5m`, `load_15m` (floats crus de `/proc/loadavg`).
- Subtensão: `journalctl -k -b -o cat --no-pager --show-cursor`, linhas contendo `Undervoltage detected`;
  `state_class: "total_increasing"`, `category: "diagnostic"`, ícone `mdi:flash-alert`.
- Principal do satélite (allowlist exata): `volume`, `do_not_disturb`, `mute`, `activation`, `timer`, `next_timer`,
  `room_temperature`, `room_humidity`, `room_pressure` (pressão aprovada pelo dono em 17/11: mesmo tratamento de
  temperatura e umidade). `next_reminder` e
  `reminder` são criadas pela integração.
- Principal do servidor (allowlist exata): `voice`, `cost_today_brl`, `cost_month_brl`, `cost_month_projection_brl`,
  `cost_month_over_budget`, `restart_friday`.
- Git em inglês; `bash secret-sweep.sh` antes de commit; nunca `--no-verify`; nada da casa no repo.
- Testes: `FRIDAY_TEST_DIR=ha-friday-wt-diag bash scripts/test.sh <paths>` (roda na VM); suíte completa
  `FRIDAY_TEST_DIR=ha-friday-wt-diag bash scripts/test-all.sh`. Nunca rodar pytest pesado no HAOS.
- Nada de deploy manual, nada de `deploy.sh`: a entrega é o release OTA alpha, com o OK do dono.

## Review Focus

1. **Satélite sem permissão de ler o kernel log** (um satélite futuro sem o grupo `adm`): `journalctl` sai vazio e sem
   cursor; o contador deve ficar `None`, nunca 0 — teste na Tarefa 3.
2. **Restart do agente no meio do boot** (todo OTA reinicia): o contador retoma do cursor salvo e não reconta; boot
   novo zera — testes na Tarefa 3.
3. **Primeira amostra depois de iniciar**: CPU Usage desconhecido, não 0; e a chave não aparece no `sample()` do
   satélite (a entidade não recebe valor falso) — testes nas Tarefas 1 e 2.
4. **Contadores de `/proc/stat` que não andam ou voltam** (relógio, leitura truncada): `None`, nunca negativo nem >100 —
   teste na Tarefa 1.
5. **Sufixo de chave aposentada que pega chave viva** (`_remove_retired_entities` compara pelo fim do `unique_id`):
   nenhuma chave anunciada termina com `_<chave aposentada>` — teste na Tarefa 6.

---

### Task 1: Uso de CPU comum aos agentes

**Files:**
- Create: `agents/common/friday_agent_core/cpu.py`
- Test: `tests/agents/test_cpu_usage.py`

**Interfaces:**
- Produces:
  - `parse_proc_stat(text: str) -> tuple[int, int] | None` — `(busy, total)` da linha `cpu ` agregada.
  - `class CpuUsage` com `update(stat_text: str) -> float | None` (guarda a amostra anterior; a primeira chamada
    devolve `None`).
  - `parse_loadavg(text: str) -> dict | None` → `{"load_1m": float, "load_5m": float, "load_15m": float}`.

- [ ] **Step 1: Write the failing tests**

```python
A = "cpu  100 0 100 700 100 0 0 0 0 0\ncpu0 1 2 3 4 5 6 7 8 9 10\n"
B = "cpu  150 0 150 750 150 0 0 0 0 0\n"
def test_parse_proc_stat(): assert cpu.parse_proc_stat(A) == (200, 1000) and cpu.parse_proc_stat("x") is None
def test_first_sample_is_none_then_delta():
    u = cpu.CpuUsage(); assert u.update(A) is None; assert u.update(B) == 50.0
def test_iowait_counts_as_idle():
    u = cpu.CpuUsage(); u.update(A); assert u.update("cpu  100 0 100 700 300 0 0 0 0 0\n") == 0.0
def test_guest_not_double_counted():  # guest/guest_nice fields ignored
    u = cpu.CpuUsage(); u.update(A); assert u.update("cpu  150 0 150 750 150 0 0 0 999 999\n") == 50.0
def test_stalled_or_backwards_counters_are_none():
    u = cpu.CpuUsage(); u.update(B); assert u.update(B) is None; assert u.update(A) is None
def test_unreadable_line_keeps_previous_sample():
    u = cpu.CpuUsage(); u.update(A); assert u.update("garbage") is None; assert u.update(B) == 50.0
def test_parse_loadavg():
    assert cpu.parse_loadavg("1.36 1.22 1.16 2/240 1234\n") == {"load_1m": 1.36, "load_5m": 1.22, "load_15m": 1.16}
    assert cpu.parse_loadavg("") is None
```

- [ ] **Step 2: Run** `FRIDAY_TEST_DIR=ha-friday-wt-diag bash scripts/test.sh tests/agents/test_cpu_usage.py` — FAIL
  (module missing).
- [ ] **Step 3: Implement** `cpu.py` com as fórmulas de Global Constraints. Resultado limitado com
  `max(0.0, min(100.0, …))` antes do `round`. Linha ilegível não substitui a amostra anterior.
- [ ] **Step 4: Run** o mesmo comando — PASS.
- [ ] **Step 5: Commit** `feat(agents): shared CPU usage from /proc/stat deltas`

---

### Task 2: Satélite — CPU Usage, Memory Usage e categorias do hardware

**Files:**
- Modify: `agents/satellite/friday_satellite_agent/hardware.py` (`sample`, `descriptors`, remover `parse_cpu_load`)
- Modify: `agents/satellite/friday_satellite_agent/__main__.py:333-341` (`hardware_loop`)
- Test: `tests/agents/test_satellite_hardware.py`

**Interfaces:**
- Consumes: `friday_agent_core.cpu.CpuUsage`, `parse_loadavg` (Task 1).
- Produces:
  - `STAT = "/proc/stat"` em `hardware.py`.
  - `parse_memory_usage(meminfo: str) -> tuple[float, dict] | None` → `(pct, {"available_mb", "total_mb"})`.
  - `sample(..., cpu: CpuUsage | None = None)` (parâmetro novo, só palavra-chave; `None` = sem CPU Usage). Chaves
    novas: `cpu_usage`, `cpu_usage_attributes`, `memory_usage`, `memory_usage_attributes`. Saem `cpu_load`,
    `memory_available`, `memory_total`.
  - `hardware_loop` cria um único `CpuUsage()` fora do laço e o passa a cada `hw.sample_async(..., cpu=cpu_usage)`.

- [ ] **Step 1: Write the failing tests** (ajustar os existentes que citam `cpu_load`/`memory_*`; `LEGACY` perde
  `memory_available`/`memory_total`)

```python
def test_memory_usage_like_windows():
    pct, attrs = hw.parse_memory_usage(FILES[hw.MEMINFO])
    assert pct == 41.5 and attrs == {"available_mb": 531, "total_mb": 907}
    assert hw.parse_memory_usage("") is None
def test_sample_cpu_usage_needs_two_samples():
    files = dict(FILES, **{hw.STAT: "cpu  100 0 100 700 100 0 0 0 0 0\n"})
    u = CpuUsage()
    first = hw.sample(read=files.__getitem__, run=fake_run(""), now=lambda: NOW, cpu=u, with_network=False)
    assert "cpu_usage" not in first
    files[hw.STAT] = "cpu  150 0 150 750 150 0 0 0 0 0\n"
    second = hw.sample(read=files.__getitem__, run=fake_run(""), now=lambda: NOW, cpu=u, with_network=False)
    assert second["cpu_usage"] == 50.0
    assert second["cpu_usage_attributes"] == {"load_1m": 2.0, "load_5m": 1.5, "load_15m": 1.0}
def test_hardware_descriptors_names_and_categories():
    d = {x["key"]: x for x in hw.descriptors(network=True)}
    assert d["cpu_usage"]["name"] == "CPU Usage" and d["cpu_usage"]["unit"] == "%"
    assert d["cpu_usage"]["state_class"] == "measurement"
    assert d["memory_usage"]["name"] == "Memory Usage" and d["memory_usage"]["unit"] == "%"
    assert not {"cpu_load", "memory_available", "memory_total"} & set(d)
    assert all(x.get("category") in ("config", "diagnostic") for x in d.values())
```

  `test_sample_wifi_only`: o dicionário esperado troca `"cpu_load": 50.0` por `"memory_usage": 41.5,
  "memory_usage_attributes": {"available_mb": 531, "total_mb": 907}` (sem `cpu` passado não há `cpu_usage`).
  `test_sample_survives_missing_sources`: o conjunto esperado (linha ~109) troca `cpu_load` por nada.

- [ ] **Step 2: Run** `FRIDAY_TEST_DIR=ha-friday-wt-diag bash scripts/test.sh tests/agents/test_satellite_hardware.py` — FAIL.
- [ ] **Step 3: Implement.** Em `descriptors()`: `cpu_usage` (`"CPU Usage"`, `%`, `mdi:cpu-64-bit`, measurement),
  `memory_usage` (`"Memory Usage"`, `%`, `mdi:memory`, measurement); `"category": "diagnostic"` em `cpu_temperature`,
  `cpu_usage`, `throttled`, `uptime_since`, `undervoltage_now`, `heat_limited`, `memory_usage`, `sd_usage`,
  `network_interface`, `ip_address`, `wifi_signal`. `/proc/stat` só é lido quando `cpu` foi passado; `cpu_usage` só entra em `out` quando não é `None`
  (regra atual do `attempt`). Docstring de módulo: CPU Usage = Gerenciador de Tarefas, load average só no atributo.
- [ ] **Step 4: Run** o mesmo comando e `tests/agents/test_satellite_capabilities.py` — PASS.
- [ ] **Step 5: Commit** `feat(satellite): CPU Usage and Memory Usage like Task Manager; hardware to Diagnostic`

---

### Task 3: Satélite — contador de subtensão

**Files:**
- Modify: `agents/satellite/friday_satellite_agent/hardware.py`
- Modify: `agents/satellite/friday_satellite_agent/__main__.py` (`hardware_loop`)
- Test: `tests/agents/test_satellite_hardware.py`

**Interfaces:**
- Produces:
  - `BOOT_ID = "/proc/sys/kernel/random/boot_id"`, `UNDERVOLTAGE_STATE = os.path.expanduser("~/.friday-agent/undervoltage.json")`.
  - `parse_journal(text: str) -> tuple[int, str | None]` → (linhas com `Undervoltage detected`, cursor da última linha
    `-- cursor: <c>` ou `None`).
  - `class UndervoltageCounter(run=_run, read=_read_file, path=UNDERVOLTAGE_STATE)` com `sample() -> int | None`.
  - `sample(..., undervoltage: UndervoltageCounter | None = None)` → chave `undervoltage_events`.
  - Descriptor `{"key": "undervoltage_events", "platform": "sensor", "name": "Undervoltage events",
    "state_class": "total_increasing", "icon": "mdi:flash-alert", "category": "diagnostic"}`.

- [ ] **Step 1: Write the failing tests** (`run` falso que registra o argv; `read` falso para o boot_id; `tmp_path`)

```python
J1 = "x\nhwmon hwmon1: Undervoltage detected!\ny\nhwmon hwmon1: Undervoltage detected!\n-- cursor: s=1;i=10\n"
J2 = "hwmon hwmon1: Undervoltage detected!\n-- cursor: s=1;i=11\n"
def test_parse_journal(): assert hw.parse_journal(J1) == (2, "s=1;i=10") and hw.parse_journal("") == (0, None)
def test_counts_incrementally_after_cursor(tmp_path):
    # 1st call argv has no --after-cursor -> 2; 2nd call argv ends with ["--after-cursor", "s=1;i=10"] -> 3
def test_no_new_lines_keeps_count_and_cursor(tmp_path):  # run returns "" after the first read -> still 2
def test_unreadable_kernel_log_is_none_not_zero(tmp_path):  # run returns "" on the first read -> None
def test_resumes_after_agent_restart(tmp_path):
    # counter A reads J1 (2); new counter B, same path and boot_id: first argv carries --after-cursor s=1;i=10,
    # reads J2 -> 3 (never re-reads from boot)
def test_new_boot_starts_from_zero(tmp_path):  # saved boot_id differs -> full read without cursor -> count of J1 only
def test_run_failure_is_none(tmp_path):  # run raises -> sample() is None, state file untouched
```

  E: `hw.descriptors(network=False)` contém `undervoltage_events` com os campos exatos acima.

- [ ] **Step 2: Run** `FRIDAY_TEST_DIR=ha-friday-wt-diag bash scripts/test.sh tests/agents/test_satellite_hardware.py` — FAIL.
- [ ] **Step 3: Implement.** argv base `["journalctl", "-k", "-b", "-o", "cat", "--no-pager", "--show-cursor"]`, mais
  `["--after-cursor", cursor]` quando há cursor do mesmo boot. Sem cursor salvo e saída sem cursor → `None`. Com
  cursor salvo e saída vazia → contagem atual (nada novo). Grava `{"boot_id", "cursor", "count"}` só quando o cursor
  anda (escrita atômica: arquivo `.tmp` + `os.replace`); arquivo corrompido = começar do zero. `hardware_loop` cria
  um `UndervoltageCounter()` fora do laço.
- [ ] **Step 4: Run** o mesmo comando — PASS.
- [ ] **Step 5: Commit** `feat(satellite): count kernel undervoltage events since boot`

---

### Task 4: Servidor — CPU Usage, Memory Usage, memória do host em % e categorias

**Files:**
- Modify: `agents/server/friday_server_agent/collectors.py` (`vm_metrics`, `proxmox_metrics`, `capabilities`)
- Modify: `agents/server/friday_server_agent/__main__.py:88-97` (`sample_vm`)
- Test: `tests/agents/test_server_collectors.py`

**Interfaces:**
- Consumes: `friday_agent_core.cpu.CpuUsage`, `parse_loadavg` (Task 1).
- Produces:
  - `vm_metrics(meminfo, loadavg, statvfs, uptime, ncpu, cpu_pct: float | None = None) -> dict` — chaves
    `vm_memory_pct`, `vm_memory_pct_attributes`, `vm_cpu_usage_pct`, `vm_cpu_usage_pct_attributes`, `vm_disk_pct`,
    `vm_uptime_since`. `vm_cpu_load_pct` sai. `ncpu` fica na assinatura (o `Source` de `__main__` chama com
    argumentos posicionais fictícios).
  - `proxmox_metrics(...)` → `host_memory_usage_pct` + `host_memory_usage_pct_attributes`
    (`{"available_mb", "total_mb"}`), no lugar de `host_available_mb`. Sem `total` ou sem `available`/`free` → os
    dois `None`.
  - `sample_vm` usa um `CpuUsage()` de módulo e `_proc("stat")`.

- [ ] **Step 1: Write the failing tests** (atualizar os existentes)

```python
m = c.vm_metrics(MEMINFO, "2.00 1.0 0.5 1/100 42", (4096, 1000, 400, 400), "86400.5 100.0", 4, cpu_pct=12.5)
assert m["vm_memory_pct"] == 75.0 and m["vm_memory_pct_attributes"] == {"available_mb": 976, "total_mb": 3906}
assert m["vm_cpu_usage_pct"] == 12.5
assert m["vm_cpu_usage_pct_attributes"] == {"load_1m": 2.0, "load_5m": 1.0, "load_15m": 0.5}
assert "vm_cpu_load_pct" not in m
assert c.vm_metrics(MEMINFO, "0 0 0", (1, 1, 1, 1), "1 1", 1)["vm_cpu_usage_pct"] is None
node = {"data": {"memory": {"total": 8589934592, "available": 2097152000}}}
p = c.proxmox_metrics(node, {}, {})
assert p["host_memory_usage_pct"] == 75.6
assert p["host_memory_usage_pct_attributes"] == {"available_mb": 2000, "total_mb": 8192}
assert c.proxmox_metrics({"data": {"memory": {"available": 1}}}, {}, {})["host_memory_usage_pct"] is None
caps = {d["key"]: d for d in c.capabilities(proxmox=True)}
assert caps["vm_cpu_usage_pct"]["name"] == "CPU Usage" and caps["vm_memory_pct"]["name"] == "Memory Usage"
assert caps["host_memory_usage_pct"]["name"] == "Proxmox host memory usage"
assert caps["host_memory_usage_pct"]["unit"] == "%" and caps["host_memory_usage_pct"]["available"] is True
assert not {"vm_cpu_load_pct", "host_available_mb"} & set(caps)
```

  `test_proxmox_metrics_maps_fields` e o `empty == {...}` trocam `host_available_mb` pelas duas chaves novas;
  `test_proxmox_unavailable_without_token` usa `host_memory_usage_pct`.

- [ ] **Step 2: Run** `FRIDAY_TEST_DIR=ha-friday-wt-diag bash scripts/test.sh tests/agents/test_server_collectors.py` — FAIL.
- [ ] **Step 3: Implement.** Em `capabilities()`: `**diag` em `vm_memory_pct`, `vm_cpu_usage_pct`, `vm_disk_pct`,
  `brain_ready`, `brain_last_dream`, `lucid_last_run`, `conversation_open`, `host_memory_usage_pct`,
  `nvme_temperature`, `last_backup`, `backup_oldest_guest`, `backup_storage_used_pct`, `image_latest`, e
  `category="diagnostic"` em `ota_server` (o `update.py` da integração já força diagnóstico; o descriptor passa a
  dizer o mesmo). Ícones: `mdi:cpu-64-bit` (CPU), `mdi:memory` (memórias).
- [ ] **Step 4: Run** o mesmo comando e `tests/agents/test_server_commands.py` — PASS.
- [ ] **Step 5: Commit** `feat(server): CPU Usage and Memory Usage like Task Manager; infra sensors to Diagnostic`

---

### Task 5: Regra de posicionamento — teste-guarda, categorias restantes e documentação

**Files:**
- Create: `tests/agents/test_entity_placement.py`
- Modify: `agents/satellite/friday_satellite_agent/capabilities.py` (`program_state` → diagnóstico)
- Modify: `agents/satellite/friday_satellite_agent/ota.py:281` (`KEY` update → `"category": "diagnostic"`)
- Modify: `docs/protocol.md` (seção nova "Entity placement" logo após "### Descriptor"), `README.md` (uma linha em
  Development apontando para ela)

**Interfaces:**
- Consumes: descriptors das Tarefas 2–4.
- Produces: `SATELLITE_MAIN`, `SERVER_MAIN` (frozensets, valores de Global Constraints) e
  `all_descriptors(kind: str) -> list[dict]` no próprio teste.

- [ ] **Step 1: Write the failing test**

```python
def all_descriptors(kind):
    # satellite: cap.controls_from_config(CFG, WW) with wifi.nmcli_available monkeypatched True
    #   + cap.room_descriptors(cap.ROOM_DESCRIPTORS) + Equalizer with available=True + ota.OtaClient.descriptors()
    #   + hw.descriptors(network=True)  (dedupe by key)
    # server: c.capabilities(proxmox=True)
@pytest.mark.parametrize("kind,main", [("satellite", SATELLITE_MAIN), ("server", SERVER_MAIN)])
def test_every_descriptor_is_placed(kind, main):
    shown = [d for d in all_descriptors(kind) if not d.get("hidden")]
    bad = [d["key"] for d in shown
           if (d["key"] in main) == (d.get("category") in ("config", "diagnostic"))]
    assert bad == []          # main keys have no category; every other key declares one
    assert main <= {d["key"] for d in shown}   # the allowlist never goes stale    if kind == "satellite":   # 17/11: pressure shown exactly like temperature and humidity
        by = {d["key"]: d for d in shown}
        assert by["room_pressure"]["state_class"] == by["room_humidity"]["state_class"] == "measurement"
        assert "category" not in by["room_pressure"] and by["room_pressure"]["device_class"] == "pressure"
```

  A mensagem do `assert` (comentário no topo do arquivo) explica a regra e aponta `docs/protocol.md#entity-placement`.

- [ ] **Step 2: Run** `FRIDAY_TEST_DIR=ha-friday-wt-diag bash scripts/test.sh tests/agents/test_entity_placement.py` —
  FAIL listando `program_state` e `ota`.
- [ ] **Step 3: Implement** as duas categorias e a documentação. Texto da regra em `docs/protocol.md` (inglês, como
  o resto do arquivo): a seção principal só tem o que um morador usa ou quer ver; hardware, rede, versões e
  manutenção/infra vão para `"diagnostic"`; ajustes vão para `"config"`; uma chave nova sem categoria precisa entrar
  no allowlist de `tests/agents/test_entity_placement.py`, senão o CI falha; entidades que a integração cria sozinha
  seguem a mesma regra (teste em `tests/integration/test_entity_placement.py`, Tarefa 6).
- [ ] **Step 4: Run** o mesmo comando e `tests/agents/test_satellite_capabilities.py tests/agents/test_satellite_ota.py` — PASS.
- [ ] **Step 5: Commit** `test(agents): every descriptor declares its section; document the placement rule`

---

### Task 6: Integração — chaves aposentadas, `reboot_vm` em Diagnóstico, prova da mudança de categoria

**Files:**
- Modify: `custom_components/friday/__init__.py:84-89` (`RETIRED_KEYS`)
- Modify: `custom_components/friday/button.py` (`RebootVmButton`: `_attr_entity_category = EntityCategory.DIAGNOSTIC`)
- Create: `tests/integration/test_entity_placement.py`
- Modify: `tests/integration/test_general_settings.py::test_retired_entities_removed`

**Interfaces:**
- Consumes: as chaves aposentadas e as allowlists (Global Constraints).

- [ ] **Step 1: Write the failing tests**
  - `test_retired_entities_removed`: pré-criar também `("sensor", "sat1_cpu_load")`, `("sensor", "sat1_memory_available")`,
    `("sensor", "sat1_memory_total")`, `("sensor", "server_vm_cpu_load_pct")`, `("sensor", "server_host_available_mb")`;
    depois do setup nenhum deles resta.
  - `test_existing_entity_moves_to_diagnostic` (novo arquivo): registro pré-criado `("sensor", "sat1_cpu_temperature")`
    com `entity_category=None`; hello com o descriptor de `cpu_temperature` com `category: "diagnostic"`; após o setup,
    `reg.async_get(...).entity_category is EntityCategory.DIAGNOSTIC` e o `entity_id` é o mesmo de antes.
  - `test_integration_built_entities_placement`: com Proxmox configurado, `button.friday_<server>_reboot_vm` tem
    `entity_category == DIAGNOSTIC`; `next_reminder`, `reminder` e `room_temperature` do satélite têm `None`;
    `restart_pending` tem `DIAGNOSTIC`. Reaproveitar os helpers de `tests/integration/conftest.py` e de
    `test_proxmox.py` / `test_reminders.py`.
  - `test_no_live_key_ends_with_a_retired_key`: para cada chave das allowlists e dos descriptors da Tarefa 5
    (importar `all_descriptors` de `tests/agents/test_entity_placement.py` ou repetir a coleta), nenhuma termina com
    `_<k>` nem é igual a `k` para `k` em `RETIRED_KEYS`.
- [ ] **Step 2: Run** `FRIDAY_TEST_DIR=ha-friday-wt-diag bash scripts/test.sh tests/integration/test_entity_placement.py tests/integration/test_general_settings.py` — FAIL (aposentadas e `reboot_vm`; o teste de mudança de categoria deve
  passar já — ele documenta o comportamento do HA; se falhar, parar e reportar: o desenho do spec depende dele).
- [ ] **Step 3: Implement** `RETIRED_KEYS` com comentário `# 17/11: semantics or unit changed (spec 2026-11-17-diagnostic-sensors)`
  e a categoria do `RebootVmButton`.
- [ ] **Step 4: Run** o mesmo comando e `tests/integration/test_proxmox.py` — PASS.
- [ ] **Step 5: Commit** `feat(integration): retire load-average and MB sensors; Reboot VM to Diagnostic`

---

### Task 7: Suíte completa, versão e PR pronto (merge e tag só com o OK do dono)

**Files:**
- Modify: `custom_components/friday/manifest.json` (`version`)

- [ ] **Step 1:** `git fetch origin && git merge origin/master` se a master andou (nunca rebase depois do push).
  Versão decidida pelo dono: **0.3.8** (última tag v0.3.7; o bump anterior foi revertido). Conferir com
  `gh release list -L 3` que v0.3.8 ainda não existe; se existir, parar e avisar. Commit
  `chore(release): bump manifest to v0.3.8`.
- [ ] **Step 2:** `bash secret-sweep.sh` e `FRIDAY_TEST_DIR=ha-friday-wt-diag bash scripts/test-all.sh` — esperado
  `ALL GREEN`.
- [ ] **Step 3:** push do branch e PR **pronto (não draft)** para `master` (inglês), aberto só com o CI verde e o
  `test-all.sh` em `ALL GREEN`. Regra do ha-friday com OTA: código de satélite só se testa ao vivo no pre-release
  alpha, que é tagueado na master depois do merge; por isso não há "Merge gate". A descrição traz o resumo, a tabela
  de chaves antigas → novas e a linha `Promotion gate (stable):` com as verificações ao vivo da prova de 48 h do alpha
  (as do Step 5 da Tarefa 8: seções das páginas, CPU Usage 0–100 nos dois aparelhos, contador de subtensão igual ao
  `journalctl` e estável após restart do agente, switches cinza sem raios com o programa parado, registros antigos
  removidos).
- [ ] **Step 4:** rascunhar as notas do release v0.3.8 no estilo do Home Assistant (no PR, para o dono revisar):
  destaques; mudanças por área (satélite, servidor, integração); **Breaking changes / Compatibility**: os `entity_id`
  de CPU Usage e Memory Usage mudam (`…_cpu_load` → `…_cpu_usage`, `…_memory_available` → `…_memory_usage`,
  `…_vm_cpu_load_pct` → `…_vm_cpu_usage_pct`, `…_host_available_mb` → `…_host_memory_usage_pct`), as estatísticas
  antigas ficam órfãs (Ferramentas do desenvolvedor → Estatísticas), sensores passam para Diagnóstico, e switches sem
  valor passam a `unavailable`. Terminar aí: merge e tag `v0.3.8` (pre-release, canal alpha) só com o OK do dono.

---

### Task 8: Depois do alpha instalado — consumidores em `/config` e prova ao vivo

Só depois do merge, da tag v0.3.8 (alpha) e da instalação na VM, na integração e no MK1. Os resultados do Step 5
  são o "Promotion gate (stable)" do PR. Estes arquivos são da
casa, fora do repo.

**Files:**
- Modify: `/config/packages/friday_producao.yaml` (`friday_ram_guard`)
- Modify: `/config/tools/friday_voz/test_producao.py`
- Modify: `/config/ui_lovelace_minimalist/dashboard/home-views/system-health.yaml` (card "Satellite", ~linha 340)
- Modify: `/config/docs/claude/friday-producao.md` (onde cita sensores renomeados)

- [ ] **Step 1: Write the failing test** em `test_producao.py`:

```python
def test_guarda_de_ram_le_o_atributo_em_mb(self):
    g = [t for t in gatilhos(automacoes()["friday_ram_guard"], "numeric_state")][0]
    self.assertEqual((g["entity_id"], g["attribute"], g["below"], g["for"]),
                     ("sensor.friday_server_host_memory_usage_pct", "available_mb",
                      "input_number.friday_ram_guard_host_min_mb", "00:30:00"))
    self.assertNotIn("proxmox_host_available_memory", str(automacoes()["friday_ram_guard"]))
```

- [ ] **Step 2: Run** `cd /config/tools/friday_voz && python3 -m unittest -q test_producao` — FAIL.
- [ ] **Step 3: Implement**: gatilho com `attribute: available_mb`; os três textos trocam
  `states('sensor.friday_server_proxmox_host_available_memory')` por
  `state_attr('sensor.friday_server_host_memory_usage_pct', 'available_mb')`. No card do satélite:
  `const cpu = parseFloat(st('sensor.' + p + 'cpu_usage') ?? st('sensor.' + p + 'cpu_load'));`. Conferir antes que os
  `entity_id` novos existem no registro (`sensor.friday_server_host_memory_usage_pct`,
  `sensor.friday_sat_8295e1_cpu_usage`); se o HA criou outro id, usar o real.
- [ ] **Step 4: Run** o teste — PASS; `automation.reload`; `ui_lovelace_minimalist.reload`; checar a config
  (`ha core check` ou a API `config/core/check_config`).
- [ ] **Step 5: Prova ao vivo** (sem custo de modelo pago):
  - Página do satélite e do servidor: na principal só os itens das allowlists (+ `next_reminder`/`reminder`/clima).
  - `sensor.friday_sat_8295e1_cpu_usage` e `sensor.friday_server_vm_cpu_usage_pct` entre 0 e 100, atributos `load_*`.
  - `ssh <user>@<mk1> 'journalctl -k -b -o cat --no-pager | grep -c "Undervoltage detected"'` igual a
    `sensor.friday_sat_8295e1_undervoltage_events` (± eventos entre as leituras); reiniciar o agente e conferir que o
    número não muda.
  - Registro: `cpu_load`, `memory_available`, `memory_total`, `vm_cpu_load`, `proxmox_host_available_memory`
    removidos; as estatísticas antigas ficam (não apagar).
- [ ] **Step 6:** atualizar a memória `project_friday_ha_integration` com as chaves novas e a regra; relatar ao dono
  para a promoção a stable.

---

### Task 9: Investigação — por que um switch vira dois raios

Pode rodar em paralelo às Tarefas 1–6 (não depende delas). Nada de mudança de comportamento aqui.

**Files:**
- Create: `tests/integration/test_switch_state.py`
- Modify: `docs/superpowers/specs/2026-11-17-diagnostic-sensors-design.md` (seção "Liga/desliga que vira dois raios":
  substituir "Hipótese a confirmar" pelo achado, com arquivo e linha do frontend)

- [ ] **Step 1: Frontend.** Descobrir a versão do frontend do HA de produção (`/api/config` → `version`; o frontend
  vem fixado no `manifest.json` de `homeassistant/components/frontend` dessa versão, `home-assistant-frontend==…`).
  Baixar o pacote dessa versão do PyPI na VM (como em 17/11: `curl` do JSON do PyPI, sem pip) e ler o fonte
  compilado de `ha-entity-toggle` e da linha de toggle do Lovelace (`hui-toggle-entity-row` /
  `entity-rows`); se o pacote só tiver JS minificado, ler o fonte do repositório `home-assistant/frontend` na tag
  correspondente. Registrar a condição exata que escolhe os dois botões (esperado: `stateObj.attributes.assumed_state`
  ou estado `unknown`/não `on`/`off`) e o que acontece com `unavailable`.
- [ ] **Step 2: Write the test that reproduces o lado do servidor**

```python
async def test_switch_without_value_is_unknown_and_available(hass):
    # satellite hello with the "mute" switch descriptor and NO "mute" key in state
    st = hass.states.get("switch.friday_sat1_mute")
    assert st.state == "unknown" and "assumed_state" not in st.attributes
async def test_switch_with_value_is_on(hass):  # state {"mute": True} -> "on"
async def test_voice_enabled_has_its_own_value(hass):  # mirrors 17/11: voice_enabled on while the others unknown
```

- [ ] **Step 3: Run** `FRIDAY_TEST_DIR=ha-friday-wt-diag bash scripts/test.sh tests/integration/test_switch_state.py`
  — PASS (o teste documenta o comportamento atual).
- [ ] **Step 4:** listar no spec todos os booleanos controláveis da integração (switches do satélite e do servidor:
  `voice`, `ota_auto_install`) e quando cada um pode ficar sem valor. Commit
  `test(integration): switches without a value stay available and unknown`. Registrar o achado no spec (a decisão já é A).

---

### Task 10: Switch sem valor fica `unavailable` (opção A, aprovada em 17/11)

Vem antes da Tarefa 7 na execução (entra na v0.3.8). Depende da Tarefa 9 só para o arquivo de teste.

**Files:**
- Modify: `custom_components/friday/switch.py` (`FridaySwitch.available`)
- Test: `tests/integration/test_switch_state.py`

- [ ] **Step 1: Failing tests:** switch com descriptor e sem chave no estado → `state == "unavailable"`; a chave
  chega (`push_state {"mute": False}`) → `"off"` e volta a disponível; `None` explícito no estado → `"unavailable"`;
  switch do servidor (`voice`) segue a mesma regra; agente desconectado continua `unavailable` (regra atual).
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** `available` = `super().available and self._value is not None`. Nada de `assumed_state`.
- [ ] **Step 4: Run** a suíte de integração — PASS; atualizar a nota da Tarefa 8 com a prova ao vivo (programa do MK1
  parado → switches cinza, sem raios).
- [ ] **Step 5: Commit** `fix(integration): a switch without a value is unavailable, not unknown`


---

### Task 11: Sinal de "Voice Program" ligado sem o programa rodar — **BLOQUEADA até a resposta do dono**

Fora da v0.3.8, a menos que o dono escolha antes de a execução começar. Opções e recomendação no spec (seção
"Voice Program mostra a intenção"). Se ele escolher **(a)**:

**Files:**
- Modify: `agents/satellite/friday_satellite_agent/__main__.py` (estado `voice_enabled_attributes`, chave nova)
- Modify: `agents/satellite/friday_satellite_agent/capabilities.py` (descriptor novo)
- Test: `tests/agents/test_satellite_power.py`, `tests/agents/test_entity_placement.py` (a chave nova é Diagnóstico)

**Interfaces:**
- Produces: `voice_program_mismatch(enabled: bool, program_state: str | None, off_since: float | None, now: float,
  grace_s: float) -> bool` (pura); estado `voice_enabled_attributes = {"running": bool}`; descriptor
  `{"key": "voice_program_not_running", "platform": "binary_sensor", "name": "Voice program not running",
  "device_class": "problem", "category": "diagnostic"}`. `grace_s` = N minutos: **o dono escolhe N** (sugestão: 5).

- [ ] **Step 1: Failing tests:** intenção `on` + `program_state` `off` há menos de N → `False`; há N ou mais → `True`;
  intenção `off` → sempre `False`; programa volta (`idle`) → `False` e `running` `True`.
- [ ] **Step 2: Run** `FRIDAY_TEST_DIR=ha-friday-wt-diag bash scripts/test.sh tests/agents/test_satellite_power.py` — FAIL.
- [ ] **Step 3: Implement** no laço de `refresh` do agente (onde `program_state` é calculado), guardando desde quando
  o programa está `off`.
- [ ] **Step 4: Run** — PASS. **Step 5: Commit** `feat(satellite): flag Voice Program on while the program is not running`

Se ele escolher **(b)**, reescrever esta tarefa antes de executar.
