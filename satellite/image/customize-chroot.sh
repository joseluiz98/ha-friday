#!/bin/bash
# Runs INSIDE the arm64 chroot of the base image (as root, under qemu-user-static), called by build.sh.
# Everything it needs is in /payload (copied there by build.sh and removed at the end). Idempotent enough to re-run.
# Nothing from the house goes in: no token, no id, no voice, no recording (spec 2026-11-05 §6.1).
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
P=/payload
H=/home/jose

echo "== packages"
apt-get update -q
apt-get install -y -q --no-install-recommends \
  python3 python3-venv python3-pip alsa-utils libasound2-plugins libportaudio2 libusb-1.0-0 rsync openssh-client \
  libavahi-compat-libdnssd1 libpipewire-0.3-0 ca-certificates curl xz-utils network-manager

echo "== user jose (no password, key only) + sudo + groups"
id jose >/dev/null 2>&1 || useradd -m -s /bin/bash -G audio,plugdev,video,netdev,gpio,i2c,spi jose 2>/dev/null \
  || useradd -m -s /bin/bash jose
usermod -aG audio,plugdev jose
passwd -l jose >/dev/null
install -d -m 0700 -o jose -g jose $H/.ssh
install -m 0600 -o jose -g jose $P/ha_authorized_key.pub $H/.ssh/authorized_keys
install -m 0440 $P/files/etc/sudoers-jose-nopasswd /etc/sudoers.d/010-jose-nopasswd
visudo -cf /etc/sudoers.d/010-jose-nopasswd >/dev/null

echo "== system settings"
install -D -m 0644 $P/files/etc/NM-wifi-powersave.conf /etc/NetworkManager/conf.d/wifi-powersave.conf
install -D -m 0644 $P/files/etc/journald-50-persistente.conf /etc/systemd/journald.conf.d/50-persistente.conf
install -D -m 0644 $P/files/etc/99-respeaker.rules /etc/udev/rules.d/99-respeaker.rules
install -D -m 0644 $P/files/etc/70-jabra-satelite.rules /etc/udev/rules.d/70-jabra-satelite.rules  # hidraw do Jabra para o grupo plugdev (botão, anel)
install -D -m 0644 $P/files/etc/tmpfiles-friday-gravacoes.conf /etc/tmpfiles.d/friday-gravacoes.conf
install -m 0644 $P/asound.conf /etc/asound.conf
mkdir -p /var/log/journal

echo "== program, models, sounds"
install -d -o jose -g jose $H/satelite $H/modelos $H/satelite-sync
cp -a $P/programa/. $H/satelite/
install -d -o jose -g jose $H/satelite/sons/onboarding   # the pre-recorded phrases land here (task 15)
cp -a $P/modelos/. $H/modelos/
install -m 0755 $P/sync/sync_recordings.sh $H/satelite-sync/sync_recordings.sh
install -d -o jose -g jose -m 0755 /etc/friday-satelite   # firstboot fills it
install -m 0644 $P/house.json /etc/friday-satelite/house.json   # VM address (from the VM local config at build time)
install -m 0644 $P/FRIDAY_VERSION $H/satelite/FRIDAY_VERSION

echo "== agent"
install -d /opt/friday-satellite-agent
cp -a $P/agent/friday_satellite_agent $P/agent/friday_agent_core /opt/friday-satellite-agent/
install -m 0755 $P/agent/friday-wifi-apply /usr/local/sbin/friday-wifi-apply
install -m 0755 $P/agent/friday-agent-apply /usr/local/sbin/friday-agent-apply
install -m 0440 $P/agent/sudoers.friday-satellite-agent /etc/sudoers.d/friday-satellite-agent
visudo -cf /etc/sudoers.d/friday-satellite-agent >/dev/null

echo "== python venv (the versions of the satellite in production)"
runuser -u jose -- python3 -m venv $H/oww
runuser -u jose -- $H/oww/bin/pip install --no-cache-dir -q --upgrade pip
runuser -u jose -- $H/oww/bin/pip install --no-cache-dir -q --no-deps -r $P/requirements.lock  # lock = pip freeze completo da Pi; o openwakeword de produção foi instalado sem o tflite-runtime (não existe para o Python 3.13)
# openWakeWord keeps its feature models (melspectrogram, embedding, VAD) outside the wheel
runuser -u jose -- $H/oww/bin/python -c "import openwakeword.utils as u; u.download_models(['alexa'])" || \
  runuser -u jose -- $H/oww/bin/python -c "import openwakeword.utils as u; u.download_models()"

echo "== Sendspin player (optional: only when the tarball was provided)"
if [ -f $P/sendspin/sendspin-cli.tar.gz ]; then
  tar -xzf $P/sendspin/sendspin-cli.tar.gz --strip-components=1 -C / --wildcards '*/usr'
  systemd-sysusers || true
  install -m 0644 $P/sendspin/sendspin-cli.conf /etc/sendspin-cli.conf
  systemctl enable sendspin-cli.service
else
  echo "WARNING: no Sendspin tarball in the payload; the image has no music player (see README)" >&2
fi

echo "== units"
for u in friday-firstboot.service friday-satelite.service friday-satellite-agent.service \
         friday-sync-recordings.service friday-sync-recordings.timer friday-netwatch.service friday-netwatch.timer \
         satelite-status.service satelite-status.timer; do
  install -m 0644 $P/units/$u /etc/systemd/system/$u
done
install -m 0755 $P/units/satelite-status.sh /usr/local/bin/satelite-status.sh
systemctl enable ssh.service friday-firstboot.service friday-satelite.service friday-satellite-agent.service \
  friday-sync-recordings.timer friday-netwatch.timer
# satelite-status needs /etc/satelite/webhook_id (a secret of the house): installed, NOT enabled

echo "== ownership and cleanup"
chown -R jose:jose $H
rm -f /etc/ssh/ssh_host_*            # regenerated on the first boot of each card
# "uninitialized", as in the official image: an EMPTY file is NOT a first boot (machine-id(5)), and then the host keys
# removed above are never regenerated, sshd does not start and the partition is not expanded (MK2 test, 06/11)
printf 'uninitialized\n' > /etc/machine-id
rm -rf /var/lib/apt/lists/* /root/.cache /tmp/* /payload
apt-get clean
echo "== done"
