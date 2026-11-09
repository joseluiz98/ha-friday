# Imagem de análise do teste de cancelamento de eco (26/10/2026). Roda na VM .251 com `docker run --rm` (nunca fica no ar).
# SpeexDSP pelo ctypes (libspeexdsp1, a mesma lib que a Pi instalaria) e WebRTC AEC3 pelo livekit (AudioProcessingModule).
FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends libspeexdsp1 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir numpy==2.5.4 scipy==1.18.1 livekit==1.1.21
WORKDIR /w
COPY aec.py test_aec.py /w/
ENTRYPOINT ["python", "/w/aec.py"]
