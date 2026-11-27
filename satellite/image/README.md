# Imagem da F.R.I.D.A.Y. Satellite (spec 2026-11-05)

Um cartão gravado com esta imagem vira, sozinho, um satélite que o HA descobre (`_friday._tcp`, `state=pairable`),
fala um código de 4 dígitos e espera ser adotado. Nada da casa vai dentro: nenhum token, id, voz, gravação nem o
verificador (ele vem da VM na adoção).

## Como é feita (e por que não é rpi-image-gen puro)
Base = a imagem **oficial** Raspberry Pi OS Lite 64 bits (Trixie), fixada por URL e pelo sha256 do `.img` (em
`build.sh`). `build.sh` cresce a partição, monta por loop, entra num chroot arm64 (qemu-user-static) e roda
`customize-chroot.sh` com o *payload* montado pelo `stage.sh` a partir dos mestres.

Decisão (ledger de 05/11, Task 12): o `rpi-image-gen` foi estudado mas **não foi possível rodar o spike** (a instalação
dos pacotes de build na VM com `sudo` foi negada pelo classificador de permissões). Lendo o código: o exemplo de
cloud-init do `rpi-image-gen` (`contrib/src/cloud-init`) é um NoCloud com **mídia CIDATA separada**, e o próprio README
diz que o Imager só customiza uma imagem que traga a integração com a semente em `/boot/firmware` — que a imagem
oficial já traz. Partir da oficial mantém Wi-Fi/hostname do Imager funcionando sem reimplementar isso. Trocar de
ferramenta depois muda só `build.sh`/`customize-chroot.sh`.

## Gerada sozinha pela VM (OTA, desde 11/11/2026)
Quando uma release vira **stable** (CI verde; pre-release depois da prova no alfa), o agente da VM gera a imagem
daquela tag sozinho, uma vez, numa unidade separada (`friday-image-build`, `Nice=19`, I/O ocioso, um build por vez):
o `stage.sh` da própria release (`--repo /srv/friday/ota/repo --ref <sha> --local --package <pacote OTA>`) e o
`build.sh`. A imagem **nasce no layout versionado** (`/opt/friday/releases/<tag>`, link `/opt/friday/current`, venv da
release; `~/satelite` só guarda dados: `sons/`, `estado/`) e com as migrações daquela release já marcadas como feitas, de
modo que um cartão gravado com qualquer imagem recente se atualiza pelo OTA logo no primeiro boot. Saída na pasta
`friday/imagens` do compartilhamento `media` do HAOS (`\\<HA>\media\friday\imagens` no PC; fora do backup do HA):
`friday-satellite-<tag>.img.xz`, `.img.xz.sha256`, `.json` (manifesto do Imager); ficam as 2 tags mais novas. Estado em
`/srv/friday/state/ota/image.json` (`ok|building|error`); falha numa tag espera o botão "Gerar imagem" da integração
(não repete sozinha). O caminho manual abaixo continua valendo (sem `--package`, gera o layout antigo, que a migração
0001 converte no primeiro OTA). **Regra das migrações:** a imagem marca como feitas as migrações da própria release;
uma migração nova que muda o sistema precisa ser feita também no `customize-chroot.sh` (ou em `files/`) no mesmo
commit, senão a imagem gerada depois dela a pula. O build trabalha em `/var/lib/friday-image` (~8 GB durante ~1 h,
fora de `/srv/friday`, que entra no backup) e apaga tudo no fim, menos a imagem base baixada; a saída completa do
último build fica em `/srv/friday/state/ota/image-build.log` (0600).

## Gerar à mão
1. No HA: `cd /config/tools/satelite/imagem && ./stage.sh [--sendspin-tarball sendspin-cli-0.3.0-linux-arm64.tar.gz]`
   (monta `/tmp/friday-image-payload`, **recusa** se achar dado da casa — `payload_check.py` — e copia para
   `friday@<VM>:~/imgbuild/src/`).
2. Na VM, uma vez: `sudo apt-get install qemu-user-static binfmt-support parted e2fsprogs` (o `./build.sh --check` lista o
   que falta). O build precisa de root para `losetup`/`mount`/`chroot`.
3. Na VM: `cd ~/imgbuild/src && ./build.sh` (uns 30–60 min; o pip roda emulado). Saída em `/srv/nobackup/images/`:
   `friday-satellite-AAAAMMDD.img.xz`, `.json` (manifesto do Imager), `.img.xz.sha256`. O script verifica a imagem montada
   (`verify_image.sh`: arquivos, unidades habilitadas, nada da casa, imports do venv) antes de comprimir.

O Sendspin (`sendspin-cli`) entra se o tarball for passado ao `stage.sh` (pelo procedimento de
`../sendspin/README.md`, `output = music_vol`). Sem ele a imagem sai sem player de música e o build avisa.

## Gravar (no PC)
O servidor das imagens já está no ar: a unidade `friday-images-http.service` (instalada pelo `server/install.sh`) serve,
só para leitura e só na LAN, a pasta das imagens da VM na porta 8088. O `url` do manifesto aponta para
`http://<VM>:8088/` (mude com `FRIDAY_IMG_URL=… ./build.sh`). Conferir: `curl -sI http://<VM>:8088/<imagem>.img.xz`
devolve 200.
1. Raspberry Pi Imager 2.x → **Opções da aplicação → Repositório de conteúdo → editar → arquivo local** = o `.json`
   do Samba (`media\friday\imagens`). Depois: dispositivo **Raspberry Pi 3** → o item **F.R.I.D.A.Y. Satellite** da
   lista de sistemas. **Não use "Use custom"** na lista: ele é só para `.img` e pula a customização (o Imager ignora
   Wi-Fi, usuário e hostname, e o Pi OS abre o cadastro de usuário).
2. Customização: **Wi-Fi da rede de 2,4 GHz** (a Pi 3 não tem 5 GHz), hostname livre (apelido humano, p. ex.
   `friday-satellite2`; o id vem do número de série). Deixe o usuário em branco ou `jose`: o usuário `jose` já existe,
   sem senha, só com a chave pública do HA (`/data/.ssh/id_ed25519.pub`; a privada nunca sai do HA).
3. Gravar, ligar, esperar a Pi falar o código. Meta: da tomada ao "descoberto no HA" em até 10 min.

## O que validar depois de gravar
- Hostname e Wi-Fi do Imager **persistiram** (`hostname`, `nmcli`), a Pi aparece em Configurações → Dispositivos (F.R.I.D.A.Y.).
- `systemctl status friday-firstboot` (oneshot, ok) e a ausência de `/etc/friday-satelite/.firstboot-done` antes do 1º boot.
- Depois do 1º boot: `/etc/friday-satelite/{id,config.json,pairable,mic}` e `~/.ssh/id_satsync(.pub)` existem; o id é
  `sat-` + 6 hex do `Serial` de `/proc/cpuinfo`.
- O código falado funciona (frases pré-gravadas em `~/satelite/sons/onboarding/`, vazio até a Task 15).

## Conteúdo (resumo)
usuário `jose` (sem senha, só a chave do HA, sudo sem senha, grupos `audio`/`plugdev`); Wi-Fi sem economia
(`wifi.powersave = 2`); journal persistente (500 MB); `asound.conf` como link para o arquivo da placa em `/etc/friday-audio/` (0755, legível pelo player de música) (Jabra por padrão, `dmix`); venv `~/oww` com as versões
do satélite de produção (`requirements.lock`, mais `spake2`, `pyusb`, `libusb-package`); modelos `friday`/`sexta_feira`, as duas ativas de fábrica (18/11)
(sem verificador); programa em `~/satelite`, agente em `/opt/friday-satellite-agent` (+ helpers e sudoers); unidades
`friday-firstboot` (antes das outras duas), `friday-satelite`, `friday-satellite-agent`, `friday-sync-recordings.{service,timer}`;
a saúde da Pi vai à integração pelo agente (o antigo `satelite-status`, por webhook, foi aposentado em 16/11); regra udev do XVF3800
(VID 2886). `friday-netwatch.{service,timer}` (a cada 5 min) é a **recuperação automática da rede**, não um aviso:
interface → NetworkManager → reboot (até 3 reboots da própria cadeia em 24 h, estado em `/var/lib/friday-netwatch/`),
luz de erro persistente quando esgota, fala só com não perturbe desligado (spec 2026-11-08-satellite-network-resilience).

## Pendências conhecidas
- Rodar o build de fato (bloqueio de permissão na VM, acima) e validar hostname/Wi-Fi no cartão.
- Perfil de áudio do XVF3800 (`asound.conf` próprio, fluxo silencioso pelo `dmix`) e calibração do anel: prova final de 18–27/11.
- Frases pré-gravadas (`sons/onboarding/<key>.wav`, 48 kHz estéreo): Task 15.

## Testes
`cd /config/tools/satelite/imagem && python3 -m unittest -q tests.test_make_manifest tests.test_payload_check`
