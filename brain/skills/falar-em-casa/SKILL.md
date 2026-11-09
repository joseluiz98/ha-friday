---
name: falar-em-casa
description: Use para que algo seja ouvido em casa pela voz da casa (o satélite), em vez de lido no Telegram — avisos ao {DONO} ou a resposta a quem pediu algo num satélite.
---

# Falar em casa (satélite de voz)

O satélite é um alto-falante com microfone num cômodo (hoje só existe o do escritório, `{SAT_OFFICE}`). Esta skill
pede ao bot da voz que toque o som de aviso de lembrete no satélite e abra uma conversa em que a voz da casa
fala a sua mensagem, com as palavras dela. Spec:
`/homeassistant/docs/superpowers/specs/2026-10-12-satelite-volume-timers-mao-dupla-design.md`.

## Como entregar

```bash
sh /srv/friday/home/.openclaw/workspace/friday/skills/falar-em-casa/entregar.sh jose lembrete "Estender a roupa"
```

- Destino `jose`: o bot confere se ele está em casa (presença do HA) e usa o satélite.
- Destino `satelite:<id>`: aquele satélite, para quem estiver lá. Pedidos que chegam com o prefixo
  `[pedido vindo do satélite de voz sat-…]` vieram desse satélite.
- Tipo `lembrete` ou `aviso`. Texto de até 1.000 caracteres.
- A chamada espera a conversa terminar: pode levar alguns minutos.

O resultado vem em JSON, no campo `resultado`:

| Resultado | O que aconteceu |
|---|---|
| `entregue` | Alguém respondeu por voz ou tocou no botão depois da fala. |
| `nao_entregue` | A mensagem foi falada (ou a conversa nem abriu) e ninguém respondeu. |
| `calado` | O não perturbe do satélite está ligado: nada foi falado. |
| `fora_de_casa` | O {DONO} não está em casa: nada foi falado. |
| `sem_satelite` | O satélite não respondeu (fora do ar). |
| `erro` | Pedido inválido ou o bot da voz fora do ar; o campo `erro` diz qual. |

Regra do {DONO} (12/10/2026), para avisos entregues por este script: quando o resultado não é `entregue`, a mesma
mensagem vai pelo Telegram dele (ele é o único usuário do Telegram). Lembretes agendados com o `delivery` descrito no
`AGENTS.md` já chegam pelo Telegram em todos os casos.

## Lembretes

Lembrete é um disparo único do agendador do OpenClaw, sempre com fuso `America/Sao_Paulo` (hora sem fuso é tratada
como UTC). Timer (contagem de minutos, "timer de 10 minutos") não é lembrete: ele mora no próprio satélite e a voz
cuida dele sem passar por aqui.

Para onde vai o resultado de um lembrete (o `delivery` do agendamento) está nas notas locais do `AGENTS.md`.

## Segurança

A senha do bot fica em `/srv/friday/state/canal_casa.token` e o script a lê sozinho. Não imprima esse
arquivo nem a senha: a saída dos comandos vai para o seu histórico.
