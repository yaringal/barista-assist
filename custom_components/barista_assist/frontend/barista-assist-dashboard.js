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

  _isCompanionApp() {
    return isCompanionApp();
  }

  _showManualCopy(text, message) {
    const status = this.shadowRoot.getElementById("status");
    const manual = this.shadowRoot.getElementById("manual");
    manual.value = text;
    manual.style.display = "block";
    manual.focus();
    manual.select();
    status.textContent = message;
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
      if (this._isCompanionApp()) {
        // The Companion app's WebView clipboard can silently truncate a
        // large writeText() call - it doesn't throw, so the try/catch
        // fallback below would never trigger and "Copied to clipboard"
        // would lie. Go straight to the manual textarea instead of trusting
        // it here.
        this._showManualCopy(
          result.text,
          "Please copy manually from the textbox."
        );
        return;
      }
      try {
        await navigator.clipboard.writeText(result.text);
        status.textContent = "Copied to clipboard.";
      } catch (_clipboardError) {
        // The Clipboard API can be unavailable in some browser contexts.
        // Rather than trying to script a copy across the shadow-DOM
        // boundary (unreliable), show the text directly so the user can
        // select and copy it themselves.
        this._showManualCopy(
          result.text,
          "Please copy manually from the textbox."
        );
      }
    } catch (error) {
      status.textContent = `Export failed: ${error?.message || error}`;
    } finally {
      button.disabled = false;
    }
  }

  _escape(value) {
    return escapeHtml(value);
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

// The Companion app injects window.externalApp (iOS) or window.externalBus
// (Android) for its native bridge - the same check Home Assistant's own
// frontend uses to detect running inside the app. Shared by both
// BaristaAssistExportCard's whole-database export and
// BaristaAssistShotHistoryCard's per-row export - the Companion app's
// WebView clipboard risk (silent truncation on a large writeText()) applies
// equally to a single shot's data, just less likely to actually bite.
function isCompanionApp() {
  return typeof window.externalApp !== "undefined" || typeof window.externalBus !== "undefined";
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

// Shared by the Shots view's per-shot detail chart and the Live Shot card -
// both plot [elapsed_ms, weight_g, flow_g_s] points against elapsed seconds
// since the shot's own start, not real wall-clock time (see
// BaristaRuntime._shot_plot_points's docstring for why that matters).
function niceStepSeconds(maxSeconds) {
  const raw = Math.max(1, maxSeconds) / 5; // aim for ~5 tick marks
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const residual = raw / magnitude;
  let step;
  if (residual > 5) step = 10 * magnitude;
  else if (residual > 2) step = 5 * magnitude;
  else if (residual > 1) step = 2 * magnitude;
  else step = magnitude;
  return Math.max(1, step);
}

const CHART_STYLES = `
  .chart-wrap { position: relative; touch-action: none; }
  svg.chart { width: 100%; height: auto; display: block; }
  .axis { stroke: var(--divider-color, rgba(127,127,127,0.4)); stroke-width: 1; }
  .weight-line { stroke: #2196f3; stroke-width: 2; }
  .flow-line { stroke: #00bcd4; stroke-width: 1.5; }
  .cursor-line { stroke: var(--secondary-text-color, #888); stroke-width: 1; stroke-dasharray: 3,3; display: none; }
  .axis-labels { position: relative; height: 16px; font-size: 0.7rem; opacity: 0.6; }
  .axis-labels span { position: absolute; transform: translateX(-50%); white-space: nowrap; }
  .chart-tooltip {
    display: none;
    position: absolute;
    top: 4px;
    transform: translateX(-50%);
    background: var(--card-background-color, #fff);
    border: 1px solid var(--divider-color, rgba(127,127,127,0.4));
    border-radius: 4px;
    padding: 2px 6px;
    font-size: 0.7rem;
    white-space: nowrap;
    pointer-events: none;
    box-shadow: 0 1px 3px rgba(0,0,0,0.2);
  }
  .legend { display: flex; gap: 16px; font-size: 0.8rem; opacity: 0.8; margin-top: 4px; }
  .legend-weight::before, .legend-flow::before { content: "—"; margin-right: 4px; font-weight: 700; }
  .legend-weight::before { color: #2196f3; }
  .legend-flow::before { color: #00bcd4; }
  .legend-target::before { content: "- -"; margin-right: 4px; font-weight: 700; color: var(--success-color, #2e7d32); }
  .empty { opacity: 0.7; padding: 8px 0; }
  .pi-band { fill: var(--secondary-text-color, #888); opacity: 0.08; }
  .healthy-band { fill: var(--success-color, #2e7d32); opacity: 0.08; }
  .target-line { stroke: var(--success-color, #2e7d32); stroke-width: 1.5; stroke-dasharray: 5,4; }
  .stop-line { stroke: var(--error-color, #c62828); stroke-width: 1.5; stroke-dasharray: 2,3; }
  .event-labels { position: relative; height: 14px; font-size: 0.65rem; opacity: 0.75; }
  .event-labels span { position: absolute; transform: translateX(-50%); white-space: nowrap; }
  .event-labels .stop-label { color: var(--error-color, #c62828); }
`;

// The "healthy" time window for a shot - the same [too_fast_factor,
// too_restrictive_factor] * expected_s range flow_analysis.py's
// analyze_shot itself classifies duration_s against (expected_s = pi +
// target_yield_g/expected_flow_g_s; expected_flow_g_s is post-pre-infusion/
// extraction-only, so pi is added on top rather than folded into the rate -
// see runtime.py's ActiveShot.expected_flow_g_s/_shot_markers). Stopping
// anywhere inside this window, at target_yield_g, is what "healthy" means -
// too_fast_factor/too_restrictive_factor come from markers (not
// re-derived/hardcoded here) so this can never silently diverge from what
// actually classified the shot.
//
// Deliberately a window, not a single point or a full drawn trajectory
// from press to target (an earlier version of this chart drew a straight
// line to one "expected completion" point): real flow isn't constant
// across a pour (there's a ramp-up period this project doesn't yet have
// enough real shot data to model - see flow_analysis.py's own analyze_shot
// "Open caveat" comment), so a specific predicted instant implies a
// precision that isn't real. The window's own edges, and target_yield_g
// itself, ARE backed by the same math classification uses; the path in
// between them isn't a claim this chart should make.
// Returns null until every figure is actually known.
function healthyWindow(markers) {
  const { preinfusion_ms, expected_flow_g_s, target_yield_g, too_fast_factor, too_restrictive_factor } = markers || {};
  if (expected_flow_g_s == null || target_yield_g == null || too_fast_factor == null || too_restrictive_factor == null) {
    return null;
  }
  const pi = preinfusion_ms ?? 0;
  const expectedMs = pi + (target_yield_g / expected_flow_g_s) * 1000;
  return {
    start_ms: expectedMs * too_fast_factor,
    end_ms: expectedMs * too_restrictive_factor,
    target_yield_g,
  };
}

// Cosmetic-only smoothing for the flow line: real per-reading flow_g_s is
// naturally jumpy (it's a derivative of noisy scale readings) even for a
// perfectly healthy shot. A simple time-windowed moving average - for each
// sample, average flow_g_s over every sample within windowMs/2 of its own
// elapsed_ms. Purely a chart-rendering concern: independent of, and never
// fed back into, anything the backend computes from the real flow_g_s
// (classification, grind correction, storage) - display only. O(n^2) in
// sample count, which is fine at a shot's actual sample counts (a few
// hundred at most) and this only re-runs on state changes, not per frame.
function smoothedFlowSeries(samples, windowMs = 600) {
  const half = windowMs / 2;
  return samples.map((sample) => {
    const inWindow = samples.filter((s) => Math.abs(s.elapsed_ms - sample.elapsed_ms) <= half);
    const avg = inWindow.reduce((sum, s) => sum + s.flow_g_s, 0) / inWindow.length;
    return { ...sample, flow_g_s: avg };
  });
}

// Shared geometry between renderShotChart's static markup and
// attachChartTooltip's hit-testing, so the two can never drift out of sync.
// markers (see runtime.py's _shot_markers) factors the healthy window's own
// far edge and target_yield_g into maxT/maxWeight too, so the chart
// auto-scales to show the whole window even when the real shot hasn't
// caught up to (or has overshot) it yet.
function chartGeometry(samples, markers = {}) {
  const width = 600;
  const height = 200;
  const padding = 28;
  const healthy = healthyWindow(markers);
  const extraT = healthy ? [healthy.end_ms] : [];
  const extraWeight = healthy ? [healthy.target_yield_g] : [];
  const maxT = Math.max(1, ...samples.map((s) => s.elapsed_ms), ...extraT);
  const maxWeight = Math.max(1, ...samples.map((s) => s.weight_g), ...extraWeight);
  const maxFlow = Math.max(1, ...samples.map((s) => s.flow_g_s));
  const x = (t) => padding + (t / maxT) * (width - 2 * padding);
  const yFor = (max) => (v) => height - padding - (Math.max(0, v) / max) * (height - 2 * padding);
  return { width, height, padding, maxT, maxWeight, maxFlow, x, yWeight: yFor(maxWeight), yFlow: yFor(maxFlow) };
}

// samples: [{elapsed_ms, weight_g, flow_g_s}, ...]. markers: see
// runtime.py's _shot_markers - {preinfusion_ms, stop_command_elapsed_ms,
// expected_flow_g_s, target_yield_g}, any of which may be null/absent (not
// yet known, or nothing to show). SVG text isn't used for the axis labels
// because preserveAspectRatio="none" (needed so the chart fills its card
// regardless of aspect ratio) non-uniformly scales - and distorts - any
// text drawn inside the same viewBox; plain positioned HTML spans avoid
// that entirely. The touch/hover tooltip (attachChartTooltip) and the
// event-time labels below follow the same rule.
function renderShotChart(samples, markers = {}) {
  if (!samples || !samples.length) {
    return `<div class="empty">No samples recorded for this shot.</div>`;
  }
  const { width, height, padding, maxT, maxWeight, maxFlow, x, yWeight, yFlow } = chartGeometry(samples, markers);
  const pathFor = (points, yAccessor) =>
    points
      .map((p, i) => `${i === 0 ? "M" : "L"}${x(p.elapsed_ms).toFixed(1)},${yAccessor(p).toFixed(1)}`)
      .join(" ");
  const smoothedFlow = smoothedFlowSeries(samples);
  const healthy = healthyWindow(markers);

  const step = niceStepSeconds(maxT / 1000);
  const ticks = [];
  for (let t = 0; t <= maxT / 1000 + 0.001; t += step) ticks.push(t);
  const axisLabels = ticks
    .map(
      (t) => `<span style="left:${((x(t * 1000) / width) * 100).toFixed(2)}%">${Math.round(t)}s</span>`
    )
    .join("");

  const preinfusionMs = markers?.preinfusion_ms;
  const piBand =
    preinfusionMs > 0
      ? `<rect x="${padding}" y="${padding}" width="${(x(preinfusionMs) - padding).toFixed(1)}" height="${height - 2 * padding}" class="pi-band" />`
      : "";
  const healthyBand = healthy
    ? `<rect x="${x(healthy.start_ms).toFixed(1)}" y="${padding}" width="${(x(healthy.end_ms) - x(healthy.start_ms)).toFixed(1)}" height="${height - 2 * padding}" class="healthy-band" />`
    : "";
  const targetLine = healthy
    ? `<line x1="${x(healthy.start_ms).toFixed(1)}" y1="${yWeight(healthy.target_yield_g).toFixed(1)}" x2="${x(healthy.end_ms).toFixed(1)}" y2="${yWeight(healthy.target_yield_g).toFixed(1)}" class="target-line" />`
    : "";
  const stopMs = markers?.stop_command_elapsed_ms;
  const hasStopMarker = stopMs != null;
  const stopLine = hasStopMarker
    ? `<line class="stop-line" x1="${x(stopMs).toFixed(1)}" y1="${padding}" x2="${x(stopMs).toFixed(1)}" y2="${height - padding}" />`
    : "";
  const eventLabels = hasStopMarker
    ? `<div class="event-labels"><span class="stop-label" style="left:${((x(stopMs) / width) * 100).toFixed(2)}%">Stop</span></div>`
    : "";

  return `
    <div class="chart-wrap">
      <svg viewBox="0 0 ${width} ${height}" class="chart" preserveAspectRatio="none">
        ${piBand}
        ${healthyBand}
        <line x1="${padding}" y1="${height - padding}" x2="${width - padding}" y2="${height - padding}" class="axis" />
        <line x1="${padding}" y1="${padding}" x2="${padding}" y2="${height - padding}" class="axis" />
        ${stopLine}
        <path d="${pathFor(samples, (s) => yWeight(s.weight_g))}" class="weight-line" fill="none" />
        <path d="${pathFor(smoothedFlow, (s) => yFlow(s.flow_g_s))}" class="flow-line" fill="none" />
        ${targetLine}
        <line class="cursor-line" x1="0" y1="${padding}" x2="0" y2="${height - padding}" />
      </svg>
      <div class="axis-labels">${axisLabels}</div>
      ${eventLabels}
      <div class="chart-tooltip"></div>
    </div>
    <div class="legend">
      <span class="legend-weight">Weight (max ${maxWeight.toFixed(1)}g)</span>
      <span class="legend-flow">Flow (max ${maxFlow.toFixed(1)} g/s)</span>
      ${healthy ? `<span class="legend-target">Target yield</span>` : ""}
    </div>`;
}

// Feature flag: flip to false (or remove this function's call sites) to
// fully disable the hover/touch tooltip below without touching the rest of
// the chart - kept as a single, self-contained addition specifically so it
// can be ripped out cleanly if it doesn't work well in practice (Android
// touch-drag-vs-scroll conflicts especially).
const CHART_TOOLTIP_ENABLED = true;

// Wires up a hover (mouse) / drag (touch) tooltip on a chart already
// inserted into the DOM by renderShotChart's output. Pointer events unify
// mouse and touch handling - the mousedown/touchstart split isn't needed.
// Must be called again after every re-render, since innerHTML replacement
// discards any previously-attached listeners along with the old elements.
function attachChartTooltip(root, samples, markers = {}) {
  if (!CHART_TOOLTIP_ENABLED || !samples || !samples.length) return;
  const wrap = root.querySelector(".chart-wrap");
  const svg = wrap?.querySelector("svg.chart");
  const cursorLine = wrap?.querySelector(".cursor-line");
  const tooltip = wrap?.querySelector(".chart-tooltip");
  if (!wrap || !svg || !cursorLine || !tooltip) return;

  // markers must match whatever was passed to the renderShotChart call
  // that produced this DOM - the idealized curve can inflate maxT/the x
  // scale, and a mismatched geometry here would put the cursor line and
  // tooltip in the wrong place relative to the paths actually drawn.
  const { width, x } = chartGeometry(samples, markers);
  // Same smoothing the flow line itself is drawn with, so the tooltip's
  // flow reading matches what the cursor is actually pointing at instead
  // of contradicting it with a raw, jumpier number.
  const smoothedFlow = smoothedFlowSeries(samples);

  const nearestIndex = (svgX) =>
    samples.reduce(
      (nearest, sample, i) =>
        Math.abs(x(sample.elapsed_ms) - svgX) < Math.abs(x(samples[nearest].elapsed_ms) - svgX) ? i : nearest,
      0
    );

  const show = (clientX) => {
    const rect = svg.getBoundingClientRect();
    const svgX = ((clientX - rect.left) / rect.width) * width;
    const index = nearestIndex(svgX);
    const sample = samples[index];
    const px = x(sample.elapsed_ms);
    cursorLine.setAttribute("x1", px.toFixed(1));
    cursorLine.setAttribute("x2", px.toFixed(1));
    cursorLine.style.display = "block";
    tooltip.style.display = "block";
    tooltip.style.left = `${((px / width) * 100).toFixed(2)}%`;
    tooltip.textContent = `${(sample.elapsed_ms / 1000).toFixed(1)}s · ${sample.weight_g.toFixed(1)}g · ${smoothedFlow[index].flow_g_s.toFixed(1)} g/s`;
  };

  const hide = () => {
    cursorLine.style.display = "none";
    tooltip.style.display = "none";
  };

  wrap.addEventListener("pointermove", (event) => show(event.clientX));
  wrap.addEventListener("pointerdown", (event) => show(event.clientX));
  wrap.addEventListener("pointerup", hide);
  wrap.addEventListener("pointercancel", hide);
  wrap.addEventListener("pointerleave", hide);
}

const CLASSIFICATION_LABELS = {
  healthy: "Healthy",
  too_fast: "Too fast",
  too_restrictive: "Too restrictive",
  puck_prep_issue: "Puck prep issue",
  invalid_measurement: "Invalid",
};

// Mirrors runtime_shared.py's _FLAVOR_TAG_LABELS - kept in sync by hand
// since this card has no access to that Python-side dict.
const FLAVOR_TAG_LABELS = {
  sour_sharp: "Sour / Sharp",
  bitter_harsh: "Bitter / Harsh",
  thin_weak: "Thin / Weak",
  dry_astringent: "Dry / Astringent",
  balanced: "Balanced",
};

// A plain emoji (📋) here was reported invisible on at least one desktop
// browser/OS while rendering fine on mobile - inconsistent emoji font
// coverage, not a code bug (🗑 next to it happens to be more universally
// covered). An inline SVG has no font dependency at all, so it renders
// identically everywhere; fill="currentColor" picks up button.export's own
// color/opacity from CSS the same way the emoji glyph used to.
const EXPORT_ICON_SVG = `<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" style="vertical-align:middle"><path d="M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z"/></svg>`;

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

  async _exportShot(shotId, coffeeName, button) {
    try {
      const result = await this._hass.callWS({
        type: "barista_assist/export_shots_text",
        shot_id: shotId,
      });
      if (!isCompanionApp()) {
        try {
          await navigator.clipboard.writeText(result.text);
          this._flashExportButton(button, "✅");
          return;
        } catch (_clipboardError) {
          // fall through to the manual copy modal below
        }
      }
      // window.prompt used to be the fallback here, but it's a single-line
      // native dialog that collapses every newline in the exported text
      // (tab-delimited sample rows become one unreadable line) - matches
      // BaristaAssistExportCard's own <textarea readonly> fallback instead,
      // which genuinely preserves line breaks.
      this._showManualCopyModal(result.text, coffeeName);
    } catch (error) {
      window.alert(`Could not export shot: ${error?.message || error}`);
    }
  }

  // Appended to document.body, not this.shadowRoot: _render() replaces
  // this card's whole shadow-DOM innerHTML wholesale (e.g. on the next
  // websocket update), which would otherwise wipe the modal out from under
  // an open copy dialog.
  _showManualCopyModal(text, coffeeName) {
    const overlay = document.createElement("div");
    overlay.innerHTML = `
      <style>
        .barista-export-modal-backdrop {
          position: fixed; inset: 0; background: rgba(0, 0, 0, 0.5);
          display: flex; align-items: center; justify-content: center;
          z-index: 1000; padding: 16px; box-sizing: border-box;
        }
        .barista-export-modal {
          background: var(--card-background-color, #fff);
          color: var(--primary-text-color, #000);
          border-radius: 8px; padding: 16px; max-width: 600px; width: 100%;
          box-sizing: border-box;
        }
        .barista-export-modal p { margin: 0 0 8px; opacity: 0.8; }
        .barista-export-modal textarea {
          width: 100%; box-sizing: border-box; font-family: monospace;
          font-size: 0.85rem; margin-bottom: 12px;
        }
        .barista-export-modal button {
          border: 0; border-radius: 999px; padding: 8px 16px; font: inherit;
          font-weight: 600; background: var(--primary-color, #03a9f4);
          color: var(--text-primary-color, #fff); cursor: pointer;
        }
      </style>
      <div class="barista-export-modal-backdrop">
        <div class="barista-export-modal">
          <p>Copy ${this._escape(coffeeName || "this shot")}'s data - select all and copy from the textbox below.</p>
          <textarea readonly rows="12"></textarea>
          <button type="button">Close</button>
        </div>
      </div>`;
    const textarea = overlay.querySelector("textarea");
    textarea.value = text;
    const close = () => overlay.remove();
    overlay.querySelector("button").addEventListener("click", close);
    overlay.querySelector(".barista-export-modal-backdrop").addEventListener("click", (event) => {
      if (event.target === event.currentTarget) close();
    });
    document.body.appendChild(overlay);
    textarea.focus();
    textarea.select();
  }

  // Brief success feedback in place of the export button's own icon, since
  // this card (unlike BaristaAssistExportCard) has no persistent status
  // line to write into - just this one row's button. innerHTML, not
  // textContent: the button's own icon is EXPORT_ICON_SVG markup, which
  // has no text content to save/restore - textContent here would silently
  // blank the button for good after the first successful copy.
  _flashExportButton(button, icon) {
    if (!button) return;
    const original = button.innerHTML;
    button.textContent = icon;
    setTimeout(() => {
      button.innerHTML = original;
    }, 1200);
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

  // renderShotChart/attachChartTooltip's markers shape (see runtime.py's
  // _shot_markers) built from a stored shot row - list_shots/recent_shots
  // already carries every field needed, no extra fetch required.
  _shotMarkers(shot) {
    return {
      preinfusion_ms: typeof shot.preinfusion_s === "number" ? shot.preinfusion_s * 1000 : null,
      stop_command_elapsed_ms: shot.stop_command_elapsed_ms ?? null,
      expected_flow_g_s: shot.expected_flow_g_s ?? null,
      target_yield_g: shot.target_yield_g ?? null,
    };
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
          <div><b>Extraction tag</b> ${this._escape(
            FLAVOR_TAG_LABELS[shot.flavor_extraction_tag] || "—"
          )}</div>
          <div><b>Mouthfeel tag</b> ${this._escape(
            FLAVOR_TAG_LABELS[shot.flavor_mouthfeel_tag] || "—"
          )}</div>
        </div>
        ${renderShotChart(samples, this._shotMarkers(shot))}
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
          <button class="export" data-export-id="${this._escape(shot.id)}" title="Export this shot">${EXPORT_ICON_SVG}</button>
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
        .col.spacer { width: 68px; }
        .tag-healthy { color: var(--success-color, #2e7d32); }
        .tag-too_fast, .tag-too_restrictive { color: var(--warning-color, #ef6c00); }
        .tag-puck_prep_issue, .tag-invalid_measurement { color: var(--error-color, #c62828); }
        button.export, button.delete {
          border: 0;
          background: none;
          cursor: pointer;
          font-size: 1rem;
          padding: 4px 6px;
          opacity: 0.6;
          border-radius: 8px;
        }
        button.export:hover, button.delete:hover {
          opacity: 1;
          background: var(--secondary-background-color, rgba(127,127,127,0.12));
        }
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
        ${CHART_STYLES}
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
    this.shadowRoot.querySelectorAll(".export").forEach((el) => {
      el.addEventListener("click", (event) => {
        event.stopPropagation();
        const shot = (this._shots || []).find((s) => s.id === el.dataset.exportId);
        this._exportShot(el.dataset.exportId, shot?.coffee_name, el);
      });
    });
    if (this._expandedId) {
      const shot = (this._shots || []).find((s) => s.id === this._expandedId);
      attachChartTooltip(
        this.shadowRoot,
        this._samplesCache.get(this._expandedId),
        shot ? this._shotMarkers(shot) : {}
      );
    }
  }

  _escape(value) {
    return escapeHtml(value);
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

// Replaced an apexcharts-card-based graph: that card's rolling time window
// always tracks real "now", so a finished shot anchored to its own real
// press time would silently scroll out of view as wall-clock time passed
// (see BaristaRuntime._shot_plot_points's docstring). This plots the same
// [elapsed_ms, weight_g, flow_g_s] points (the status entity's shot_plot
// attribute) with the exact chart renderShotChart already uses for the
// Shots view's per-shot detail chart - elapsed seconds since the shot's
// own start, not real time - so a frozen shot just has nothing to do with
// "now" and never needs to be re-anchored.
class BaristaAssistLiveShotCard extends HTMLElement {
  static getStubConfig() {
    return { entity: "sensor.barista_assist_status", title: "Weight & flow rate" };
  }

  setConfig(config) {
    if (!config?.entity) {
      throw new Error("barista-assist-live-shot-card requires an `entity`");
    }
    this._config = config;
    this._lastState = undefined;
    this.attachShadow({ mode: "open" });
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    const state = hass.states[this._config.entity];
    if (state === this._lastState) return; // this entity didn't change - nothing to redraw
    this._lastState = state;
    this._render();
  }

  _render() {
    const title = escapeHtml(this._config.title || "Live shot");
    const rawPoints = this._lastState?.attributes?.shot_plot || [];
    const samples = rawPoints.map(([elapsed_ms, weight_g, flow_g_s]) => ({
      elapsed_ms,
      weight_g,
      flow_g_s,
    }));
    const markers = this._lastState?.attributes?.shot_markers || {};
    this.shadowRoot.innerHTML = `
      <ha-card>
        <div class="wrap">
          <div class="title">${title}</div>
          ${renderShotChart(samples, markers)}
        </div>
      </ha-card>
      <style>
        .wrap { padding: 16px; }
        .title { font-size: 1.1rem; font-weight: 600; margin-bottom: 12px; }
        ${CHART_STYLES}
      </style>`;
    attachChartTooltip(this.shadowRoot, samples, markers);
  }

  getCardSize() {
    return 3;
  }
}

customElements.define("barista-assist-live-shot-card", BaristaAssistLiveShotCard);
if (!window.customCards.some((item) => item.type === "barista-assist-live-shot-card")) {
  window.customCards.push({
    type: "barista-assist-live-shot-card",
    name: "Barista Assist live shot",
    description: "Live weight/flow chart for the active or last completed shot.",
    preview: true,
  });
}
