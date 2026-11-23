# Voice training: negatives that always exist (design, 2026-11-23)

## Why
On 23/11 the voice-learning program on MK2 recorded the whole session and then failed at the training step with
`FileNotFoundError: /home/jose/vozes/rotulos.json`. The verifier training assumed the 15/10 archive (`rotulos.json`,
`_negativos/`) that only MK1 has. A future MK3 must be able to run the program the day it arrives. The same session
showed the assistant telling the person "o satélite conduzirá o processo": it copied the wording of the tool
descriptions, and the satellite/brain/model split is meant to be invisible to whoever is talking.

## Decisions (owner, 23/11)
- A satellite with enough ambient audio of its own learns from it. Without enough, the training mixes in negatives the
  VM already knows from the other satellites, measured at the moment of the training. There is always a baseline.
- MK2 is not trained with a stopgap: the owner redoes it when the release with this fix is promoted to stable
  (a one-shot Home Assistant automation tells him).
- One PR for the full design; split in two if it passes 20 files (it does not).

## Design
1. **Negatives source** (`satellite/program/negativos.py`, pure, no network):
   - own ambient (`~/gravacoes/ambiente`) ≥ `MIN_AMBIENTE_S` (30 min) → used alone, `ALVO_NEGATIVOS_S` (15 min)
     spread over the days it has;
   - below that → own ambient plus the baseline folder `~/vozes/_baseline`: other satellites' false activations (all)
     and ambient (equal share per source) to complete the 15 min;
   - `_negativos/` (MK1 archive) still counts when it exists; `rotulos.json` missing means empty labels.
2. **Gate**: fewer than `MIN_NEGATIVOS_S` (5 min) of negatives in total → not approved, with a reason that says what is
   missing and when to try again. Nothing is ever approved without a proof against non-wake-word audio.
3. **Baseline pack** (VM, `agents/server/.../negatives.py`): `GET /v1/training/negatives` on the OTA TLS port, the
   satellite's own bearer. It selects from `incoming/<other satellites>/gravacoes/ambiente` and
   `vozes/_negativos` only (never `vozes/<person>`), skips files modified in the last 10 min, skips symlinks, caps at
   30 min and 90 MB, answers 204 when the house knows nothing yet.
4. **Agent** (`agents/satellite/.../negatives.py`): `POST /v1/local/negatives` (loopback, local password) fetches the
   pack, accepts only flat `sat-xxxxxx__*.wav` regular files within size caps (anything else refuses the whole pack),
   unpacks into `~/vozes/_baseline` owned by the voices' user. The program deletes the folder when the training ends.
   A failure never stops the training: the gate decides with what exists.
5. **Voice**: the tool descriptions, the rehearsal script and the system instruction no longer name who does what
   (satellite, brain, model). The system instruction states the fact: whoever talks perceives one assistant; the rest
   is the mechanism inside.

## Not in this PR (and why)
- Training the `sexta_feira` verifier in the same session: it needs new recorded prompts for the word (paid
  ElevenLabs audio, needs the owner's permission).
- A weekly report of verifier rejections per word and satellite: `sexta_feira` logs no `barrada_verificador` at all
  (110 activations and 1124 near-misses since 02/11), so first find out whether extras are not logged or never reject.

## Effects on MK1
Its next training also uses 15 min of its ambient as negatives (more evidence for the gate), and `rotulos.json` is
read leniently. The gate criteria themselves did not change.
