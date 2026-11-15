import { formatBytes, statusText, imageInfo } from "./ota-format.js";

// "Imagem de provisionamento": version, date, size and the download button of the newest satellite image.
class FridayOtaCard extends HTMLElement {
  static getStubConfig(hass) {
    const e = Object.keys(hass?.states ?? {}).find((k) => /^sensor\.friday_.*_image_latest$/.test(k));
    return { entity: e ?? "sensor.friday_server_image_latest" };
  }

  setConfig(config) {
    if (!config.entity) throw new Error("entity is required");
    this._config = config;
    this._images = [];
    this._error = "";
    this._note = "";
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._render();
  }

  getCardSize() { return 3; }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    const st = hass.states[this._config?.entity];
    const sig = st ? `${st.state}|${st.last_changed}` : "";
    if (first || sig !== this._sig) {
      this._sig = sig;
      this._load();
    }
  }

  async _load() {
    if (!this._hass || !this._config) return;
    try {
      this._images = (await this._hass.callApi("GET", "friday/image")).images ?? [];
      this._error = "";
    } catch (err) {
      this._images = [];
      this._error = "lista de imagens indisponível (somente administradores)";
    }
    this._render();
  }

  async _download(file) {
    this._note = "";
    try {
      const { path } = await this._hass.callWS({ type: "auth/sign_path", path: `/api/friday/image/${encodeURIComponent(file)}`, expires: 300 });
      const a = document.createElement("a");
      a.href = path;
      a.download = file;
      a.click();
    } catch (err) {
      this._note = "não foi possível iniciar o download";
    }
    this._render();
  }

  _render() {
    if (!this.shadowRoot || !this._config) return;
    const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
    const st = this._hass?.states[this._config.entity];
    const info = imageInfo(st?.state, st?.attributes, this._images);
    const date = info.builtAt ? info.builtAt.toLocaleString([], { hour12: false }) : "—";
    const body = info.tag
      ? `<div class="grid"><span>Versão</span><b>${esc(info.tag)}</b><span>Construída em</span><b>${esc(date)}</b>
         <span>Tamanho</span><b>${esc(formatBytes(info.size))}</b><span>Situação</span><b>${esc(statusText(info.status))}</b></div>
         ${info.error ? `<div class="err">${esc(info.error)}</div>` : ""}
         ${info.file ? `<button class="dl" data-file="${esc(info.file)}">Baixar</button>` : ""}`
      : `<div class="empty">Nenhuma imagem construída ainda.</div>`;
    this.shadowRoot.innerHTML = `<style>
      .card{padding:12px;background:var(--ha-card-background,var(--card-background-color));border-radius:var(--ha-card-border-radius,12px);color:var(--primary-text-color)}
      h3{margin:0 0 8px;font-weight:500}.grid{display:grid;grid-template-columns:auto 1fr;gap:4px 16px}.grid span{color:var(--secondary-text-color)}
      .dl{margin-top:10px;border:none;background:var(--primary-color);color:var(--text-primary-color,#fff);border-radius:14px;padding:6px 16px;cursor:pointer}
      .empty,.err,.note{padding:8px 0;color:var(--secondary-text-color)}.err,.note{color:var(--error-color)}
    </style><div class="card"><h3>Imagem de provisionamento</h3>${body}
      ${this._error ? `<div class="note">${esc(this._error)}</div>` : ""}${this._note ? `<div class="note">${esc(this._note)}</div>` : ""}</div>`;
    this.shadowRoot.querySelectorAll(".dl").forEach((b) => b.addEventListener("click", () => this._download(b.dataset.file)));
  }
}

customElements.define("friday-ota-card", FridayOtaCard);
window.customCards = window.customCards ?? [];
window.customCards.push({ type: "friday-ota-card", name: "F.R.I.D.A.Y. provisioning image",
  description: "Newest satellite image with version, date, size and download" });
