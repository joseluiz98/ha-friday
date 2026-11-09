# Imagem de produção da F.R.I.D.A.Y. (spec 2026-10-18). Debian (glibc) porque o Pipecat e dependências nativas do
# OpenClaw (node-pty, sqlite-vec, onnxruntime...) não têm build para Alpine/musl.
FROM node:24-bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 python3-venv ca-certificates curl git jq openssl socat tini \
 && rm -rf /var/lib/apt/lists/*

RUN npm install -g --no-audit --no-fund openclaw@2026.9.9 mcporter@0.14.1 \
 && npm cache clean --force

RUN python3 -m venv /opt/pc \
 && /opt/pc/bin/pip install --upgrade pip \
 && /opt/pc/bin/pip install "pipecat-ai[google,webrtc,mcp,runner]==1.12.0"

# ffmpeg para o TTS do OpenClaw (01/10): o Gemini TTS devolve PCM, e a nota de voz do Telegram precisa
# de Opus. Antes a Sexta-feira instalava com apt-get dentro do container, e a instalação sumia a cada
# recriação. Camada própria, depois do npm e do pip, para o rebuild não refazer as anteriores.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg chromium openssh-client \
 && rm -rf /var/lib/apt/lists/*

# SSH para o HAOS e o LXC media: o OpenSSH lê o known_hosts de /root (pasta do usuário), não do $HOME.
RUN printf 'Host *\n  UserKnownHostsFile /srv/friday/home/.ssh/known_hosts\n  IdentityFile /srv/friday/secrets/haos_ssh\n' \
      > /etc/ssh/ssh_config.d/friday.conf

# summarize (21/10): CLI da skill bundled summarize (resumo de link, vídeo e PDF), com a GEMINI_API_KEY do gateway.
# Versão fixada com semanas de idade (cadeia de suprimentos); camada própria para o rebuild não refazer as anteriores.
RUN npm install -g --no-audit --no-fund @steipete/summarize@0.25.1 \
 && npm cache clean --force

COPY run.sh /run.sh
COPY gateway_loop.sh /gateway_loop.sh
COPY avulsos.sh /avulsos.sh
COPY remendo_pool.sh /remendo_pool.sh
COPY haos_links.sh /haos_links.sh
# Samba do HAOS só em /mnt/haos (desacoplamento de 19/10): /share é LOCAL, com o apelido do HOME (compose) e atalhos para
# /mnt/haos/share/<pasta>; os fixos abaixo são os que o bot e a memória usam, o run.sh acrescenta o resto a cada partida.
RUN mkdir -p /share && ln -s /mnt/haos/config /homeassistant \
 && for n in .claude CLAUDE.md claude-voice friday alexa-rotinas alexa-uso; do ln -s /mnt/haos/share/$n /share/$n; done
RUN chmod +x /run.sh

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/run.sh"]
