# wakeword-trainer (02/11/2026): pontuação, montagem e prova do retreino das palavras do satélite FORA da Pi.
# Versões IGUAIS às da Pi (~/oww): a igualdade das notas Pi x VM é provada antes de cada troca (ver LEIA-ME).
FROM python:3.13-slim
RUN pip install --no-cache-dir numpy==2.5.3 onnxruntime==1.30.0 scikit-learn==1.9.1 scipy==1.18.1 \
      threadpoolctl==3.7.0 joblib==1.6.0 soundfile==0.14.0 tqdm==4.70.1 requests==2.34.2 \
 && pip install --no-cache-dir --no-deps openwakeword==0.6.0
# modelos auxiliares do openWakeWord copiados da Pi (mesmos md5)
COPY oww_models/ /usr/local/lib/python3.13/site-packages/openwakeword/resources/models/
WORKDIR /work
