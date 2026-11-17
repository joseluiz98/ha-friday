# Simulated satellite

Test tool for wake-word arbitration (spec 2026-11-16-satellite-arbitration) when only one real satellite
(the MK1) exists. The simulator talks to the gateway like a satellite: it keeps presence alive (so the MK1 sees
"another satellite online" and asks the gateway before acting) and sends wake claims with a chosen voice level
and score. Standard library only (runs on the VM host python). The gateway certificate is self-signed, so the
simulator does not verify it. The token is read from a file and never printed.

## Registration (done once, by the operator, never by the tool)

**On the VM everything under `/srv/friday/state` needs `sudo`:** the directory is `0700` and the files `0600`, owned
by root (the gateway container writes them). User `friday` cannot read the token or the arbitration log, so the
commands below that touch `/srv/friday/state` (and every `sim.py` run on the VM) use `sudo`. Without it the
simulator stops at once with "permission denied ... run the simulator with sudo" (it never polls an unreadable log
silently).

The simulator authenticates with its own entry in `/share/friday/satellites.json`. The id **must** match
`sat-[0-9a-f]{6}` or the gateway silently drops the entry; `sat-f510a1` is the simulator's id (sorts after the
real satellites, so a "deliver to the owner" never picks it). The password must be at least 32 characters.

1. Generate the password straight into the token file (nothing is printed), on the VM:

   ```bash
   sudo mkdir -p -m 700 /srv/friday/state/arbitration
   sudo sh -c "umask 077; python3 -c 'import secrets; print(secrets.token_urlsafe(48))' > /srv/friday/state/arbitration/sim.token"
   sudo chmod 600 /srv/friday/state/arbitration/sim.token
   ```

2. Look at the structure of the registry without printing passwords:

   ```bash
   python3 -c 'import json; d=json.load(open("/share/friday/satellites.json")); print({k: sorted(v) for k, v in d.items()})'
   ```

   Each entry has `comodo`, `senha`, `ip` and `player`.

3. Add the entry (the password goes from the token file into the registry, never to the terminal). Keep a
   backup first; run where both files are readable (copy the token over, or run the same snippet on each side
   if the registry lives on the HAOS share). On the VM the token is root-only: run the snippet with
   `sudo python3 - <<'PY'`.

   ```bash
   cp /share/friday/satellites.json /share/friday/satellites.json.bak
   python3 - <<'PY'
   import json, os
   reg = "/share/friday/satellites.json"
   token = open("/srv/friday/state/arbitration/sim.token").read().strip()
   d = json.load(open(reg))
   d["sat-f510a1"] = {"comodo": "simulator", "senha": token, "ip": "192.0.2.1", "player": ""}
   tmp = reg + ".tmp"
   fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
   with os.fdopen(fd, "w") as f:
       json.dump(d, f, indent=2)
   os.replace(tmp, reg)
   PY
   ```

   `192.0.2.1` is a documentation address: a delivery that ever targets the simulator fails fast. The gateway
   re-reads the registry; if it caches it, restart the gateway container.

### Removal (when the tests are over)

```bash
python3 - <<'PY'
import json, os
reg = "/share/friday/satellites.json"
d = json.load(open(reg))
d.pop("sat-f510a1", None)
tmp = reg + ".tmp"
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    json.dump(d, f, indent=2)
os.replace(tmp, reg)
PY
sudo shred -u /srv/friday/state/arbitration/sim.token
```

## Usage

```bash
# on the VM the token file and the arbitration log are root-only: sudo
# make the MK1 see another satellite online (presence every 4 s, Ctrl-C to stop)
sudo python3 sim.py --gateway https://<vm-address>:7443 --token-file /srv/friday/state/arbitration/sim.token --presence-only

# one claim; prints the verdict JSON {"status", "verdict", "reason", "round_id"}
sudo python3 sim.py --gateway https://<vm-address>:7443 --token-file /srv/friday/state/arbitration/sim.token \
    --claim --voice-dbfs -20 --score 0.9 [--delay-ms 0]

# on the VM: keep presence and claim whenever ANOTHER satellite opens a round
sudo python3 sim.py --gateway https://127.0.0.1:7443 --token-file /srv/friday/state/arbitration/sim.token \
    --claim-loop --voice-dbfs -20 --score 0.9 [--delay-ms 0]
```

`--claim-loop` polls today's `/srv/friday/state/arbitration/YYYY-MM-DD.ndjson` (America/Sao_Paulo day; override
with `--arbitration-dir` or `FRIDAY_ARBITRATION_DIR`) every 20 ms for new `round_opened` records, ignoring the
simulator's own (`--sid`, default `sat-f510a1`), so it joins the MK1's round inside the 200 ms window. Use
`--voice-dbfs` louder than the MK1's to make the simulator win, quieter to make it lose; `--delay-ms` delays the
claim to test late arrivals.

If the gateway is down or the token is wrong the tool reports only the HTTP status or the exception class. A log
file that does not exist yet (no round today) is fine; an unreadable one ends the run with a non-zero exit.

## Tests

```bash
cd tools/satellite_sim && python3 -m unittest -q
```
