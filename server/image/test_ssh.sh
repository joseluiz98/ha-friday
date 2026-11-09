#!/bin/sh
# O OpenSSH acha o known_hosts pela pasta do usuário (getpwuid → /root), não pelo $HOME: a imagem precisa de um
# ssh_config.d que aponte o known_hosts e a chave da F.R.I.D.A.Y. (senão "Host key verification failed").
# sh test_ssh.sh
F=$(dirname "$0")/Dockerfile; f=0
for l in "UserKnownHostsFile /srv/friday/home/.ssh/known_hosts" "IdentityFile /srv/friday/secrets/haos_ssh" "/etc/ssh/ssh_config.d/friday.conf"; do
  grep -qF "$l" "$F" && echo "ok Dockerfile: $l" || { echo "ERRO Dockerfile sem: $l"; f=1; }
done
exit $f
