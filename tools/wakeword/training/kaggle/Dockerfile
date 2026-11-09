# Executor do Kaggle sob demanda (spec 2026-10-24). Nunca fica rodando: kaggle_run.sh usa docker run --rm.
FROM python:3.12-slim
RUN pip install --no-cache-dir kaggle==2.2.5
ENTRYPOINT ["kaggle"]
