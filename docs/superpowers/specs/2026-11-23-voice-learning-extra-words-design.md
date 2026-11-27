# Voice learning for every wake word (design, 2026-11-23)

Status: decisions answered and phrases approved by the owner on 23/11; implemented in PR #56 (v0.4.4). Implementation notes: the window names carry the word (`<word>__tentativa_NN`, the principal keeps the old names), marks of the extra words carry `palavra`, sessions of a partial enrolment are merged by appending audio and shifting the marks, and `treino_verificador.py --executar` takes `palavra`, `modelo` and `lv_fixo`. Builds on PR #55 (negatives that always exist).

## 1. Why
The voice-learning program teaches one thing: the person's "Friday". The house has two words, and the second one has
no path at all:
- `friday` has a personal verifier on MK1 (16/10; it rejected 296 candidates against 245 activations in November).
- `sexta_feira` got its verifier by hand on 02/11 (harvested clips, offline test: -95 % false fires, -2/70 positives)
  and exists only on MK1. MK2 has none for either word; a new satellite starts with none.
- Everything in the flow is hard-wired to one word: the base model (`MODELO_BASE = friday.onnx`), the recorded
  phrases ("diga Friday"), the single output file (`friday_verificador.pkl`), the single config key (`verificador`)
  and the gate (`vf.REFERENCIA_PALAVRA`, one `limiar_palavra`).
Goal: the same spoken session enrols the person for every word the satellite has, end to end, with no manual step,
and a new word added to `palavras_extras` joins the flow without touching the training code.

## 2. Goals / non-goals / anti-goals
- Goals: one session per person; per-word verifier trained and gated on its own; new satellite (MK3) enrols both
  words on day one; adding a third word needs data, not code.
- Non-goals: retraining the base models; changing the gate's product tolerances (`MIN_NOVA`, `FALSAS_PASSAM_MAX`);
  the weekly rejections report (needs the `sexta_feira` logging question answered first).
- Anti-goals: a session that takes so long people abandon it (today 2 min); approving a word without proof against
  non-wake-word audio; one word's failure discarding the other's good result; the assistant describing the mechanism.

## 3. What exists (facts the design relies on)
- Recording: `cadastro.Roteiro` plays intro, 10 beeps 6 s apart (window opens after each beep), a "move 3 m" phrase
  after the 5th, 30 s of free speech, an ending phrase; the `Gravador` keeps `sessao.wav` + `marcas.json`.
- Storage: `~/vozes/<id>/atual/{sessao.wav,marcas.json}` and `cadastro.json`; the last refused session in
  `<id>/reprovado/`.
- Training (`treino_verificador.executar`): base score per 80 ms frame with `MODELO_BASE`, openWakeWord verifier
  features, cross-validated gate against the detector of today, one `.pkl`.
- Detector: `palavra_modelo.criar` loads one verifier per word (`custom_verifier_models`: `friday` from
  `cfg.verificador`, extras from `palavras_extras[i].verificador`) but **one** `custom_verifier_threshold` for all.
- Recorded phrases are fixed WAVs (`sons.aprendizagem_*`), spoken with the Friday voice; their texts live outside the
  repo (`tools/satelite/gerar_falas_aprendizagem.py`) and the generator in the repo imports them.

## 4. Approaches
| | A. One session, one round per word | B. One session per word (separate requests) | C. One round, train every word from it |
|---|---|---|---|
| What the person does | "Friday" x10, move, then "Sexta-feira" x10, then free speech once | asks for the program twice | says only "Friday"; the second word is trained on the same audio |
| Duration | ~3.5 min | 2 + 2 min, two conversations | 2 min |
| Quality | each word has its own positives, in two places | same as A | a verifier for a word never heard: wrong |
| Cost | 2-3 new recorded phrases | same, plus a prompt to ask for the second round | none |
Recommendation: **A**. C is not an option (no positives of the word). B doubles the ceremony and loses the chance to
share the free-speech negatives and the "move" step.

## 5. Flow (A)
1. `aprendizagem_de_voz` returns the script and the words this satellite will record: every word in its config
   (`friday` + `palavras_extras`) that has recorded phrases (decision 1: every word in the config). A person already enrolled for some words is
   offered only the missing ones (decision 4).
2. `comecar_aprendizagem_de_voz(nome)` as today. The Pi builds the script from the word list:
   intro(word1) -> 5 beeps -> "move" -> 5 beeps -> [transition(word2) -> 5 beeps -> "move" -> 5 beeps] -> free speech
   (no word) -> ending. The move step happens once per word so each word has both positions.
3. Recording keeps one `sessao.wav` and marks named `<word>/tentativa_NN`; layout
   `~/vozes/<id>/atual/{sessao.wav, marcas.json}` stays, marks gain the word prefix (old marks = `friday`, so
   existing enrolments need no migration).
4. Training runs one word at a time, never in parallel (one heavy process on the Pi 3), `nice 19`: friday, then each
   extra. Each word: its base model (`<word>.onnx`), its negatives (PR #55: own ambient or baseline, same for every
   word, free speech excluded around **any** word), its people (`pessoa:<id>` carry-over per word), its gate, its
   `.pkl` (`~/modelos/<word>_verificador.pkl`).
5. Each word is approved or refused alone; the result lists both ("a sua voz foi aprendida para as duas palavras" /
   "só para Friday: ..."). A refused word keeps its previous verifier.
6. On approval the config is updated per word: `verificador` for `friday`, `palavras_extras[i].verificador`
   and `limiar` for the others (today the code writes only the first).

## 6. The shared verifier threshold
`custom_verifier_threshold` is one number for every word. Rule: a word being trained is evaluated at the threshold
already in force (`cfg.limiar_verificador`) and only the word threshold (`limiar`) is chosen from the table; the first
word ever enrolled on a satellite picks the threshold as today. If a word passes only at another threshold, it is
refused with that reason (honest, and the owner can decide to retune). Alternative left out on purpose: patching
openWakeWord for per-word thresholds.

## 7. Recorded phrases (paid, needs permission)
Today (generated 15-16/10, voice Aoede):
| File | Text now |
|---|---|
| `aprendizagem_intro` | "Vamos começar o aprendizado da sua voz. Depois de cada bipe, diga Friday uma vez, do jeito que você costuma me chamar. São dez vezes." |
| `aprendizagem_mudar_lugar` | "Muito bem. Agora vá para outro lugar do cômodo, a uns três metros daqui, e continue depois de cada bipe." |
| `aprendizagem_fala_livre` | "Ótimo. Agora fale qualquer coisa por trinta segundos, sem dizer Friday. Pode contar como foi o seu dia." |
| `aprendizagem_fim` | "Pronto. Agora eu vou aprender a sua voz. Em alguns minutos eu aviso o resultado." |

Proposal: 2 files change, 3 stay.
- **new** `aprendizagem_intro_<word>` (one per extra word; first: `aprendizagem_intro_sexta_feira`): "Agora a próxima palavra.
  Depois de cada bipe, diga Sexta-feira uma vez, do jeito que você costuma me chamar. Também são dez vezes."
- **replaced** `aprendizagem_fala_livre`, worded so it is true for one word or many: "Ótimo. Agora fale qualquer coisa
  por trinta segundos, sem repetir as palavras que você acabou de dizer. Pode contar como foi o seu dia."
- unchanged: `intro` (the first word, as today), `mudar_lugar` (reused in each round), `fim`, `cancelada`.
Up to 4 attempts each, transcript checked against the text: cents (< R$ 1, estimate). A word without its intro file
is left out of the session and the result says so (no tone fallback: a beep cannot tell which word to say). Move
`gerar_falas_aprendizagem.py` (FALAS) into `satellite/voice_lines/`. Ship order: audio first (vault manifest), code after.

## 8. Voice texts (transparency)
The roteiro returned to the voice lists beeps, moving and speaking per word, never who does it. The words come from the
config, so the texts are built, not written per word. Same test as PR #55 greps every text for "satélite",
"cérebro", "modelo".

## 9. Decisions (owner, 23/11)
1. Which words: every word in the satellite's config. The shadow/active mode is per word and per satellite and has no
   bearing on enrolment.
2. Approach A approved; phrase wordings (section 7) pending his approval.
3. Generation of the 2 phrases: yes, after he sees the texts.
4. A person already enrolled only for Friday is offered just the missing word: yes.
5. A word that passes only at another threshold is refused with the reason: yes.

## 10. Tests and rollout
- Pure: word list from config, script building (durations from recorded phrases), mark naming, per-word layout and
  carry-over, threshold rule, config update per word, result text without internals.
- Satellite program suite runs it with the openWakeWord-free parts; the training part is proven on the Pi with a
  recorded session replayed per word (offline, one process at a time).
- Release: one PR if it stays under 20 files, otherwise phrases + script first, flow after. Alpha gate: MK1 enrols
  "Sexta-feira" for the person already enrolled for Friday and the detector log shows `com verificador` with both
  words; after promotion MK2 enrols both in one session.
