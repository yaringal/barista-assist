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

const CLASSIFICATION_LABELS = {
  healthy: "Healthy",
  too_fast: "Too fast",
  too_restrictive: "Too restrictive",
  puck_prep_issue: "Puck prep issue",
  invalid_measurement: "Invalid",
};

class BaristaAssistShotHistoryCard extends HTMLElement {
  static getStubConfig() {
    return { title: "Shot history" };
  }

  setConfig(config) {
    this._config = config || {};
    this._shots = null;
    this._loading = false;
    this._error = null;
    this._expandedId = null;
    this._samplesCache = new Map();
    this.attachShadow({ mode: "open" });
    this._render();
  }

  set hass(hass) {
    const firstAssignment = !this._hass;
    this._hass = hass;
    if (firstAssignment) this._loadShots();
  }

  async _loadShots() {
    this._loading = true;
    this._render();
    try {
      const result = await this._hass.callWS({ type: "barista_assist/list_shots" });
      this._shots = result.shots;
      this._error = null;
    } catch (error) {
      this._error = error?.message || String(error);
    } finally {
      this._loading = false;
      this._render();
    }
  }

  async _toggleExpand(shotId) {
    this._expandedId = this._expandedId === shotId ? null : shotId;
    this._render();
    if (this._expandedId && !this._samplesCache.has(shotId)) {
      try {
        const result = await this._hass.callWS({
          type: "barista_assist/shot_samples",
          shot_id: shotId,
        });
        this._samplesCache.set(shotId, result.samples);
      } catch (_error) {
        this._samplesCache.set(shotId, []);
      }
      this._render();
    }
  }

  async _deleteShot(shotId, coffeeName) {
    const label = coffeeName ? ` (${coffeeName})` : "";
    if (!window.confirm(`Delete this shot${label}? This cannot be undone.`)) return;
    try {
      await this._hass.callWS({ type: "barista_assist/delete_shot", shot_id: shotId });
      this._shots = this._shots.filter((shot) => shot.id !== shotId);
      this._samplesCache.delete(shotId);
      if (this._expandedId === shotId) this._expandedId = null;
      this._render();
    } catch (error) {
      window.alert(`Could not delete shot: ${error?.message || error}`);
    }
  }

  _formatDate(iso) {
    if (!iso) return "—";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return iso;
    const pad = (n) => String(n).padStart(2, "0");
    return `${pad(date.getDate())}/${pad(date.getMonth() + 1)} ${pad(date.getHours())}:${pad(
      date.getMinutes()
    )}:${pad(date.getSeconds())}`;
  }

  _formatNumber(value, digits = 1) {
    return typeof value === "number" ? value.toFixed(digits) : "—";
  }

  _formatDuration(ms) {
    return typeof ms === "number" ? `${(ms / 1000).toFixed(1)}s` : "—";
  }

  _renderChart(samples) {
    if (!samples || !samples.length) {
      return `<div class="empty">No samples recorded for this shot.</div>`;
    }
    const width = 600;
    const height = 200;
    const padding = 28;
    const maxT = Math.max(1, ...samples.map((s) => s.elapsed_ms));
    const maxWeight = Math.max(1, ...samples.map((s) => s.weight_g));
    const maxFlow = Math.max(1, ...samples.map((s) => s.flow_g_s));
    const x = (t) => padding + (t / maxT) * (width - 2 * padding);
    const yFor = (max) => (v) => height - padding - (Math.max(0, v) / max) * (height - 2 * padding);
    const yWeight = yFor(maxWeight);
    const yFlow = yFor(maxFlow);
    const path = (accessor) =>
      samples
        .map((s, i) => `${i === 0 ? "M" : "L"}${x(s.elapsed_ms).toFixed(1)},${accessor(s).toFixed(1)}`)
        .join(" ");
    return `
      <svg viewBox="0 0 ${width} ${height}" class="chart" preserveAspectRatio="none">
        <line x1="${padding}" y1="${height - padding}" x2="${width - padding}" y2="${height - padding}" class="axis" />
        <line x1="${padding}" y1="${padding}" x2="${padding}" y2="${height - padding}" class="axis" />
        <path d="${path((s) => yWeight(s.weight_g))}" class="weight-line" fill="none" />
        <path d="${path((s) => yFlow(s.flow_g_s))}" class="flow-line" fill="none" />
      </svg>
      <div class="legend">
        <span class="legend-weight">Weight (max ${this._formatNumber(maxWeight)}g)</span>
        <span class="legend-flow">Flow (max ${this._formatNumber(maxFlow)} g/s)</span>
      </div>`;
  }

  _renderDetail(shot) {
    const samples = this._samplesCache.get(shot.id);
    if (samples === undefined) {
      return `<div class="detail loading">Loading samples…</div>`;
    }
    return `
      <div class="detail">
        <div class="detail-grid">
          <div><b>Slot</b> ${this._escape(shot.slot || "—")}</div>
          <div><b>Status</b> ${this._escape(shot.status || "—")}</div>
          <div><b>Dose</b> ${this._formatNumber(shot.dose_g)}g</div>
          <div><b>Grind</b> ${this._formatNumber(shot.grind, 1)}</div>
          <div><b>Pre-infusion</b> ${this._formatNumber(shot.preinfusion_s)}s (${
            shot.adapt_pi ? "Adapt" : "Machine"
          })</div>
          <div><b>Total duration</b> ${this._formatDuration(shot.stop_command_elapsed_ms)}</div>
          <div><b>Effective stop margin</b> ${this._formatNumber(shot.effective_stop_margin_g)}g</div>
          <div><b>Channeling suspicion</b> ${
            shot.channeling_suspicion != null ? this._formatNumber(shot.channeling_suspicion, 2) : "—"
          }</div>
          <div><b>Roaster</b> ${this._escape(shot.roaster || "—")}</div>
        </div>
        ${this._renderChart(samples)}
      </div>`;
  }

  _renderRow(shot) {
    const expanded = this._expandedId === shot.id;
    const classification = shot.classification || "";
    return `
      <div class="row">
        <div class="summary" data-shot-id="${this._escape(shot.id)}">
          <div class="col date">${this._escape(this._formatDate(shot.started_at))}</div>
          <div class="col coffee">${this._escape(shot.coffee_name || "—")}</div>
          <div class="col classification tag-${this._escape(classification)}">${this._escape(
            CLASSIFICATION_LABELS[classification] || classification || "—"
          )}</div>
          <div class="col yield">${this._formatNumber(shot.actual_yield_g)} / ${this._formatNumber(
            shot.target_yield_g
          )}g</div>
          <button class="delete" data-delete-id="${this._escape(shot.id)}" title="Delete shot">🗑</button>
        </div>
        ${expanded ? this._renderDetail(shot) : ""}
      </div>`;
  }

  _render() {
    const title = this._escape(this._config.title || "Shot history");
    let body;
    if (this._loading && !this._shots) {
      body = `<div class="status">Loading shots…</div>`;
    } else if (this._error) {
      body = `<div class="status error">Could not load shots: ${this._escape(this._error)}</div>`;
    } else if (!this._shots || !this._shots.length) {
      body = `<div class="status">No shots recorded yet.</div>`;
    } else {
      body = `
        <div class="row header-row">
          <div class="col date">Started</div>
          <div class="col coffee">Coffee</div>
          <div class="col classification">Result</div>
          <div class="col yield">Yield</div>
          <div class="col spacer"></div>
        </div>
        ${this._shots.map((shot) => this._renderRow(shot)).join("")}`;
    }
    this.shadowRoot.innerHTML = `
      <ha-card>
        <div class="wrap">
          <div class="title">${title}</div>
          ${body}
        </div>
      </ha-card>
      <style>
        .wrap { padding: 16px; }
        .title { font-size: 1.1rem; font-weight: 600; margin-bottom: 12px; }
        .status { opacity: 0.7; padding: 8px 0; }
        .status.error { color: var(--error-color, #c62828); }
        .header-row {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 4px 4px 8px;
          font-weight: 600;
          opacity: 0.7;
          font-size: 0.85rem;
        }
        .row { border-bottom: 1px solid var(--divider-color, rgba(127,127,127,0.25)); }
        .summary {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 10px 4px;
          cursor: pointer;
        }
        .summary:hover { background: var(--secondary-background-color, rgba(127,127,127,0.08)); }
        .col { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .col.date { flex: 1.4; }
        .col.coffee { flex: 1.2; }
        .col.classification { flex: 1.3; }
        .col.yield { flex: 0.9; text-align: right; }
        .col.spacer { width: 32px; }
        .tag-healthy { color: var(--success-color, #2e7d32); }
        .tag-too_fast, .tag-too_restrictive { color: var(--warning-color, #ef6c00); }
        .tag-puck_prep_issue, .tag-invalid_measurement { color: var(--error-color, #c62828); }
        button.delete {
          border: 0;
          background: none;
          cursor: pointer;
          font-size: 1rem;
          padding: 4px 6px;
          opacity: 0.6;
          border-radius: 8px;
        }
        button.delete:hover { opacity: 1; background: var(--secondary-background-color, rgba(127,127,127,0.12)); }
        .detail { padding: 12px 8px 16px; }
        .detail.loading { opacity: 0.7; }
        .detail-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
          gap: 6px 16px;
          margin-bottom: 12px;
          font-size: 0.9rem;
        }
        .detail-grid b { font-weight: 600; margin-right: 4px; }
        svg.chart { width: 100%; height: auto; }
        .axis { stroke: var(--divider-color, rgba(127,127,127,0.4)); stroke-width: 1; }
        .weight-line { stroke: #2196f3; stroke-width: 2; }
        .flow-line { stroke: #00bcd4; stroke-width: 1.5; }
        .legend { display: flex; gap: 16px; font-size: 0.8rem; opacity: 0.8; margin-top: 4px; }
        .legend-weight::before, .legend-flow::before { content: "—"; margin-right: 4px; font-weight: 700; }
        .legend-weight::before { color: #2196f3; }
        .legend-flow::before { color: #00bcd4; }
        .empty { opacity: 0.7; padding: 8px 0; }
      </style>`;
    this._attachListeners();
  }

  _attachListeners() {
    this.shadowRoot.querySelectorAll(".summary").forEach((el) => {
      el.addEventListener("click", () => this._toggleExpand(el.dataset.shotId));
    });
    this.shadowRoot.querySelectorAll(".delete").forEach((el) => {
      el.addEventListener("click", (event) => {
        event.stopPropagation();
        const shot = (this._shots || []).find((s) => s.id === el.dataset.deleteId);
        this._deleteShot(el.dataset.deleteId, shot?.coffee_name);
      });
    });
  }

  _escape(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  getCardSize() {
    return 6;
  }
}

customElements.define("barista-assist-shot-history-card", BaristaAssistShotHistoryCard);
if (!window.customCards.some((item) => item.type === "barista-assist-shot-history-card")) {
  window.customCards.push({
    type: "barista-assist-shot-history-card",
    name: "Barista Assist shot history",
    description: "Browse, inspect, and delete stored Barista Assist shots.",
    preview: true,
  });
}
