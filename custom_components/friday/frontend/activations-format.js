// Pure helpers for friday-activations-card (no DOM, unit-tested with node --test).

const CAUSES = { verifier: "verifier", no_voice: "no human voice", patience: "patience" };

export function causeText(cause) {
  return CAUSES[cause] ?? (cause ? String(cause) : "");
}

// Note shown on the card for a non-OK answer of the recording proxy ("" when OK).
export function playNote(status) {
  if (status >= 200 && status < 300) return "";
  if (status === 404) return "recording not ready";
  if (status === 403) return "satellite refused";
  if (status === 502) return "satellite offline";
  return `error ${status}`;
}

export function fmt(x) {
  return x === null || x === undefined || Number.isNaN(Number(x)) ? "—" : Number(x).toFixed(2);
}

const ACTIVATION_RE = /^event\.friday_.*_activation$/;

// Every satellite activation event entity in a hass.states map, sorted.
export function activationEntities(states) {
  return Object.keys(states ?? {}).filter((k) => ACTIVATION_RE.test(k)).sort();
}

// Human label of the satellite behind an entity: device name_by_user, else name, else the entity id.
export function satelliteLabel(hass, entityId) {
  const deviceId = hass?.entities?.[entityId]?.device_id;
  const dev = deviceId ? hass?.devices?.[deviceId] : null;
  return dev?.name_by_user || dev?.name || entityId;
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
