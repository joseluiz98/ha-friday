# Análise do falso negativo da palavra "Friday" (desde 24/10/2026)

Responde "por que tive que dizer Friday várias vezes?" com dados. Foi assim que, em 24/10, a coleta de 20–24/10 mostrou
que o modelo base `friday.onnx` dá nota ~0 a "Friday" ditos com clareza (~80% das perdidas), e que a repontuação
offline reproduz o ao vivo (o pipeline da Pi está fora de suspeita).

## Rodar
```bash
/homeassistant/tools/satelite/wakeword-analysis/run.sh --since 20261024            # modelo atual
/homeassistant/tools/satelite/wakeword-analysis/run.sh --model /home/jose/modelos/candidato.onnx   # compara um modelo novo
```
Opções: `--whisper medium` (mais fiel, ~3× mais lento), `--out DIR` (padrão `/tmp/wakeword-analysis/<data>`, efêmero).

## Peças
| Onde | O quê |
|---|---|
| Pi (já no programa) | `falas_palavra.ndjson` (nota base por trecho de fala) e `ativacoes/contexto_<ts>.wav` (30 s antes de cada ativação), desde 20/10 17:50 |
| VM `.251` | imagem `friday-asr:1` (`/opt/friday/asr/`, mestre em `asr/`), cache do Whisper em `/var/cache/friday-asr` (regenerável, fora do backup). **Nunca fica rodando**: `run.sh` usa `docker run --rm` com teto de 3 CPUs / 3 GB |
| `rescore.py` | roda na Pi com `systemd-run` (450 MB, 60% CPU, nice 19): só o modelo base, áudio contínuo |
| `report.py` + `test_report.py` | cruza tudo (`python3 -m unittest -q test_report`) |

## Armadilhas
- O áudio da casa nunca sai da rede: a transcrição é local (VM). As cópias temporárias são apagadas no fim (`trap`).
- As transcrições são texto de conversas da casa: a saída fica em `/tmp`, não no backup.
- Whisper erra em trecho ruidoso e varia entre rodadas: use para lote, não para julgar um arquivo. Sem `initial_prompt` de propósito (com "Friday" no prompt ele inventa a palavra).
- O decodificador PyAV do faster-whisper 1.2.1 quebra (`metadata_errors`): o `transcribe.py` lê WAV com `wave`/numpy.
- Leitura: perdida com nota offline < 0,3 = modelo não ouviu (retreino); 0,3–0,6 = limiar; `barrada` = verificador.

Backups do modelo antes do retreino: `modelos/friday.onnx.bak.20261024_pre_retrain` e `friday_verificador.pkl.bak.20261024_pre_retrain` (aqui e em `~/modelos/` na Pi).
