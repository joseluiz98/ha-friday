import { causeText, rowsFromHistory, fmt } from "./activations-format.js";

const PERIODS = [[24, "24 h"], [72, "3 d"], [168, "7 d"]];
const ALL_TYPES = ["conversation", "rejected", "shadow"];

class FridayActivationsCard extends HTMLElement {
  static getStubConfig(hass) {
    const e = Object.keys(hass?.states ?? {}).find((k) => /^event\.friday_.*_activation$/.test(k));
    return { entity: e ?? "event.friday_sat_activation" };
  }

  setConfig(config) {
    if (!config.entity) throw new Error("entity is required");
    this._config = config;
    this._entities = Array.isArray(config.entity) ? config.entity : [config.entity];
    this._hours = config.hours ?? 24;
    this._types = new Set(config.types ?? ALL_TYPES);
    this._rows = [];
    this._error = "";
    this._notes = {};
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._render();
  }

  getCardSize() { return 6; }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    const sig = (this._entities ?? []).map((e) => hass.states[e]?.last_changed).join("|");
    if (first || sig !== this._sig) {
      this._sig = sig;
      this._load();
    }
  }

  async _load() {
    const hass = this._hass;
    if (!hass || !this._entities) return;
    const end = new Date();
    const start = new Date(end.getTime() - this._hours * 3600e3);
    try {
      const resp = await hass.callWS({
        type: "history/history_during_period",
        start_time: start.toISOString(), end_time: end.toISOString(),
        entity_ids: this._entities, minimal_response: false, no_attributes: false,
        significant_changes_only: false,
      });
      this._rows = rowsFromHistory(resp, this._entities);
      this._error = "";
    } catch (err) {
      this._error = err?.message ?? "history unavailable";
    }
    this._render();
  }

  _deviceId(entityId) { return this._hass.entities?.[entityId]?.device_id; }

  async _play(ev) {
    const btn = ev.currentTarget;
    const key = btn.dataset.key;
    const [entityId, name] = [btn.dataset.entity, btn.dataset.rec];
    const dev = this._deviceId(entityId);
    this._notes[key] = "";
    if (!dev) { this._notes[key] = "satellite offline"; return this._render(); }
    try {
      const { path } = await this._hass.callWS({
        type: "auth/sign_path", path: `/api/friday/recording/${dev}/${encodeURIComponent(name)}`,
      });
      const res = await fetch(path);
      if (res.status === 404) this._notes[key] = "recording not ready";
      else if (res.status === 502) this._notes[key] = "satellite offline";
      else if (!res.ok) this._notes[key] = `error ${res.status}`;
      else {
        this._src = this._src ?? new Audio();
        this._src.src = URL.createObjectURL(await res.blob());
        await this._src.play();
      }
    } catch (err) {
      this._notes[key] = "satellite offline";
    }
    this._render();
  }

  _toggle(type) {
    this._types.has(type) ? this._types.delete(type) : this._types.add(type);
    this._render();
  }

  _render() {
    if (!this.shadowRoot || !this._config) return;
    const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
    const cutoff = Date.now() - this._hours * 3600e3;
    const rows = this._rows.filter((r) => this._types.has(r.type) && r.when.getTime() >= cutoff);
    const chips = ALL_TYPES.map((t) =>
      `<button class="chip ${this._types.has(t) ? "on" : ""}" data-type="${t}">${t}</button>`).join("");
    const periods = PERIODS.map(([h, l]) =>
      `<button class="chip ${this._hours === h ? "on" : ""}" data-hours="${h}">${l}</button>`).join("");
    const body = rows.map((r) => {
      const key = `${r.entityId}|${r.when.getTime()}`;
      const extra = [
        r.verifierScore !== null ? `verifier ${fmt(r.verifierScore)}` : "",
        r.whisper ? "whisper" : "",
      ].filter(Boolean).join(" · ");
      const play = r.recording
        ? `<button class="play" data-key="${esc(key)}" data-entity="${esc(r.entityId)}" data-rec="${esc(r.recording)}">&#9654;</button>` : "";
      return `<div class="row"><span class="t">${r.when.toLocaleTimeString([], { hour12: false })}</span>
        <span class="w">${esc(r.word ?? "—")}</span><span class="b ${esc(r.type)}">${esc(r.type)}</span>
        <span class="c">${esc(causeText(r.cause))}</span>
        <span class="n">${fmt(r.score)} / ${fmt(r.threshold)}</span>
        <span class="n">voice ${fmt(r.voice)} / ${fmt(r.voiceThreshold)}</span>
        <span class="x">${esc(extra)}</span>${play}
        <span class="note">${esc(this._notes[key] ?? "")}</span></div>`;
    }).join("") || `<div class="empty">No activations.</div>`;
    this.shadowRoot.innerHTML = `<style>
      .card{padding:12px;background:var(--ha-card-background,var(--card-background-color));border-radius:var(--ha-card-border-radius,12px);color:var(--primary-text-color)}
      .bar{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}
      .chip{border:1px solid var(--divider-color);background:none;color:inherit;border-radius:14px;padding:3px 10px;cursor:pointer}
      .chip.on{background:var(--primary-color);color:var(--text-primary-color,#fff)}
      .row{display:flex;flex-wrap:wrap;gap:4px 10px;align-items:center;padding:6px 0;border-top:1px solid var(--divider-color);font-size:.9em}
      .t{font-variant-numeric:tabular-nums}.b{border-radius:4px;padding:0 6px;background:var(--secondary-background-color)}
      .b.rejected{color:var(--error-color)}.b.shadow{color:var(--secondary-text-color)}
      .c,.x,.note{color:var(--secondary-text-color)}.note{color:var(--error-color)}
      .play{border:none;background:var(--primary-color);color:var(--text-primary-color,#fff);border-radius:50%;width:28px;height:28px;cursor:pointer}
      .empty,.err{padding:12px;color:var(--secondary-text-color)}.err{color:var(--error-color)}
    </style><div class="card"><div class="bar">${chips}<span style="flex:1"></span>${periods}</div>
      ${this._error ? `<div class="err">${esc(this._error)}</div>` : ""}${body}</div>`;
    this.shadowRoot.querySelectorAll("[data-type]").forEach((b) => b.addEventListener("click", () => this._toggle(b.dataset.type)));
    this.shadowRoot.querySelectorAll("[data-hours]").forEach((b) => b.addEventListener("click", () => { this._hours = Number(b.dataset.hours); this._load(); }));
    this.shadowRoot.querySelectorAll(".play").forEach((b) => b.addEventListener("click", (e) => this._play(e)));
  }
}

customElements.define("friday-activations-card", FridayActivationsCard);
window.customCards = window.customCards ?? [];
window.customCards.push({ type: "friday-activations-card", name: "F.R.I.D.A.Y. activations",
  description: "Satellite wake-word activations with details and playback" });
