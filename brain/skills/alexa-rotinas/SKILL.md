---
name: alexa-rotinas
description: Use quando o {DONO} perguntar sobre rotinas da Alexa (o que uma rotina faz, "aquela rotina da manhã", por que algo aconteceu sozinho), quando uma mensagem do Home Assistant avisar que as rotinas da Alexa mudaram, ou na auditoria semanal das rotinas.
---

# Rotinas da Alexa

As rotinas moram na nuvem da Amazon. O Home Assistant as lê (somente leitura) com a sessão da
integração Alexa Media Player e entrega a você já traduzidas. Spec:
`/homeassistant/docs/superpowers/specs/2026-09-30-alexa-rotinas-auditoria-design.md`.

## Como ler

Resumo de todas (≈11 KB, comece sempre por aqui):

```bash
curl -s -X POST -H "Authorization: Bearer $FRIDAY_HA_TOKEN" -H "Content-Type: application/json" \
  -d '{"modo": "resumo"}' \
  "$FRIDAY_HA_URL/api/services/pyscript/alexa_rotinas?return_response"
```

Detalhe de uma rotina (nome igual ou parte do nome, sem diferenciar maiúsculas):

```bash
curl -s -X POST -H "Authorization: Bearer $FRIDAY_HA_TOKEN" -H "Content-Type: application/json" \
  -d '{"modo": "detalhe", "nome": "bom dia"}' \
  "$FRIDAY_HA_URL/api/services/pyscript/alexa_rotinas?return_response"
```

A resposta vem em `service_response`. Campos:

- `ativa`: a rotina está ligada na Amazon.
- `gatilhos`: o que dispara a rotina (frase, horário, nascer ou pôr do sol, sensor como a fechadura).
- `acoes`: o que ela faz, em ordem; aparelhos do HA aparecem pelo `entity_id`.
- `entidades`: entidades do HA acionadas e o estado atual; `"inexistente"` = não existe no HA.
- `orfaos`: quantos aparelhos distintos a rotina cita (em ação ou gatilho) que não estão mais na lista
  de aparelhos da Amazon. É forte candidato a rotina quebrada, mas confirme no detalhe antes de afirmar.
- `nao_verificaveis`: aparelhos citados com um id que não é de aparelho comum (por exemplo
  `virtual@LIGHT@…`); pode ser grupo ou aparelho virtual da Amazon. Não dá para confirmar se existe:
  não chame de quebrado.
- No resumo geral, `orfaos_total` e `nao_verificaveis_total` contam aparelhos distintos, não rotinas.
- `incompleto: true`: a lista de aparelhos da Amazon não veio; nessa leitura os órfãos não foram
  calculados e os aparelhos aparecem como `aparelho <8 primeiros caracteres do id>`.
- `candidatas`: o nome pedido bate com mais de uma rotina; pergunte ao {DONO} qual.
- `ok: false` + `erro`: a leitura falhou (por exemplo, a sessão da Amazon expirou).
- No detalhe, `definicao` traz o JSON cru da Amazon (`triggers`, `condition`, `sequence`).

O estado do leitor fica em `pyscript.alexa_rotinas` (última checagem, pendência, falhas). A base da
comparação diária, com o resumo de cada rotina, fica em `/share/alexa-rotinas/snapshot.json`: é de lá
que sai a descrição de uma rotina que já foi removida da Amazon.

## Quando o Home Assistant avisar mudanças

A mensagem traz os nomes de rotinas criadas, alteradas e removidas desde a última checagem. Ela vem do
próprio HA, às 08:30, só quando algo mudou.

Enquanto você analisa, o snapshot guarda em `pendencia.antes` o resumo de cada rotina alterada ou
removida como ela era antes da mudança. Compare com o resumo atual para dizer o que mudou de fato:

```bash
jq '.pendencia' /share/alexa-rotinas/snapshot.json
```

## Auditoria semanal

Objetivos:

1. mudanças recentes (`ultima_mudanca` no snapshot);
2. conflitos com automações do Home Assistant que mexem nas mesmas entidades;
3. rotinas quebradas (órfãos, entidades inexistentes ou `unavailable`);
4. sugestões de melhoria.

## Avisar o {DONO}

O resultado fica nesta conversa, que ele lê no app. O push vai só para ele:

```bash
curl -s -X POST -H "Authorization: Bearer $FRIDAY_HA_TOKEN" -H "Content-Type: application/json" \
  -d '{"title": "Rotinas da Alexa", "message": "<seu resumo curto>"}' \
  "$FRIDAY_HA_URL/api/services/{OWNER_NOTIFY}"
```

## Método econômico

Cada passo seu reenvia o contexto inteiro, então o tamanho do que você lê vira custo.

- Comece pelo resumo; abra o detalhe só das rotinas que importam.
- Para achar conflitos, busque só as automações que citam as entidades envolvidas (por exemplo,
  `grep -n "script.tv_on" /homeassistant/automations.yaml`), em vez de ler o arquivo inteiro.
- Não despeje logbook, histórico nem o snapshot inteiro no contexto.

## Limites

- Somente leitura: não edite, crie nem execute rotinas da Alexa.
- Não altere automações do HA por conta própria a partir da auditoria: proponha, e o {DONO} decide.
- `$FRIDAY_HA_TOKEN` já está no seu ambiente: use a variável direto nos comandos, como nos exemplos
  acima. Nunca imprima tokens nem variáveis de ambiente (`env`, `printenv`, `echo $…`): o que aparece na
  saída vai para o seu histórico e para o modelo. `curl -v`, `curl --trace` e `set -x` também imprimem
  o cabeçalho `Authorization` com o token: não use.
