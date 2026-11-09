# Imagem de transcrição sob demanda (24/10/2026). Nunca fica rodando: run.sh usa `docker run --rm`.
# Modelos do Whisper ficam no volume /models (persistente na VM, fora da imagem).
FROM python:3.12-slim
RUN pip install --no-cache-dir faster-whisper==1.2.1 numpy==2.5.4
COPY transcribe.py /app/transcribe.py
ENTRYPOINT ["python", "/app/transcribe.py"]
