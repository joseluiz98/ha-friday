// Pure helpers for friday-ota-card (no DOM, unit-tested with node --test).

export function formatBytes(n) {
  if (typeof n !== "number" || !Number.isFinite(n) || n < 0) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return `${i === 0 ? v : v.toFixed(v >= 100 ? 0 : 1)} ${units[i]}`;
}

const STATUS = { ok: "pronta", building: "em construção", error: "com erro" };

export function statusText(status) {
  return STATUS[status] ?? (status ? String(status) : "—");
}

// The image the card offers: the sensor's tag (state) matched to the list the integration serves
// ({file, tag, size, mtime, sha256}); the sensor's `built_at` wins over the file date.
export function imageInfo(state, attributes, images) {
  const tag = state && state !== "unknown" && state !== "unavailable" ? state : null;
  const attrs = attributes ?? {};
  const item = (images ?? []).find((i) => i.tag === tag) ?? null;
  const builtAt = attrs.built_at ? new Date(attrs.built_at) : item ? new Date(item.mtime * 1000) : null;
  return {
    tag,
    status: attrs.status ?? null,
    error: attrs.error || "",
    builtAt: builtAt && !Number.isNaN(builtAt.getTime()) ? builtAt : null,
    size: item ? item.size : null,
    file: item ? item.file : null,
    downloadUrl: attrs.download_url ?? (item ? `/api/friday/image/${item.file}` : null),
  };
}
