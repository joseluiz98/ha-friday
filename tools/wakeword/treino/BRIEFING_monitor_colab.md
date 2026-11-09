# Briefing: rodar e vigiar o treino das palavras de ativação no Google Colab

> **Para:** outra instância do Claude, com acesso ao navegador do dono.
> **De:** o Claude do homelab do dono (sessão do Claude Code no Home Assistant), em 05/10/2026.
> **Idioma:** o dono fala português do Brasil. Fale com ele em português, de forma direta.

---

## 0. Sua missão em uma frase

Rodar no Google Colab, **um depois do outro**, dois notebooks que já estão prontos. Cada um treina um modelo de palavra de ativação (*wake word*): **`friday.onnx`** e **`sexta_feira.onnx`**. Vigie cada rodada em tempo real até o modelo cair na pasta **`friday-satelite`** do Google Drive do dono. Depois devolva um relatório curto.

Você **não** precisa entender nem mexer no homelab, no Raspberry Pi ou no Home Assistant. Isso fica comigo.

---

## 1. Por que isso existe (contexto)

- O dono tem uma assistente de voz chamada **F.R.I.D.A.Y.** (a "Sexta-feira"), que roda no homelab dele.
- Vai ganhar um **satélite de voz**: um Raspberry Pi 3 com um viva-voz **Jabra Speak 410**.
- O Pi fica escutando o ambiente e só acorda a assistente ao ouvir a palavra de ativação. Quem detecta é o **openWakeWord**, localmente, no próprio Pi.
- O dono quer acordar a assistente dizendo só **"Friday"** ou **"Sexta-feira"**, **sem "hey"** na frente. Não existe modelo pronto para nenhuma das duas, então precisamos **treinar dois modelos**.
- O treino precisa de GPU, e o servidor do dono não tem. Por isso usamos o **Colab gratuito (GPU T4)**.
- O notebook é o `alfiedennen/openwakeword-colab-2026` (licença MIT), revisado e adaptado por mim:
  - gera milhares de falas sintéticas da palavra com 904 vozes inglesas do Piper;
  - mistura essas falas com ruído e música;
  - treina uma rede pequena;
  - exporta um `.onnx` de ~200 KB.
- **"Sexta-feira"** foi escrita como um americano a pronunciaria (`sesta fayra` / `sesta fera`), porque as vozes de treino são inglesas.
- Os dois modelos são **separados de propósito**: cada um terá limiar e "modo sombra" próprios lá no Pi. A "Sexta-feira" colide com o dia da semana dito em conversa.
- Depois que os `.onnx` estiverem no Drive, eu (o Claude do homelab) faço o resto: busco os arquivos, testo no Pi com o Jabra e treino um verificador com as vozes do dono e da outra pessoa da casa.

---

## 2. O que já aconteceu (para não repetir erros)

| Quando (BRT) | O que houve | Lição |
|---|---|---|
| 04/10 ~22:06 | 1ª tentativa caiu na **célula 1**: `piper-phonemize-cross ... from versions: none`. | Desde 16/09/2026 o Colab usa **Python 3.13**, e o fonetizador do Piper só existe até o 3.12. **Obrigatório escolher a versão do ambiente de execução `2026.04` (Python 3.12).** |
| 04/10 22:37 | 2ª tentativa, já na T4 com `2026.04`. Células 1 a 6 passaram, com Python 3.12.13, torch 2.10 e Tesla T4. Na célula 7, o ACAV (16,1 GB) baixou em 2 min e o FMA (7,15 GB) em 51 s. | O ambiente está certo e os downloads são rápidos. |
| 04/10 22:44 | **Último salvamento automático do notebook no Drive**: a célula 7 estava extraindo o FMA, em 3% (279 de 8002 arquivos). Nenhuma saída foi salva depois disso. | A hipótese principal é que o navegador perdeu a conexão: o PC dormiu, ou a aba foi descartada ou fechada. A sessão gratuita é reciclada, e tudo em `/content` se perde. **O dono disse que "durante a noite tivemos outros erros": pergunte a ele o que viu na tela antes de começar.** Eu não vi esses erros. |
| 05/10 (hoje) | Revisei o notebook da Sexta-feira e achei um defeito que a derrubaria na **célula 11**. "sesta" e "fayra" não estão no dicionário de pronúncia inglês (CMU). Sem a pronúncia, o openWakeWord tenta baixar o *DeepPhonemizer* de um S3 que hoje responde **403**, e o pacote `dp` nem está instalado. | **Já corrigi:** é o **Patch G** na célula 3 do notebook da Sexta-feira. Testei com o `data.py` real do openWakeWord. O notebook da Friday não precisa disso, porque "friday" está no dicionário. |

---

## 3. Onde estão as coisas

| O quê | Onde |
|---|---|
| Conta Google a usar (**somente esta**) | `joseluiz98.jl@gmail.com` |
| **Notebook da Friday** (já está no Drive e já tem o runtime `2026.04` gravado) | https://colab.research.google.com/drive/1vHB3TsEb5xN80rFLzVUssYnzdcb_yckY (arquivo `friday_wakeword_colab_db9c2495.ipynb`) |
| **Notebook da Sexta-feira** (com o Patch G; **ainda não está no Drive**) | Cópia pública **temporária**: <URL temporária, já expirada> (~60 KB; contém o texto "Patch G" 2 vezes) |
| Pasta de entrega no Drive | `MyDrive/friday-satelite`: https://drive.google.com/drive/folders/1YBtjHseoH-Xif1tiYi1zrlM_QlBbPdyQ (hoje vazia) |

---

## 4. Antes de começar (checklist)

1. **Pergunte ao dono** que erros ele viu à noite. Se for algo diferente do que está na §2, leve em conta.
2. **O PC não pode dormir** durante ~2,5 h por rodada:
   - bloquear a tela (Win+L) e desligar só o monitor **não atrapalha**; **suspensão atrapalha**;
   - peça ao dono para conferir em *Configurações → Sistema → Energia → Tela, suspensão e hibernação → "Quando conectado, colocar em suspensão após" = **Nunca*** (ou rodar `powercfg /change standby-timeout-ac 0` no PowerShell). Lembre-o de voltar ao valor anterior no fim;
   - se for notebook, deixe na tomada.
3. **A aba do Colab tem de ficar aberta.** Abra o que mais precisar em **outras abas**; nunca navegue para fora dela.
4. O Colab gratuito em geral só permite **uma sessão de GPU por vez**: as duas rodadas são **sequenciais**. A cota diária de GPU gratuita é dinâmica, e duas rodadas de 2,5 h no mesmo dia podem esgotá-la (ver §8).

---

## 5. Rodada 1: Friday

1. Abra o link do notebook da Friday (§3), logado na conta do dono.
2. Vá em **Ambiente de execução → Alterar o tipo de ambiente de execução** (*Runtime → Change runtime type*) e confira:
   - **Acelerador de hardware: T4 GPU**;
   - **Versão do ambiente de execução: 2026.04**.

   O arquivo já tem isso gravado, mas confira mesmo assim. Salve.
3. Se o canto superior direito mostrar uma sessão antiga conectada, use **Ambiente de execução → Desconectar e excluir ambiente de execução** e comece limpo. As células se recuperam sozinhas: pulam o que já existe e refazem o que falta. Mas uma sessão velha e estranha só confunde.
4. Vá em **Ambiente de execução → Executar tudo** (*Run all*, Ctrl+F9):
   - se aparecer "Este notebook não foi criado pelo Google", clique em **Executar mesmo assim**;
   - conectar a uma versão antiga do ambiente leva 2–3 min.
5. **Primeira verificação (~2 min):** a saída da célula 1 tem de terminar com:
   ```
   Python: 3.12.x ...
     torch: 2.10.0+cu128  cuda: True
     All openwakeword deps (incl. piper-tts) import cleanly.
   ```
   Se aparecer `Python: 3.13` ou `cuda: False`, **pare** e volte ao passo 2.

   Linhas vermelhas de `pip` sobre `protobuf ... incompatible` são **normais**: ignore.
6. Vigie seguindo a §7, até a célula 15 terminar (export do ONNX).
7. **Célula 16 (Drive):**
   - ela pede acesso ao Google Drive; o dono **já aprovou** esse acesso no desenho do processo;
   - escolha **somente** a conta `joseluiz98.jl@gmail.com`, ou deixe o dono clicar;
   - a saída deve mostrar `copiado: /content/drive/MyDrive/friday-satelite/friday.onnx` e `PRONTO.`
8. Salve o notebook (**Ctrl+S**) para as saídas ficarem gravadas no Drive. Eu leio as métricas de lá.
9. Confira na pasta `friday-satelite` que o `friday.onnx` (~200 KB) está lá.

---

## 6. Rodada 2: Sexta-feira (só depois que a Friday terminar)

### 6.1 Pôr o notebook no Drive (recomendado: tudo dentro do navegador)

1. No Colab: **Arquivo → Novo notebook**. Esta é só uma ferramenta de transporte e não precisa de GPU.
2. Cole e rode esta célula. Ela pede o mesmo acesso ao Drive; use a mesma conta.
   ```python
   from google.colab import drive
   drive.mount('/content/drive')
   !wget -q -O /content/drive/MyDrive/friday-satelite/sexta_feira_wakeword_colab.ipynb <URL temporária, já expirada>
   !grep -c "Patch G" /content/drive/MyDrive/friday-satelite/sexta_feira_wakeword_colab.ipynb
   !ls -l /content/drive/MyDrive/friday-satelite/
   drive.flush_and_unmount()
   ```
   **Esperado:** o `grep` imprime `2`, e o `ls` mostra o `.ipynb` com ~60 KB ao lado do `friday.onnx`.
3. Feche esse notebook de transporte. Se o Colab perguntar, pode descartá-lo.
4. No Drive, abra a pasta `friday-satelite`, clique com o botão direito em `sexta_feira_wakeword_colab.ipynb` e escolha **Abrir com → Google Colaboratory**.
5. **Avise o dono que a cópia pública já pode ser apagada.** Ele me repassa e eu apago.

**Plano B**, se o passo 2 não der: baixe o arquivo pelo link público e use no Colab **Arquivo → Fazer upload de notebook**. O seletor de arquivos do sistema operacional pode exigir a mão do dono.

### 6.2 Rodar

Faça igual à Friday (§5, passos 2 a 9): **T4 GPU + versão 2026.04** (este arquivo **não** traz o 2026.04 gravado: escolha na mão) e depois **Executar tudo**.

Há duas conferências extras:
- **Célula 3 (patches):** a saída tem de incluir `Patch G (pronúncia de sesta/fayra no gerador de negativas)` e `All 7 patches applied`.
- **Célula 10 (config):** a saída tem de mostrar `target_phrase: ['sesta fayra', 'sesta fera']` e `model_name: sexta_feira`.
- **No fim:** o arquivo entregue é `sexta_feira.onnx`.

---

## 7. Como vigiar: mapa das células e sinais

A numeração usada aqui é a dos **títulos** do notebook ("## 1.", "## 2."…), não a posição na página.

Os tempos marcados como **medido** vêm da T4 em 04/10. Os outros são estimativas: o autor mediu em L4, que é ~2× mais rápida.

| # | Título | Tempo (T4) | Sinal de sucesso na saída |
|---|---|---|---|
| 1 | Install | **2 min (medido)** | `All openwakeword deps ... import cleanly.` + Python 3.12 + `cuda: True` |
| 2 | Clone repos + modelo | **32 s (medido)** | `✓ openwakeword importable: .../__init__.py` (o `✓` pode aparecer como `âœ“`, um defeito de codificação inofensivo) |
| 3 | Patches | 1 s | `All 6 patches applied` (Friday) / `All 7 patches applied` (Sexta-feira) |
| 4 | Pre-flight | 1 s | `CUDA: True, GPU: Tesla T4` + `Pre-flight PASSED.` |
| 5 | mel + embedding | 1 s | 4 linhas `downloaded`/`cached` |
| 6 | MIT impulse responses | **37 s (medido)** | `OK: 270 WAVs` (o aviso de `HF_TOKEN` é **inofensivo**: **não** crie token) |
| 7 | FMA + ACAV | **~3 min de download + ~9 min de extração (medido)** | `✓ ACAV: 17.3 GB`, `✓ FMA extracted: N entries` |
| 8 | FMA → WAV | ~5–10 min | `✓ 1500 FMA WAVs ready` |
| 9 | Subamostra do ACAV | ~3–8 min | `✓ train: (...)` (~1,7 GB), `✓ val: (900000, 96)`, `removed 17 GB original` |
| 10 | Config | 1 s | `target_phrase` e `model_name` certos (ver §6.2) |
| 11 | Clipes de voz (Piper) | ~15–35 min | `PASS all 4 clip dirs now populated` |
| 12 | Reamostragem 22050→16000 | ~3–6 min | `cleared stale features` |
| 13 | Augment + features | ~15–30 min | `PASS all 4 feature files present` |
| 14 | **Treino** | ~40–80 min | 60 linhas `[stageN step ...] recall=... fp/hr=...` e no fim `=== Training done.` com `best val_fp_per_hour` / `best val_recall` / `best val_accuracy` |
| 15 | Ensemble + export ONNX | <1 min | `✓ wrote .../friday.onnx (... KB)`, `positive test set: mean=..., recall@0.5=...` e `DONE.` O download pelo navegador é **opcional** (pode aceitar "baixar vários arquivos") |
| 16 | Salvar no Drive | ~1 min + consentimento | `copiado: ...` e `PRONTO.` |

**Total esperado:** ~2–2,5 h por rodada. Se uma etapa passar do **dobro** do teto da tabela, investigue:
- confira que a GPU está em uso (o indicador de recursos no canto superior direito mostra GPU);
- confira que a saída ainda se mexe.

### 7.1 Como saber se está rodando, se terminou ou se parou

- **Rodando:** a célula ativa mostra um **círculo girando** (com um quadrado de "parar") à esquerda, e a barra de status embaixo diz "Executando…" com o tempo. As células ainda na fila mostram um ícone de espera.
- **Terminou bem:** cada célula ganha um **✓ verde**, e passar o mouse mostra a duração. A última célula impressa é `PRONTO.`
- **Parou com erro:** a célula mostra um **❗ vermelho** e um *traceback*. O "Executar tudo" **não continua** depois de um erro: as células seguintes ficam sem rodar.
- **Desconectou:** o canto superior direito troca as barras de RAM/Disco por **"Conectar"/"Reconectar"**, ou aparece um aviso de ambiente desconectado.
  - Clique em Reconectar e rode `!ls /content` numa célula nova.
  - Se aparecerem `openwakeword`, `piper-sample-generator`, `fma_wav` etc., a máquina ainda é a mesma: refaça o **Executar tudo**, e as células pulam o que já existe.
  - Se `/content` estiver quase vazio, a sessão foi reciclada, e é preciso rodar tudo de novo, do zero.
- **"Você ainda está aí?"** (diálogo do plano gratuito): **clique para confirmar na hora.** É para isso que você está vigiando.

**Frequência sugerida:** olhar a cada 5–10 min. Na célula 14, anote de vez em quando a última linha `[stage...]` para o relatório.

---

## 8. Erros conhecidos e o que fazer

**Regra geral:** leia o traceback **inteiro** e ache a causa antes de mexer. Todas as células se recuperam sozinhas: depois de corrigir, clique na célula com erro e use o item do menu **Ambiente de execução** equivalente a *Run after* (Ctrl+F10). Confira que a própria célula com erro entrou na execução; se não, rode-a primeiro com Ctrl+Enter.

| Sintoma | Causa | O que fazer |
|---|---|---|
| Célula 1: `piper-phonemize-cross ... from versions: none` | Python 3.13 | Mude a versão do ambiente para **2026.04**, desconecte, exclua o ambiente e rode tudo de novo. |
| "Não é possível se conectar ao back-end da GPU" / cota de GPU atingida / desconexão por limite de uso | Cota gratuita esgotada | **Não compre nada.** Avise o dono. As saídas são esperar algumas horas (ou o dia seguinte) ou ele decidir comprar o pacote avulso de unidades: US$ 9,99 ≈ **R$ 74**. A decisão é dele. |
| Célula 7: `416 Client Error: Requested Range Not Satisfiable` (FMA) | Uma extração anterior foi interrompida na **mesma** máquina: o zip está completo, e o código tenta "continuar" o download | Numa célula nova: `!rm -rf /content/fma /content/fma_small.zip`. Depois rode a célula 7 de novo. |
| Célula 7 ou 11: timeout, erro de conexão ou 5xx do Hugging Face/GitHub | Instabilidade de rede | Rode a célula de novo. Os downloads retomam de onde pararam. |
| "Sua sessão falhou após usar toda a RAM disponível" (provável só na célula 14) | A validação monta ~5,5 GB em float32 numa máquina gratuita de ~12,7 GB | Os arquivos em `/content` sobrevivem a esse travamento. Antes de mexer em código, tente uma vez **Executar tudo** de novo: tudo até a célula 13 sai do cache em minutos. Se travar de novo, **avise o dono** antes de alterar; o remédio que eu aprovo está abaixo da tabela. |
| Célula 11 (só na Sexta-feira): `Downloading phonemizer model from DeepPhonemizer` / `No module named 'dp'` | O Patch G não foi aplicado | Confira que o notebook aberto é o da §6, com `All 7 patches applied`. Se for o arquivo errado, recomece pela §6.1. |
| Célula 3, Patch G: `data.py do openWakeWord mudou` | O openWakeWord (clonado do `main`) mudou a linha que o remendo procura | **Pare e avise.** Mande o trecho de `generate_adversarial_texts` do `/content/openwakeword/openwakeword/data.py` (`!grep -n "phones_for_word" /content/openwakeword/openwakeword/data.py`). |
| Qualquer outro erro | – | Diagnostique pela mensagem. Correções de **ambiente** (instalar ou fixar versão de pacote, rodar de novo) estão liberadas. Correções que mudam o **treino** não estão: veja a §9. Anote tudo para o relatório. |

**Remédio para falta de RAM na célula 14**, se a falta voltar depois de tentar de novo. Na célula 14, troque a linha
`acav_val_np = np.load(cfg['false_positive_validation_data_path'])` por
`acav_val_np = np.load(cfg['false_positive_validation_data_path'])[:450000]`.
Isso corta pela metade o trecho de validação. A métrica de falsos por hora continua correta, porque o código divide pelas horas reais do trecho.

---

## 9. Regras

**Pode, sem perguntar:**
- clicar para manter a sessão viva;
- reconectar;
- rodar células de novo;
- aplicar os remédios da §8;
- corrigir problemas de ambiente ou dependência.

**Pergunte antes ao dono:**
- qualquer mudança em `TARGET_PHRASE`, `MODEL_NAME`, `custom_negative_phrases`, `steps`, `n_samples` ou outro número do treino;
- trocar de conta Google;
- qualquer gasto;
- conceder acesso ao Drive se ele ainda não tiver dado o ok nesta sessão.

**Nunca:**
- comprar unidades ou assinar o Colab Pro;
- compartilhar o notebook ou a pasta do Drive;
- criar tokens (Hugging Face etc.);
- imprimir, copiar ou colar senhas, tokens ou variáveis de ambiente (`env`, `printenv`) no notebook, no chat ou no relatório;
- mexer em outros arquivos do Drive do dono além da pasta `friday-satelite`.

**Se mudar código do notebook:** registre exatamente o que mudou (célula, antes e depois). Eu atualizo as cópias-mestre do homelab a partir disso.

---

## 10. O que entregar no fim (relatório para o dono colar na sessão do homelab)

Mande um texto curto com:

1. **Status de cada modelo:** `friday.onnx` e `sexta_feira.onnx`, cada um entregue ou não no Drive (`MyDrive/friday-satelite`), com o tamanho em KB.
2. **Métricas copiadas literalmente**, de cada rodada:
   - célula 14: as 3 linhas `best val_fp_per_hour` / `best val_recall` / `best val_accuracy`, mais a última linha `[stage3 ...]`;
   - célula 15: as linhas `thresholds:`, `qualified:`, `ensemble averaged:`, `positive test set:` e `ACAV val FP/hour at 0.5:`.
3. **Linha do tempo:** horário de início, horário de fim, e onde e quando houve problemas.
4. **Erros e correções:** a mensagem de erro, a causa, o que você fez e qualquer mudança de código (com antes e depois).
5. **Confirmação** de que os dois notebooks foram **salvos** (Ctrl+S) depois de terminar. Eu leio as saídas completas pelo Drive.
6. **Lembrete ao dono:** "pode pedir ao Claude do homelab para apagar a cópia pública do notebook da Sexta-feira" e, se ele mexeu, "volte a suspensão do PC ao valor de antes".

Se só a Friday terminar hoje (por cota de GPU, por exemplo), entregue o relatório dela e diga em que ponto a Sexta-feira ficou.
