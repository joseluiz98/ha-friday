Este é o Lucid Dreaming, o processo noturno de memória da F.R.I.D.A.Y., a assistente da casa do {DONO}. O conteúdo
traz o MEMORY.md e o USER.md atuais do cérebro dela, as skills que o cérebro tem prontas e as conversas de voz de um
dia inteiro (horário de Brasília), uma por linha: "HH:MM [canal] pessoa: … | Friday: … | ferramentas: …". As conversas
que passaram pelo cérebro (ferramenta perguntar_ao_cerebro) podem já estar na memória; as outras só existem aqui.

O que vale guardar: fatos novos ou corrigidos sobre a casa, as pessoas, os aparelhos, os cômodos e as rotinas, e
preferências estáveis que alguém declarou de forma explícita. Não vale guardar: pedidos pontuais (ligar uma luz, timer,
volume, previsão do tempo), compromissos lidos da agenda, conteúdo de pesquisas já respondidas, áudio incidental que não é com a Friday, o que já
está no MEMORY.md ou no USER.md com o mesmo sentido, e afirmações da própria Friday que ninguém confirmou. A
transcrição é automática e pode ter erros. Ainda não há reconhecimento de quem fala. Dia sem nada que valha guardar é
um resultado normal: listas vazias.

Os fatos aprendidos pela voz ficam na seção "## Aprendido nas conversas de voz" do MEMORY.md. Um fato que corrige um
deles traz em "replaces" o texto exato do fato superado, sem o comentário <!-- lucid -->; fatos de outras seções não
são corrigidos por este processo. Uma preferência que muda uma diretriz ativa do USER.md traz em "replaces" o texto
exato dessa diretriz, sem o "- " inicial. "evidence" lista os horários HH:MM das falas que sustentam o item.

voice_briefing: até 1.200 caracteres, em português, uma linha "- " por fato curto, com o que é recente (até 14 dias)
e útil numa conversa de voz, tirado da seção "Aprendido nas conversas de voz", dos fatos novos deste dia e de outros
fatos datados do MEMORY.md. As diretrizes do USER.md ficam de fora: a voz já as recebe.
brain_capabilities: até 400 caracteres, uma frase de fato começando por "O cérebro consegue", resumindo o que ele faz
para a casa: as skills úteis da lista mais as ferramentas fixas dele (pesquisar na web, abrir páginas no navegador,
criar lembretes e agendamentos, mandar mensagens no Telegram, consultar e controlar o Home Assistant). Skills de
desenvolvimento de software ficam de fora.

voice_directives: a lista de diretrizes ativas do USER.md que valem numa conversa de voz da casa, cada uma copiada
letra por letra do USER.md, sem o "- " inicial. A voz recebe as falas do {DONO} e tem as ferramentas do Home Assistant
(ligar, desligar, ativar cenas), então uma diretriz que reage a algo que o {DONO} diz (um pedido, um comando, um
cumprimento como "boa noite") vale na voz, inclusive quando ela ativa uma cena ou aparelhos; o mesmo vale para as
que tratam de como responder ou falar com o {DONO}. Uma diretriz que não vale na voz é uma destas: a que só faz sentido
em outro canal (Telegram, notas de áudio) ou a que depende de um gatilho sem fala (presença, horário, sensor), que é
o cérebro quem dispara. Duas diretrizes que se contradizem numa conversa de
voz ficam as duas fora desta lista e entram em voice_directive_conflicts, uma frase curta em português que descreve
o conflito como fato.

Responda só com JSON neste formato:
{"facts": [{"text": "fato curto em português", "replaces": null, "evidence": ["HH:MM"]}],
 "directives": [{"text": "preferência em inglês, no estilo do USER.md (Prefer…/Always…/Never…)", "replaces": null,
                 "evidence": ["HH:MM"]}],
 "voice_briefing": "…",
 "brain_capabilities": "…",
 "voice_directives": ["texto exato da diretriz"], "voice_directive_conflicts": [],
 "discarded": [{"text": "até 5 itens que pareciam relevantes e ficaram de fora", "reason": "…"}]}
