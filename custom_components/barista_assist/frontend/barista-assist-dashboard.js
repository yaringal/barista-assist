class BaristaAssistDashboardStrategy extends HTMLElement {
  static getCreateSuggestions(_hass) {
    return {
      title: "Barista Assist",
      icon: "mdi:coffee-maker",
    };
  }

  static async generate(_config, hass) {
    return hass.callWS({ type: "barista_assist/get_dashboard" });
  }
}

customElements.define(
  "ll-strategy-dashboard-barista-assist",
  BaristaAssistDashboardStrategy,
);

window.customStrategies = window.customStrategies || [];
if (!window.customStrategies.some((item) => item.type === "barista-assist")) {
  window.customStrategies.push({
    type: "barista-assist",
    strategyType: "dashboard",
    name: "Barista Assist",
    description: "A clean espresso workflow dashboard managed by the Barista Assist integration.",
  });
}


class BaristaAssistExportCard extends HTMLElement {
  static getStubConfig() {
    return { title: "Shot data", button_text: "Copy all shot data" };
  }

  setConfig(config) {
    this._config = config || {};
    this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `
      <ha-card>
        <div class="wrap">
          <div class="title">${this._escape(this._config.title || "Shot data")}</div>
          <div class="subtitle">Copy every stored shot and raw scale time series for diagnosis.</div>
          <button id="copy" type="button">${this._escape(this._config.button_text || "Copy all shot data")}</button>
          <div id="status" aria-live="polite"></div>
        </div>
      </ha-card>
      <style>
        .wrap { padding: 16px; }
        .title { font-size: 1.1rem; font-weight: 600; margin-bottom: 4px; }
        .subtitle { opacity: 0.7; margin-bottom: 12px; line-height: 1.4; }
        button {
          border: 0;
          border-radius: 999px;
          padding: 10px 16px;
          font: inherit;
          font-weight: 600;
          background: var(--primary-color);
          color: var(--text-primary-color);
          cursor: pointer;
        }
        button:disabled { opacity: 0.55; cursor: wait; }
        #status { margin-top: 8px; min-height: 1.2em; opacity: 0.8; }
      </style>`;
    this.shadowRoot.getElementById("copy").addEventListener("click", () => this._copy());
  }

  set hass(hass) {
    this._hass = hass;
  }

  async _copy() {
    const button = this.shadowRoot.getElementById("copy");
    const status = this.shadowRoot.getElementById("status");
    button.disabled = true;
    status.textContent = "Preparing export…";
    try {
      const result = await this._hass.callWS({ type: "barista_assist/export_shots_text" });
      try {
        await navigator.clipboard.writeText(result.text);
      } catch (_clipboardError) {
        const area = document.createElement("textarea");
        area.value = result.text;
        area.style.position = "fixed";
        area.style.opacity = "0";
        this.shadowRoot.appendChild(area);
        area.focus();
        area.select();
        if (!document.execCommand("copy")) {
          throw new Error("Browser clipboard access is unavailable");
        }
        area.remove();
      }
      status.textContent = "Copied to clipboard. You can paste it directly here.";
    } catch (error) {
      status.textContent = `Copy failed: ${error?.message || error}`;
    } finally {
      button.disabled = false;
    }
  }

  _escape(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }
}

customElements.define("barista-assist-export-card", BaristaAssistExportCard);
window.customCards = window.customCards || [];
if (!window.customCards.some((item) => item.type === "barista-assist-export-card")) {
  window.customCards.push({
    type: "barista-assist-export-card",
    name: "Barista Assist shot export",
    description: "Copy all stored Barista Assist shot time series to the clipboard.",
    preview: true,
  });
}
