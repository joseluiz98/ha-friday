// Pure helpers for friday-activations-card (no DOM, unit-tested with node --test).

const CAUSES = { verifier: "verifier", no_voice: "no human voice", patience: "patience" };

export function causeText(cause) {
  return CAUSES[cause] ?? (cause ? String(cause) : "");
}

export function fmt(x) {
  return x === null || x === undefined || Number.isNaN(Number(x)) ? "—" : Number(x).toFixed(2);
}

const num = (v) => (typeof v === "number" ? v : null);

// resp: compressed history response {entity_id: [{s, a, lu}]}; newest first.
export function rowsFromHistory(resp, entityIds) {
  const rows = [];
  for (const entityId of entityIds) {
    for (const st of resp?.[entityId] ?? []) {
      const when = new Date(st.s);
      if (typeof st.s !== "string" || Number.isNaN(when.getTime())) continue;
      const a = st.a ?? {};
      rows.push({
        entityId,
        when,
        type: a.event_type ?? "",
        word: a.word ?? null,
        cause: a.cause ?? null,
        score: num(a.score),
        threshold: num(a.threshold),
        voice: num(a.voice),
        voiceThreshold: num(a.voice_threshold),
        verifierScore: num(a.verifier_score),
        whisper: a.whisper ?? null,
        recording: a.recording ?? null,
      });
    }
  }
  return rows.sort((x, y) => y.when - x.when);
}
