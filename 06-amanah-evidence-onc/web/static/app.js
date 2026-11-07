/* Onco-Evidence web client — vanilla JS over the engine API (mounted at /api).
   Flow: POST /api/analyze -> poll GET /api/report/{id} -> render. */

const API = "/api";
const CALIBRATION_API = "/ui/calibration";
const META_ANALYSIS_API = "/ui/meta-analysis";
const POLL_MS = 1500;
const POLL_MAX = 120; // ~3 min ceiling

const $ = (id) => document.getElementById(id);

const views = {
  input: $("inputView"),
  working: $("workingView"),
  rejected: $("rejectedView"),
  error: $("errorView"),
  results: $("resultsView"),
};

const calibrationView = $("calibrationView");
const calibrationPanels = {
  loading: $("calibrationLoading"),
  error: $("calibrationError"),
  empty: $("calibrationEmpty"),
  content: $("calibrationContent"),
};

const metaAnalysisView = $("metaAnalysisView");
const metaAnalysisPanels = {
  loading: $("metaAnalysisLoading"),
  error: $("metaAnalysisError"),
  empty: $("metaAnalysisEmpty"),
  content: $("metaAnalysisContent"),
};

let appMode = "case";
let lastCaseView = "input";

function prefersReducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function scrollTop() {
  window.scrollTo({ top: 0, behavior: prefersReducedMotion() ? "auto" : "smooth" });
}

function updateNav() {
  const setCurrent = (id, on) => {
    const btn = $(id);
    if (on) btn.setAttribute("aria-current", "page");
    else btn.removeAttribute("aria-current");
  };
  setCurrent("casesNav", appMode === "case");
  setCurrent("calibrationNav", appMode === "calibration");
  setCurrent("metaAnalysisNav", appMode === "meta-analysis");
}

function show(name) {
  appMode = "case";
  lastCaseView = name;
  calibrationView.hidden = true;
  metaAnalysisView.hidden = true;
  for (const [k, el] of Object.entries(views)) el.hidden = k !== name;
  views.input.hidden = !(name === "input");
  if (name === "results" || name === "working" || name === "rejected" || name === "error") {
    views.input.hidden = true;
  }
  updateNav();
  scrollTop();
}

/* ---------- auth token (optional) ---------- */
const TOKEN_KEY = "onco_api_token";
const tokenInput = $("apiToken");
tokenInput.value = localStorage.getItem(TOKEN_KEY) || "";
tokenInput.addEventListener("input", () => {
  localStorage.setItem(TOKEN_KEY, tokenInput.value.trim());
});
function authHeaders(extra = {}) {
  const t = (localStorage.getItem(TOKEN_KEY) || "").trim();
  return t ? { ...extra, Authorization: `Bearer ${t}` } : extra;
}
/* Export/provenance go through fetch (so the Authorization header is sent),
   then open as a blob — plain <a href> can't carry auth headers. */
async function openExport(jobId, path, label) {
  try {
    const res = await fetch(`${API}/report/${jobId}/${path}`, { headers: authHeaders() });
    if (res.status === 401) { alert("Authentication required — set your API token in Settings."); return; }
    if (res.status === 503) { alert("PDF rendering unavailable (no Chrome on the server). Try HTML."); return; }
    if (!res.ok) { alert(`${label} export failed (${res.status}).`); return; }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    window.open(url, "_blank", "noopener");
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  } catch (e) {
    alert(`Could not fetch ${label}.`);
  }
}

$("settingsToggle").addEventListener("click", (e) => {
  const panel = $("settingsPanel");
  const open = panel.hasAttribute("hidden");
  if (open) panel.removeAttribute("hidden"); else panel.setAttribute("hidden", "");
  e.currentTarget.setAttribute("aria-expanded", String(open));
});

/* ---------- citation URL helper ---------- */
function citationUrl(c) {
  if (c.url) return c.url;
  const id = c.id || "";
  switch ((c.source || "").toLowerCase()) {
    case "pubmed": return `https://pubmed.ncbi.nlm.nih.gov/${id}/`;
    case "clinicaltrials": return `https://clinicaltrials.gov/study/${id}`;
    case "openfda": return `https://labels.fda.gov/`;
    default: return id.startsWith("http") ? id : "";
  }
}

const GRADE_VARS = {
  rct: "--grade-rct", meta_analysis: "--grade-meta", "meta-analysis": "--grade-meta",
  trial: "--grade-trial", phase_iii: "--grade-trial", review: "--grade-review",
  label: "--grade-label", guideline: "--grade-meta", unknown: "--grade-unknown",
};
function gradeColor(grade) {
  const key = String(grade || "unknown").toLowerCase().replace(/[\s/]+/g, "_");
  const v = GRADE_VARS[key] || "--grade-unknown";
  return getComputedStyle(document.documentElement).getPropertyValue(v).trim() || "#8a9099";
}

function el(tag, cls, html) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html != null) e.innerHTML = html;
  return e;
}
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (m) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]));
}

/* ---------- rendering ---------- */
function renderSources(report) {
  const row = $("sourcesRow");
  row.innerHTML = "";
  (report.sources_status || []).forEach((s) => {
    const pill = el("span", `source-pill ${s.available ? "up" : "down"}`);
    pill.innerHTML = `<span class="s-dot"></span>${esc(s.source)}${s.available ? "" : " · n/a"}`;
    if (s.note) pill.title = s.note;
    row.appendChild(pill);
  });
}

function renderFinding(f, i) {
  const li = el("li", "finding");
  li.style.animationDelay = `${Math.min(i * 70, 600)}ms`;

  li.appendChild(el("p", "finding-statement", esc(f.statement)));

  const meta = el("div", "finding-meta");
  const grade = f.evidence_grade || "unknown";
  const badge = el("span", "grade-badge", esc(String(grade).replace(/_/g, " ")));
  badge.style.background = gradeColor(grade);
  meta.appendChild(badge);

  if (typeof f.confidence === "number") {
    const conf = el("span", "confidence");
    const pct = Math.round(f.confidence * 100);
    conf.innerHTML = `confidence <span class="conf-bar"><span class="conf-fill" style="width:${pct}%"></span></span> ${pct}%`;
    meta.appendChild(conf);
  }
  li.appendChild(meta);

  if (f.quote) li.appendChild(el("blockquote", "finding-quote", `“${esc(f.quote)}”`));

  const cites = el("div", "cites");
  (f.citations || []).forEach((c) => {
    const url = citationUrl(c);
    const chip = el(url ? "a" : "span", "cite-chip");
    if (url) { chip.href = url; chip.target = "_blank"; chip.rel = "noopener"; }
    chip.innerHTML =
      `<span class="verified" title="verified against the live source">${c.verified ? "✓" : "○"}</span>` +
      `<span class="src">${esc(c.source)}</span>` +
      `<span>${esc(c.id)}</span>`;
    cites.appendChild(chip);
  });
  li.appendChild(cites);

  if (f.flags && f.flags.length) {
    const fr = el("div", "flag-row");
    f.flags.forEach((fl) => fr.appendChild(el("span", "flag", esc(fl))));
    li.appendChild(fr);
  }
  if (f.conflicts_with && f.conflicts_with.length) {
    li.appendChild(el("div", "conflict-note",
      `<span aria-hidden="true">⚠</span> Contested by finding(s) ${f.conflicts_with.map((n) => n + 1).join(", ")}`));
  }
  return li;
}

function renderManuscript(m) {
  const sec = $("manuscriptSection");
  const body = $("manuscriptBody");
  body.innerHTML = "";
  if (!m) { sec.hidden = true; return; }
  const order = ["title", "introduction", "case_presentation", "discussion", "literature_review", "conclusion"];
  const keys = [...order.filter((k) => k in m), ...Object.keys(m).filter((k) => !order.includes(k) && k !== "flags")];
  keys.forEach((k) => {
    body.appendChild(el("h3", null, esc(k.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()))));
    body.appendChild(el("p", null, esc(m[k])));
  });
  if (m.flags) {
    body.appendChild(el("div", "manuscript-flags",
      `<strong>Audit:</strong> ${esc(m.flags)}`));
  }
  sec.hidden = false;
}

function renderResults(jobId, report) {
  $("resultsTitle").textContent = report.empty_reason ? "No verified evidence" : "Decision support";
  renderSources(report);

  const list = $("findingsList");
  list.innerHTML = "";
  const findings = report.decision_support || [];
  $("emptyState").hidden = findings.length > 0;
  findings.forEach((f, i) => list.appendChild(renderFinding(f, i)));

  renderManuscript(report.manuscript);

  const bind = (id, path, label) => {
    const a = $(id);
    a.onclick = (e) => { e.preventDefault(); openExport(jobId, path, label); };
    a.href = "#";
  };
  bind("exportHtml", "export?format=html&manuscript=true", "HTML");
  bind("exportJson", "export?format=json", "JSON");
  bind("exportPdf", "export?format=pdf&manuscript=true", "PDF");
  bind("exportProv", "provenance", "Provenance");

  $("disclaimerFoot").textContent = report.disclaimer || "";
  show("results");
}

/* ---------- run ---------- */
let polling = false;

async function analyze() {
  const text = $("caseText").value.trim();
  const errEl = $("inputError");
  errEl.hidden = true;
  if (text.length < 15) {
    errEl.textContent = "Please enter a fuller de-identified case (at least a sentence or two).";
    errEl.hidden = false;
    return;
  }
  show("working");
  try {
    const res = await fetch(`${API}/analyze`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ case_text: text, make_manuscript: $("manuscriptToggle").checked }),
    });
    if (res.status === 401) return fail("Authentication required. Open Settings and enter a valid API token.");
    if (!res.ok) return fail(`Server returned ${res.status}.`);
    const { job_id } = await res.json();
    pollReport(job_id, 0);
  } catch (e) {
    fail("Could not reach the server. Is it running?");
  }
}

async function pollReport(jobId, n) {
  if (n > POLL_MAX) return fail("Timed out waiting for the analysis.");
  try {
    const res = await fetch(`${API}/report/${jobId}`, { headers: authHeaders() });
    if (!res.ok) return fail(`Server returned ${res.status}.`);
    const data = await res.json();
    if (data.status === "running") {
      setTimeout(() => pollReport(jobId, n + 1), POLL_MS);
      return;
    }
    if (data.status === "rejected") {
      $("rejectedMsg").textContent = data.error || "Residual identifiers detected; the case was not sent anywhere.";
      return show("rejected");
    }
    if (data.status === "error") return fail(data.error || "Processing error.");
    if (data.status === "done" && data.report) return renderResults(jobId, data.report);
    fail("Unexpected response from the server.");
  } catch (e) {
    fail("Lost connection while waiting for results.");
  }
}

function fail(msg) {
  $("errorMsg").textContent = msg;
  show("error");
}

/* ---------- calibration dashboard ---------- */
const SVG_NS = "http://www.w3.org/2000/svg";

function fmtPct(n) {
  return typeof n === "number" && Number.isFinite(n) ? `${(n * 100).toFixed(1)}%` : "—";
}
function fmtNum(n, digits = 3) {
  return typeof n === "number" && Number.isFinite(n) ? n.toFixed(digits) : "—";
}
function fmtAxis(s) {
  return String(s || "").replace(/_/g, " ");
}

function showCalibrationPanel(name) {
  for (const [k, el] of Object.entries(calibrationPanels)) el.hidden = k !== name;
}

function showCalibration() {
  appMode = "calibration";
  for (const el of Object.values(views)) el.hidden = true;
  calibrationView.hidden = false;
  metaAnalysisView.hidden = true;
  updateNav();
  scrollTop();
}

function showMetaAnalysis() {
  appMode = "meta-analysis";
  for (const el of Object.values(views)) el.hidden = true;
  calibrationView.hidden = true;
  metaAnalysisView.hidden = false;
  updateNav();
  scrollTop();
}

function showCases() {
  show(lastCaseView);
}

function renderStatCard(label, value, sub = "") {
  const card = el("div", "cal-stat");
  card.appendChild(el("div", "cal-stat-label", esc(label)));
  card.appendChild(el("div", "cal-stat-value", esc(value)));
  if (sub) card.appendChild(el("div", "cal-stat-sub", esc(sub)));
  return card;
}

function renderCalibrationStats(data) {
  const row = $("calibrationStats");
  row.innerHTML = "";
  row.appendChild(renderStatCard("AUC", fmtNum(data.auc, 3)));
  row.appendChild(renderStatCard(
    "Operating point",
    fmtNum(data.chosen_threshold, 2),
    `sens ${fmtPct(data.achieved_sensitivity)} · spec ${fmtPct(data.achieved_specificity)}`,
  ));
  const targetCard = renderStatCard(
    "Target recall",
    fmtPct(data.target_recall),
    `n+ ${data.n_positive} · n− ${data.n_negative}`,
  );
  const badge = el("span", `cal-badge ${data.target_met ? "met" : "miss"}`, data.target_met ? "target met" : "target missed");
  targetCard.appendChild(badge);
  row.appendChild(targetCard);
  row.appendChild(renderStatCard("Score axis", fmtAxis(data.score_axis)));
  row.appendChild(renderStatCard("Source", data.source || "—"));
}

function svgEl(tag, attrs = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  return node;
}

function chartScale(pad, plotW, plotH, minX, maxX, minY, maxY) {
  const spanX = maxX - minX || 1;
  const spanY = maxY - minY || 1;
  return {
    x: (v) => pad.l + ((v - minX) / spanX) * plotW,
    y: (v) => pad.t + plotH - ((v - minY) / spanY) * plotH,
  };
}

function polylinePoints(curve, xKey, yKey, scale) {
  return curve.map((p) => `${scale.x(p[xKey]).toFixed(2)},${scale.y(p[yKey]).toFixed(2)}`).join(" ");
}

function drawAxes(svg, pad, w, h, plotW, plotH, xLabel, yLabel, ticks = [0, 0.25, 0.5, 0.75, 1]) {
  const scale = chartScale(pad, plotW, plotH, 0, 1, 0, 1);
  svg.appendChild(svgEl("line", { class: "axis-line", x1: pad.l, y1: pad.t + plotH, x2: pad.l + plotW, y2: pad.t + plotH }));
  svg.appendChild(svgEl("line", { class: "axis-line", x1: pad.l, y1: pad.t, x2: pad.l, y2: pad.t + plotH }));
  ticks.forEach((t) => {
    const x = scale.x(t);
    const y = scale.y(t);
    svg.appendChild(svgEl("line", { class: "grid-line", x1: x, y1: pad.t, x2: x, y2: pad.t + plotH }));
    svg.appendChild(svgEl("line", { class: "grid-line", x1: pad.l, y1: y, x2: pad.l + plotW, y2: y }));
    const xLbl = svgEl("text", { class: "axis-label", x, y: pad.t + plotH + 18, "text-anchor": "middle" });
    xLbl.textContent = t.toFixed(2);
    svg.appendChild(xLbl);
    const yLbl = svgEl("text", { class: "axis-label", x: pad.l - 8, y: y + 3, "text-anchor": "end" });
    yLbl.textContent = t.toFixed(2);
    svg.appendChild(yLbl);
  });
  const xAxisLbl = svgEl("text", { class: "axis-label", x: pad.l + plotW / 2, y: h - 6, "text-anchor": "middle" });
  xAxisLbl.textContent = xLabel;
  svg.appendChild(xAxisLbl);
  const yAxisLbl = svgEl("text", { class: "axis-label", x: 14, y: pad.t + plotH / 2, "text-anchor": "middle", transform: `rotate(-90 14 ${pad.t + plotH / 2})` });
  yAxisLbl.textContent = yLabel;
  svg.appendChild(yAxisLbl);
  return scale;
}

function verticalMarker(svg, scale, threshold, cls, label, plotH, pad) {
  if (typeof threshold !== "number" || !Number.isFinite(threshold)) return;
  const x = scale.x(threshold);
  svg.appendChild(svgEl("line", {
    class: cls,
    x1: x, y1: pad.t, x2: x, y2: pad.t + plotH,
  }));
  if (label) {
    const lbl = svgEl("text", { class: "marker-label", x, y: pad.t - 4, "text-anchor": "middle" });
    lbl.textContent = label;
    svg.appendChild(lbl);
  }
}

function renderThresholdChart(data) {
  const wrap = $("thresholdChart");
  wrap.innerHTML = "";
  const curve = data.curve || [];
  if (!curve.length) {
    wrap.appendChild(el("p", "card-lede", "No curve points available."));
    return;
  }

  const w = 640;
  const h = 280;
  const pad = { l: 44, r: 16, t: 28, b: 36 };
  const plotW = w - pad.l - pad.r;
  const plotH = h - pad.t - pad.b;
  const aria = `Sensitivity and specificity versus threshold. Operating point at threshold ${fmtNum(data.chosen_threshold, 2)} with sensitivity ${fmtPct(data.achieved_sensitivity)} and specificity ${fmtPct(data.achieved_specificity)}. AUC ${fmtNum(data.auc, 3)}.`;

  const svg = svgEl("svg", { class: "cal-chart", viewBox: `0 0 ${w} ${h}`, role: "img", "aria-label": aria });
  const title = svgEl("title");
  title.textContent = aria;
  svg.appendChild(title);

  const scale = drawAxes(svg, pad, w, h, plotW, plotH, "threshold", "rate");
  svg.appendChild(svgEl("polyline", { class: "curve-sens", points: polylinePoints(curve, "threshold", "sensitivity", scale) }));
  svg.appendChild(svgEl("polyline", { class: "curve-spec", points: polylinePoints(curve, "threshold", "specificity", scale) }));
  verticalMarker(svg, scale, data.chosen_threshold, "marker-line", "operating point", plotH, pad);
  verticalMarker(svg, scale, data.youden_threshold, "marker-youden", "Youden", plotH, pad);

  const legY = pad.t + 10;
  const sensLeg = svgEl("text", { class: "chart-legend", x: pad.l + 4, y: legY });
  sensLeg.textContent = "— sensitivity";
  svg.appendChild(sensLeg);
  const specLeg = svgEl("text", { class: "chart-legend", x: pad.l + 110, y: legY });
  specLeg.textContent = "— specificity";
  svg.appendChild(specLeg);

  wrap.appendChild(svg);
}

function renderRocChart(data) {
  const wrap = $("rocChart");
  wrap.innerHTML = "";
  const curve = data.curve || [];
  if (!curve.length) {
    wrap.appendChild(el("p", "card-lede", "No curve points available."));
    return;
  }

  const rocPoints = curve.map((p) => ({
    fpr: 1 - (p.specificity ?? 0),
    tpr: p.sensitivity ?? 0,
  }));

  const w = 640;
  const h = 280;
  const pad = { l: 44, r: 16, t: 28, b: 36 };
  const plotW = w - pad.l - pad.r;
  const plotH = h - pad.t - pad.b;
  const aria = `ROC curve with AUC ${fmtNum(data.auc, 3)}. Sensitivity versus false positive rate.`;

  const svg = svgEl("svg", { class: "cal-chart", viewBox: `0 0 ${w} ${h}`, role: "img", "aria-label": aria });
  const title = svgEl("title");
  title.textContent = aria;
  svg.appendChild(title);

  const scale = drawAxes(svg, pad, w, h, plotW, plotH, "1 − specificity", "sensitivity");
  svg.appendChild(svgEl("line", {
    class: "chance-line",
    x1: scale.x(0), y1: scale.y(0), x2: scale.x(1), y2: scale.y(1),
  }));
  svg.appendChild(svgEl("polyline", {
    class: "curve-roc",
    points: rocPoints.map((p) => `${scale.x(p.fpr).toFixed(2)},${scale.y(p.tpr).toFixed(2)}`).join(" "),
  }));

  const aucLbl = svgEl("text", { class: "auc-label", x: pad.l + plotW - 4, y: pad.t + 18, "text-anchor": "end" });
  aucLbl.textContent = `AUC = ${fmtNum(data.auc, 3)}`;
  svg.appendChild(aucLbl);

  wrap.appendChild(svg);
}

function renderCalibration(data) {
  renderCalibrationStats(data);
  renderThresholdChart(data);
  renderRocChart(data);

  const caveats = $("calibrationCaveats");
  const notes = data.caveats || [];
  if (notes.length) {
    caveats.hidden = false;
    caveats.innerHTML = `<strong>Caveats:</strong> ${notes.map(esc).join(" · ")}`;
  } else {
    caveats.hidden = true;
    caveats.innerHTML = "";
  }
}

async function loadCalibration() {
  showCalibration();
  showCalibrationPanel("loading");
  try {
    const res = await fetch(CALIBRATION_API);
    if (!res.ok) throw new Error(`Server returned ${res.status}.`);
    const data = await res.json();
    if (!data.available) {
      showCalibrationPanel("empty");
      return;
    }
    renderCalibration(data);
    showCalibrationPanel("content");
  } catch (e) {
    $("calibrationErrorMsg").textContent = e.message || "Could not reach the server.";
    showCalibrationPanel("error");
  }
}

/* ---------- meta-analysis dashboard ---------- */
function showMetaAnalysisPanel(name) {
  for (const [k, el] of Object.entries(metaAnalysisPanels)) el.hidden = k !== name;
}

function fmtPct100(n, digits = 1) {
  return typeof n === "number" && Number.isFinite(n) ? `${n.toFixed(digits)}%` : "—";
}

function fmtPropCI(prop, ci) {
  const p = fmtPct100(prop);
  if (Array.isArray(ci) && ci.length >= 2) {
    return `${p} [${fmtPct100(ci[0])}, ${fmtPct100(ci[1])}]`;
  }
  return p;
}

function fmtHRCI(hr, ci, digits = 2) {
  const hrStr = typeof hr === "number" && Number.isFinite(hr) ? hr.toFixed(digits) : "—";
  if (Array.isArray(ci) && ci.length >= 2) {
    return `${hrStr} [${fmtNum(ci[0], digits)}, ${fmtNum(ci[1], digits)}]`;
  }
  return hrStr;
}

function fmtI2(n) {
  return typeof n === "number" && Number.isFinite(n) ? `${n.toFixed(1)}%` : "—";
}

function isNonEmptyObject(obj) {
  return obj && typeof obj === "object" && !Array.isArray(obj) && Object.keys(obj).length > 0;
}

function isNonEmptyArray(arr) {
  return Array.isArray(arr) && arr.length > 0;
}

function renderMetaTable(headers, rows) {
  const wrap = el("div", "ma-table-wrap");
  const table = el("table", "ma-table");
  const thead = el("thead");
  const headRow = el("tr");
  headers.forEach((h) => headRow.appendChild(el("th", null, esc(h))));
  thead.appendChild(headRow);
  table.appendChild(thead);
  const tbody = el("tbody");
  rows.forEach((row) => {
    const tr = el("tr");
    row.forEach((cell) => tr.appendChild(el("td", null, esc(cell))));
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  wrap.appendChild(table);
  return wrap;
}

function renderMetaSectionCard(eyebrow, title, bodyEl) {
  const card = el("section", "card ma-card");
  card.appendChild(el("div", "card-eyebrow", esc(eyebrow)));
  if (title) card.appendChild(el("h3", "ma-card-title", esc(title)));
  card.appendChild(bodyEl);
  return card;
}

function resultCI(r) {
  if (Array.isArray(r.ci) && r.ci.length >= 2) return r.ci;
  if (r.ci_lb != null && r.ci_ub != null) return [r.ci_lb, r.ci_ub];
  return null;
}

function renderPooledOutcome(outcome, kind) {
  const results = outcome.results || [];
  if (!results.length) return null;

  const estimateHeader = kind === "hr" ? "HR [95% CI]" : "Estimate [95% CI]";
  const rows = results.map((r) => {
    const ci = resultCI(r);
    const estimate = kind === "hr"
      ? fmtHRCI(r.hr ?? r.prop, ci)
      : fmtPropCI(r.prop, ci);
    return [r.group || "—", estimate, r.k ?? "—", fmtI2(r.I2)];
  });

  return renderMetaSectionCard(
    "Pooled estimate",
    `${outcome.label || "Outcome"} (k=${outcome.k ?? "—"})`,
    renderMetaTable(["Subgroup", estimateHeader, "k", "I²"], rows),
  );
}

function renderProportionsSection(proportions) {
  const container = $("metaProportions");
  container.innerHTML = "";
  if (!isNonEmptyArray(proportions)) {
    container.hidden = true;
    return;
  }

  proportions.forEach((outcome) => {
    const card = renderPooledOutcome(outcome, "prop");
    if (card) container.appendChild(card);
  });
  container.hidden = container.childElementCount === 0;
}

function renderHazardRatiosSection(hazardRatios) {
  const container = $("metaHazardRatios");
  container.innerHTML = "";
  if (!isNonEmptyArray(hazardRatios)) {
    container.hidden = true;
    return;
  }

  hazardRatios.forEach((outcome) => {
    const card = renderPooledOutcome(outcome, "hr");
    if (card) container.appendChild(card);
  });
  container.hidden = container.childElementCount === 0;
}

function renderBucherTable(bucher, title) {
  if (!isNonEmptyObject(bucher)) return null;
  const rows = Object.entries(bucher).map(([comparison, v]) => {
    const ci = v && v.CI;
    return [
      comparison,
      fmtHRCI(v && v.HR, ci),
      (v && v.interpretation) || "—",
    ];
  });
  return renderMetaTable(["Comparison", "HR [95% CI]", "Interpretation"], rows);
}

function renderCuratedTier1Table(curated) {
  if (!isNonEmptyObject(curated)) return null;
  const rows = Object.entries(curated).map(([study, vals]) => {
    const hr = Array.isArray(vals) ? vals[0] : null;
    const ci = Array.isArray(vals) && vals.length >= 3 ? [vals[1], vals[2]] : null;
    return [study, fmtHRCI(hr, ci)];
  });
  return renderMetaTable(["Study", "PFS HR vs SOC [95% CI]"], rows);
}

function renderNoteList(title, items) {
  if (!isNonEmptyArray(items)) return null;
  const block = el("div", "ma-notes");
  block.appendChild(el("h4", "ma-notes-title", esc(title)));
  const ul = el("ul", "ma-notes-list");
  items.forEach((note) => ul.appendChild(el("li", null, esc(note))));
  block.appendChild(ul);
  return block;
}

function renderKeyValueBlock(title, obj) {
  if (!isNonEmptyObject(obj)) return null;
  const block = el("div", "ma-kv");
  block.appendChild(el("h4", "ma-kv-title", esc(title)));
  const dl = el("dl", "ma-kv-list");
  Object.entries(obj).forEach(([k, v]) => {
    dl.appendChild(el("dt", null, esc(k)));
    dl.appendChild(el("dd", null, esc(typeof v === "object" ? JSON.stringify(v) : v)));
  });
  block.appendChild(dl);
  return block;
}

function renderIndirectSection(indirect, nma) {
  const container = $("metaIndirect");
  container.innerHTML = "";

  const headline = nma && nma.headline;
  const bucherPfs = indirect && indirect.bucher_indirect_PFS;
  const bucherOs = indirect && indirect.bucher_indirect_OS;
  const curated = indirect && indirect.curated_tier1_pfs_hr_vs_soc;
  const curationNotes = indirect && indirect.curation_notes;
  const transitivity = indirect && indirect.transitivity_threats;

  const hasContent = headline
    || isNonEmptyObject(bucherPfs)
    || isNonEmptyObject(bucherOs)
    || isNonEmptyObject(curated)
    || isNonEmptyArray(curationNotes)
    || isNonEmptyArray(transitivity);

  if (!hasContent) {
    container.hidden = true;
    return;
  }

  const card = el("section", "card ma-card");
  card.appendChild(el("div", "card-eyebrow", esc("Anchored indirect comparison")));

  if (headline) {
    card.appendChild(el("p", "ma-headline", esc(headline)));
  }

  const bucherPfsTable = renderBucherTable(bucherPfs, "Bucher indirect — PFS");
  if (bucherPfsTable) {
    card.appendChild(el("h4", "ma-subtitle", esc("Bucher indirect — PFS")));
    card.appendChild(bucherPfsTable);
  }

  const bucherOsTable = renderBucherTable(bucherOs, "Bucher indirect — OS");
  if (bucherOsTable) {
    card.appendChild(el("h4", "ma-subtitle", esc("Bucher indirect — OS")));
    card.appendChild(bucherOsTable);
  }

  const curatedTable = renderCuratedTier1Table(curated);
  if (curatedTable) {
    card.appendChild(el("h4", "ma-subtitle", esc("Curated tier-1 PFS HR vs SOC")));
    card.appendChild(curatedTable);
  }

  const notesBlock = renderNoteList("Curation notes", curationNotes);
  if (notesBlock) card.appendChild(notesBlock);

  const threatsBlock = renderNoteList("Transitivity threats", transitivity);
  if (threatsBlock) card.appendChild(threatsBlock);

  container.appendChild(card);
  container.hidden = false;
}

function renderLeagueTable(league) {
  if (!isNonEmptyObject(league)) return null;

  const rowArms = Object.keys(league);
  const colKeys = new Set();
  rowArms.forEach((arm) => {
    const cols = league[arm];
    if (cols && typeof cols === "object") {
      Object.keys(cols).forEach((k) => colKeys.add(k));
    }
  });
  const columns = [...colKeys].sort();

  if (!rowArms.length || !columns.length) return null;

  const headers = ["Arm", ...columns.map((c) => c.replace(/^vs_/, "vs "))];
  const rows = rowArms.map((arm) => {
    const cells = columns.map((col) => {
      const vals = league[arm] && league[arm][col];
      if (Array.isArray(vals) && vals.length >= 3) {
        return fmtHRCI(vals[0], [vals[1], vals[2]]);
      }
      return "—";
    });
    return [arm, ...cells];
  });

  return renderMetaTable(headers, rows);
}

function renderNmaSection(nma) {
  const container = $("metaNma");
  container.innerHTML = "";
  if (!nma || typeof nma !== "object") {
    container.hidden = true;
    return;
  }

  const league = nma.league_table_HR_row_vs_col;
  const pScore = nma.P_score_ranking;
  const grade = nma.GRADE_CINeMA_certainty;
  const analysis = nma.analysis;
  const maic = nma.MAIC;

  const leagueTable = renderLeagueTable(league);
  const pScoreBlock = renderKeyValueBlock("P-score ranking", pScore);
  const gradeBlock = renderKeyValueBlock("GRADE / CINeMA certainty", grade);
  const hasMaic = maic != null && (typeof maic !== "object" || Object.keys(maic).length > 0);

  if (!leagueTable && !pScoreBlock && !gradeBlock && !analysis && !hasMaic) {
    container.hidden = true;
    return;
  }

  const card = el("section", "card ma-card");
  card.appendChild(el("div", "card-eyebrow", esc("Network meta-analysis")));

  if (analysis) {
    card.appendChild(el("p", "ma-meta-line", esc(analysis)));
  }

  if (leagueTable) {
    card.appendChild(el("h4", "ma-subtitle", esc("League table (HR row vs column)")));
    card.appendChild(leagueTable);
  }

  if (pScoreBlock) card.appendChild(pScoreBlock);
  if (gradeBlock) card.appendChild(gradeBlock);

  if (hasMaic) {
    if (typeof maic === "object") {
      card.appendChild(renderKeyValueBlock("MAIC", maic));
    } else {
      card.appendChild(el("h4", "ma-subtitle", esc("MAIC")));
      card.appendChild(el("p", "ma-meta-line", esc(String(maic))));
    }
  }

  container.appendChild(card);
  container.hidden = false;
}

function prettifyFigureName(name) {
  return String(name || "").replace(/\.png$/i, "").replace(/_/g, " ");
}

function renderFiguresSection(figures) {
  const container = $("metaFigures");
  container.innerHTML = "";
  if (!isNonEmptyArray(figures)) {
    container.hidden = true;
    return;
  }

  const card = el("section", "card ma-card");
  card.appendChild(el("div", "card-eyebrow", esc("Forest & funnel plots")));
  const grid = el("div", "ma-figure-grid");

  figures.forEach((name) => {
    const fig = el("figure", "ma-figure");
    const img = document.createElement("img");
    img.className = "ma-figure-img";
    img.loading = "lazy";
    img.src = `/ui/meta-analysis/figure/${encodeURIComponent(name)}`;
    img.alt = prettifyFigureName(name);
    fig.appendChild(img);
    fig.appendChild(el("figcaption", "ma-figure-caption", esc(prettifyFigureName(name))));
    grid.appendChild(fig);
  });

  card.appendChild(grid);
  container.appendChild(card);
  container.hidden = false;
}

function renderMetaAnalysis(data) {
  const sourceEl = $("metaAnalysisSource");
  if (data.source) {
    sourceEl.hidden = false;
    sourceEl.innerHTML = `<span class="ma-source-label">Source</span> ${esc(data.source)}`;
  } else {
    sourceEl.hidden = true;
    sourceEl.innerHTML = "";
  }

  renderProportionsSection(data.proportions);
  renderHazardRatiosSection(data.hazard_ratios);
  renderIndirectSection(data.indirect, data.nma);
  renderNmaSection(data.nma);
  renderFiguresSection(data.figures);
}

async function loadMetaAnalysis() {
  showMetaAnalysis();
  showMetaAnalysisPanel("loading");
  try {
    const res = await fetch(META_ANALYSIS_API);
    if (!res.ok) throw new Error(`Server returned ${res.status}.`);
    const data = await res.json();
    if (!data.available) {
      const msg = data.reason || "No completed meta-analysis yet";
      $("metaAnalysisEmptyMsg").textContent = msg;
      showMetaAnalysisPanel("empty");
      return;
    }
    renderMetaAnalysis(data);
    showMetaAnalysisPanel("content");
  } catch (e) {
    $("metaAnalysisErrorMsg").textContent = e.message || "Could not reach the server.";
    showMetaAnalysisPanel("error");
  }
}

/* ---------- wiring ---------- */
$("analyzeBtn").addEventListener("click", analyze);
$("caseText").addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") analyze();
});
document.querySelectorAll(".restart-btn").forEach((b) =>
  b.addEventListener("click", () => show("input")));
$("casesNav").addEventListener("click", showCases);
$("calibrationNav").addEventListener("click", loadCalibration);
$("calibrationRetry").addEventListener("click", loadCalibration);
$("metaAnalysisNav").addEventListener("click", loadMetaAnalysis);
$("metaAnalysisRetry").addEventListener("click", loadMetaAnalysis);

show("input");
