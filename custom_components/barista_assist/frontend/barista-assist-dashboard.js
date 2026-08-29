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
          <textarea id="manual" readonly rows="6"></textarea>
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
        #manual {
          display: none;
          width: 100%;
          box-sizing: border-box;
          margin-top: 8px;
          font-family: monospace;
          font-size: 0.85rem;
        }
      </style>`;
    this.shadowRoot.getElementById("copy").addEventListener("click", () => this._copy());
  }

  set hass(hass) {
    this._hass = hass;
  }

  async _copy() {
    const button = this.shadowRoot.getElementById("copy");
    const status = this.shadowRoot.getElementById("status");
    const manual = this.shadowRoot.getElementById("manual");
    button.disabled = true;
    manual.style.display = "none";
    status.textContent = "Preparing export…";
    try {
      const result = await this._hass.callWS({ type: "barista_assist/export_shots_text" });
      try {
        await navigator.clipboard.writeText(result.text);
        status.textContent = "Copied to clipboard.";
      } catch (_clipboardError) {
        // The Clipboard API can be unavailable in the companion app's webview
        // as well as some browser contexts. Rather than trying to script a
        // copy across the shadow-DOM boundary (unreliable), show the text
        // directly so the user can select and copy it themselves.
        manual.value = result.text;
        manual.style.display = "block";
        manual.focus();
        manual.select();
        status.textContent =
          "Clipboard access isn't available here. The text below is selected — copy it with Ctrl/Cmd+C.";
      }
    } catch (error) {
      status.textContent = `Export failed: ${error?.message || error}`;
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
