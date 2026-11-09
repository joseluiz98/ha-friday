"""Primeiro boot do satélite: só a identidade (spec 2026-11-05 §6.2). Roda uma vez, como root, antes do programa e do
agente (`friday-firstboot.service`). Idempotente: nunca troca id, config, chave de envio nem estado de pareamento
que já existam. Sem rede, sem cérebro, nada pesado."""
import json, os, re, secrets, shutil, subprocess, sys

import configuracao

ETC = "etc/friday-satelite"
SSH = "home/jose/.ssh"
DONE = ".firstboot-done"


def serial_id(cpuinfo_text: str) -> str:
    """`sat-` + últimos 6 hex (minúsculos) do Serial da Pi; sem serial (ou só zeros) -> aleatório."""
    m = re.search(r"^Serial\s*:\s*([0-9a-fA-F]+)\s*$", cpuinfo_text or "", re.M)
    if m and int(m.group(1), 16) != 0:
        return "sat-" + m.group(1)[-6:].lower().rjust(6, "0")
    return "sat-" + secrets.token_hex(3)


def mic_from_ring(kind: str) -> str:
    return "xvf3800" if kind == "xvf3800" else "jabra-speak-410"


def gerar_chave(caminho: str, comentario: str):
    """ed25519 sem senha (ssh-keygen)."""
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", comentario, "-f", caminho],
                   check=True, stdout=subprocess.DEVNULL)


def _escrever(caminho, texto, modo=0o644):
    with open(caminho, "w") as f:
        f.write(texto)
    os.chmod(caminho, modo)


def _dono_jose(root, caminhos):
    """Em produção (root == "/") o programa roda como `jose` e grava em /etc/friday-satelite."""
    if root != "/":
        return
    try:
        import pwd
        u = pwd.getpwnam("jose")
    except (ImportError, KeyError):
        return
    for c in caminhos:
        for pasta, dirs, arqs in os.walk(c) if os.path.isdir(c) else [(os.path.dirname(c), [], [os.path.basename(c)])]:
            for n in [""] + dirs + arqs:
                try:
                    os.chown(os.path.join(pasta, n) if n else pasta, u.pw_uid, u.pw_gid)
                except OSError:
                    pass


def run(root: str, cpuinfo_text: str, mic: str, keygen=None) -> dict:
    etc = os.path.join(root, ETC)
    ssh = os.path.join(root, SSH)
    os.makedirs(etc, exist_ok=True)
    id_path = os.path.join(etc, "id")
    created = []

    if os.path.exists(os.path.join(etc, DONE)):
        with open(id_path) as f:
            return {"id": f.read().strip(), "created": []}

    if not os.path.exists(id_path):
        _escrever(id_path, serial_id(cpuinfo_text) + "\n")
        created.append("id")
    with open(id_path) as f:
        sid = f.read().strip()

    cfg_path = os.path.join(etc, "config.json")
    if not os.path.exists(cfg_path):
        cfg = dict(configuracao.PADROES)
        cfg["senha_porta_local"] = secrets.token_urlsafe(24)
        cfg["comodo"] = None
        configuracao.gravar(cfg_path, cfg)  # 600
        created.append("config.json")

    if not os.path.exists(os.path.join(etc, "ha_token")) and not os.path.exists(os.path.join(etc, "pairable")):
        _escrever(os.path.join(etc, "pairable"), "")
        created.append("pairable")

    if not os.path.exists(os.path.join(etc, "mic")):
        _escrever(os.path.join(etc, "mic"), mic + "\n")
        created.append("mic")

    chave = os.path.join(ssh, "id_satsync")
    if not os.path.exists(chave):
        os.makedirs(ssh, exist_ok=True)
        os.chmod(ssh, 0o700)
        (keygen or gerar_chave)(chave, sid + "-satsync")
        created.append("id_satsync")

    _escrever(os.path.join(etc, DONE), "")
    _dono_jose(root, [etc, os.path.join(root, "home/jose/.ssh")])
    return {"id": sid, "created": created}


def main():
    with open("/proc/cpuinfo") as f:
        cpuinfo = f.read()
    import ring
    r = run("/", cpuinfo, mic_from_ring(ring.detect_ring()))
    print(json.dumps(r), flush=True)


if __name__ == "__main__":
    main()
