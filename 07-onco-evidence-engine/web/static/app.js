/* Onco-Evidence web client — vanilla JS over the engine API (mounted at /api).
   Flow: POST /api/analyze -> poll GET /api/report/{id} -> render. */

const API = "/api";
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

function show(name) {
  for (const [k, el] of Object.entries(views)) el.hidden = k !== name;
  // input is always present above; hide it only when another primary view shows
  views.input.hidden = !(name === "input");
  if (name === "results" || name === "working" || name === "rejected" || name === "error") {
    views.input.hidden = true;
  }
  window.scrollTo({ top: 0, behavior: "smooth" });
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

/* ---------- wiring ---------- */
$("analyzeBtn").addEventListener("click", analyze);
$("caseText").addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") analyze();
});
document.querySelectorAll(".restart-btn").forEach((b) =>
  b.addEventListener("click", () => show("input")));

show("input");
