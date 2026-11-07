const state = { runs: [], selected: "", providerGate: null, fullTextAccessConfig: null };
const $ = (selector) => document.querySelector(selector);

async function request(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

function providerConfig() {
  const provider = $("#provider-id").value;
  const config = { provider, live_enabled: false };
  if (provider === "interactive_subscription") {
    config.service = $("#provider-service").value;
  } else if (provider !== "ollama_local") {
    const name = $("#provider-env-name").value.trim();
    if (name) config.credential_ref = { kind: "env", name };
  }
  return config;
}

function refreshProviderFields() {
  const provider = $("#provider-id").value;
  const subscription = provider === "interactive_subscription";
  const local = provider === "ollama_local";
  $("#provider-env-label").hidden = subscription || local;
  $("#provider-service-label").hidden = !subscription;
  clearProviderGate();
}

function clearProviderGate() {
  state.providerGate = null;
  const gateResult = $("#provider-gate-result");
  const planResult = $("#provider-plan-result");
  const confirmation = $("#provider-gate-confirm");
  if (gateResult) gateResult.textContent = "No operator gate has been issued.";
  if (planResult) planResult.textContent = "No gate-bound plan has been built.";
  if (confirmation) confirmation.checked = false;
}

function providerGatePayload() {
  if (!state.selected) throw new Error("Select a run before issuing a provider gate.");
  if (!$("#provider-gate-confirm").checked) {
    throw new Error("Check the explicit operator authorization before issuing a gate.");
  }
  const config = providerConfig();
  return {
    config,
    run_id: state.selected,
    action: "health_check",
    operator_ack: {
      confirmed: true,
      statement: `I authorize one health_check for provider ${config.provider} in this offline control surface.`,
    },
  };
}

async function issueProviderGate() {
  try {
    const result = await request("/api/providers/gate", {
      method: "POST",
      body: JSON.stringify(providerGatePayload()),
    });
    state.providerGate = result;
    $("#provider-gate-result").textContent = JSON.stringify(result, null, 2);
    $("#provider-plan-result").textContent = "No gate-bound plan has been built.";
    $("#provider-gate-confirm").checked = false;
  } catch (error) {
    state.providerGate = null;
    $("#provider-gate-result").textContent = error.message;
    $("#provider-plan-result").textContent = "No gate-bound plan has been built.";
  }
}

async function buildProviderCallPlan() {
  try {
    if (!state.providerGate) throw new Error("Issue an operator gate before building its plan.");
    const config = providerConfig();
    const result = await request("/api/providers/call-plan", {
      method: "POST",
      body: JSON.stringify({
        config,
        gate: state.providerGate,
        run_id: state.selected,
        action: "health_check",
      }),
    });
    $("#provider-plan-result").textContent = JSON.stringify(result, null, 2);
  } catch (error) {
    $("#provider-plan-result").textContent = error.message;
  }
}

async function runProviderAction(action) {
  try {
    const result = await request(`/api/providers/${action}`, {
      method: "POST",
      body: JSON.stringify(providerConfig()),
    });
    $("#provider-result").textContent = JSON.stringify(result, null, 2);
  } catch (error) {
    $("#provider-result").textContent = error.message;
  }
}

$("#provider-id").addEventListener("change", refreshProviderFields);
$("#provider-env-name").addEventListener("input", clearProviderGate);
$("#provider-service").addEventListener("change", clearProviderGate);
$("#provider-validate").addEventListener("click", () => runProviderAction("validate"));
$("#provider-health").addEventListener("click", () => runProviderAction("health-plan"));
$("#provider-manifest").addEventListener("click", () => runProviderAction("manifest"));
$("#provider-gate").addEventListener("click", issueProviderGate);
$("#provider-call-plan").addEventListener("click", buildProviderCallPlan);

request("/api/providers").then((payload) => {
  $("#provider-result").textContent = JSON.stringify(payload, null, 2);
  refreshProviderFields();
}).catch((error) => { $("#provider-result").textContent = error.message; });

function fullTextCitation() {
  const citation = {};
  const doi = $("#full-text-doi").value.trim();
  const pmid = $("#full-text-pmid").value.trim();
  const title = $("#full-text-title").value.trim();
  if (doi) citation.doi = doi;
  if (pmid) citation.pmid = pmid;
  if (title) citation.title = title;
  return citation;
}

async function showFullTextConnectors() {
  try {
    const result = await request("/api/full-text/connectors");
    $("#full-text-result").textContent = JSON.stringify(result, null, 2);
  } catch (error) {
    $("#full-text-result").textContent = error.message;
  }
}

async function buildFullTextPlan() {
  try {
    const result = await request("/api/full-text/plan", {
      method: "POST",
      body: JSON.stringify({
        citation: fullTextCitation(),
        ...(state.fullTextAccessConfig ? { access_config: state.fullTextAccessConfig } : {}),
      }),
    });
    $("#full-text-result").textContent = JSON.stringify(result, null, 2);
  } catch (error) {
    $("#full-text-result").textContent = error.message;
  }
}

function fullTextAccessConfigPayload() {
  const connectorId = $("#full-text-config-connector").value;
  const mode = $("#full-text-config-mode").value;
  const entry = { connector_id: connectorId, enabled: true, mode };
  const institution = $("#full-text-config-institution").value.trim();
  const envName = $("#full-text-config-env").value.trim();
  const proxyUrl = $("#full-text-config-proxy").value.trim();
  if (institution) entry.institution_label = institution;
  if (envName) entry.credential_ref = { kind: "env", name: envName };
  if (proxyUrl) entry.proxy_base_url = proxyUrl;
  return { connectors: [entry] };
}

async function validateFullTextAccessConfig() {
  try {
    const result = await request("/api/full-text/access-config/validate", {
      method: "POST",
      body: JSON.stringify(fullTextAccessConfigPayload()),
    });
    state.fullTextAccessConfig = result;
    $("#full-text-config-result").textContent = JSON.stringify(result, null, 2);
  } catch (error) {
    state.fullTextAccessConfig = null;
    $("#full-text-config-result").textContent = error.message;
  }
}

async function buildFullTextProbePlan() {
  try {
    if (!state.fullTextAccessConfig) {
      throw new Error("Validate a reference-only access profile first.");
    }
    const result = await request("/api/full-text/probe-plan", {
      method: "POST",
      body: JSON.stringify({
        citation: fullTextCitation(),
        access_config: state.fullTextAccessConfig,
      }),
    });
    $("#full-text-result").textContent = JSON.stringify(result, null, 2);
  } catch (error) {
    $("#full-text-result").textContent = error.message;
  }
}

async function createManualAccessRecord() {
  try {
    const candidate = {};
    const doi = $("#manual-record-doi").value.trim();
    const pmid = $("#manual-record-pmid").value.trim();
    const title = $("#manual-record-title").value.trim();
    if (doi) candidate.doi = doi;
    if (pmid) candidate.pmid = pmid;
    if (title) candidate.title = title;
    const sourceUrl = $("#manual-record-url").value.trim();
    const result = await request("/api/full-text/manual-record", {
      method: "POST",
      body: JSON.stringify({
        citation: fullTextCitation(),
        route_id: $("#manual-record-route").value,
        access_state: $("#manual-record-state").value,
        candidate,
        ...(sourceUrl ? { source_url: sourceUrl } : {}),
      }),
    });
    $("#manual-record-result").textContent = JSON.stringify(result, null, 2);
  } catch (error) {
    $("#manual-record-result").textContent = error.message;
  }
}

$("#full-text-connectors").addEventListener("click", showFullTextConnectors);
$("#full-text-plan").addEventListener("click", buildFullTextPlan);
$("#full-text-config-validate").addEventListener("click", validateFullTextAccessConfig);
$("#full-text-probe-plan").addEventListener("click", buildFullTextProbePlan);
$("#manual-record-submit").addEventListener("click", createManualAccessRecord);

function renderRuns() {
  const select = $("#run-select");
  select.innerHTML = '<option value="">Select a run</option>';
  state.runs.forEach((run) => {
    const option = document.createElement("option");
    option.value = run.id;
    option.textContent = `${run.name} · ${run.id}`;
    option.selected = run.id === state.selected;
    select.appendChild(option);
  });
}

async function refreshRuns() {
  const payload = await request("/api/runs");
  state.runs = payload.runs;
  renderRuns();
  await refreshSelected();
}

function renderQueue(screening) {
  const el = $("#adjudication-queue");
  if (!el) return;
  el.innerHTML = "";
  if (!screening || !screening.remaining || screening.remaining.length === 0) return;
  const heading = document.createElement("p");
  heading.className = "muted";
  heading.textContent = `Blocking queue (${screening.remaining.length} pending adjudication):`;
  el.appendChild(heading);
  screening.remaining.forEach((id) => {
    const row = document.createElement("div");
    row.className = "queue-row";
    const label = document.createElement("span");
    label.textContent = id;
    row.appendChild(label);
    ["include", "exclude"].forEach((decision) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = decision;
      button.addEventListener("click", async () => {
        try {
          await request(`/api/runs/${state.selected}/adjudicate`, {
            method: "POST",
            body: JSON.stringify({ id, decision }),
          });
          await refreshSelected();
        } catch (error) { alert(error.message); }
      });
      row.appendChild(button);
    });
    el.appendChild(row);
  });
}

async function refreshSelected() {
  if (!state.selected) {
    $("#run-state").textContent = "No run selected";
    $("#import-state").textContent = "No run selected";
    $("#dedup-lock-state").textContent = "No run selected";
    $("#screen-state").textContent = "No run selected";
    $("#extraction-state").textContent = "No run selected";
    $("#rob-state").textContent = "No run selected";
    $("#synthesis-state").textContent = "No run selected";
    $("#package-replay-state").textContent = "No run selected";
    $("#artifacts").textContent = "Create or select a run.";
    return;
  }
  const run = await request(`/api/runs/${state.selected}`);
  const artifacts = await request(`/api/runs/${state.selected}/artifacts`);
  const screening = await request(`/api/runs/${state.selected}/screening`);
  const replay = await request(`/api/runs/${state.selected}/replay`);
  $("#run-state").textContent = `${run.name} · ${run.id}`;
  $("#import-state").textContent = run.stages && run.stages.import === "complete" ? "Imported" : "Not imported yet";
  const stages = run.stages || {};
  const dedupLabel = stages.dedup === "complete" ? "Deduplicated" : "Not deduplicated yet";
  const lockLabel = stages.lock === "complete" ? "Locked" : "Not locked yet";
  $("#dedup-lock-state").textContent = `${dedupLabel} · ${lockLabel}`;
  const screenLabel = stages.screen === "blocked" ? "Blocked on adjudication"
    : stages.screen === "complete" ? "Screened"
    : "Not screened yet";
  $("#screen-state").textContent = screenLabel;
  $("#extraction-state").textContent = stages.extraction === "complete"
    ? "Extraction recorded"
    : "Not extracted yet";
  $("#rob-state").textContent = stages.rob === "complete"
    ? "Risk of bias recorded"
    : "Not assessed yet";
  $("#synthesis-state").textContent = stages.synthesis === "complete"
    ? "Synthesis calculated"
    : "Not synthesized yet";
  const toggle = $("#human-adjudication-toggle");
  if (toggle) toggle.checked = run.human_adjudication === "on";
  renderQueue(screening);
  const packageLabel = stages.package === "complete" ? "Packaged" : "Not packaged yet";
  const replayLabel = stages.replay === "complete" ? "Replay byte-stable"
    : stages.replay === "mismatched" ? "Replay mismatched"
    : "Not replayed yet";
  $("#package-replay-state").textContent = `${packageLabel} · ${replayLabel}`;
  const rawSourceArtifacts = artifacts.artifacts.filter((item) => item.name.startsWith("source/"));
  const frozenArtifacts = artifacts.artifacts.filter((item) => !item.name.startsWith("source/") && !item.name.startsWith("replay/"));
  const replayArtifacts = artifacts.artifacts.filter((item) => item.name.startsWith("replay/"));
  $("#artifacts").textContent = JSON.stringify(
    { run, raw_source_artifacts: rawSourceArtifacts, frozen_artifacts: frozenArtifacts, replay_artifacts: replayArtifacts, replay_comparison: replay },
    null,
    2
  );
}

$("#new-run-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const run = await request("/api/runs", { method: "POST", body: JSON.stringify({ name: $("#run-name").value }) });
    state.selected = run.id;
    clearProviderGate();
    $("#run-name").value = "";
    await refreshRuns();
  } catch (error) { alert(error.message); }
});

$("#run-select").addEventListener("change", async (event) => {
  state.selected = event.target.value;
  clearProviderGate();
  await refreshSelected();
});

$("#refresh").addEventListener("click", refreshSelected);

$("#human-adjudication-toggle").addEventListener("change", async (event) => {
  if (!state.selected) {
    event.target.checked = !event.target.checked;
    return alert("Select a run first.");
  }
  const enabled = event.target.checked;
  try {
    await request(`/api/runs/${state.selected}/human_adjudication`, {
      method: "POST",
      body: JSON.stringify({ enabled }),
    });
    await refreshSelected();
  } catch (error) {
    event.target.checked = !enabled;
    alert(error.message);
  }
});

// Read the upload as raw bytes (never as decoded text) so non-UTF-8 RIS/NBIB
// exports, invalid byte sequences, and BOMs survive unmodified. `file.text()`
// would decode through UTF-8 (replacing invalid sequences) before the bytes
// ever reach the server, which breaks the byte-for-byte source-preservation
// contract this stage is required to uphold. base64 is a transport encoding
// only -- server.py decodes it back to the exact original bytes before
// writing the source artifact to disk.
async function readFileAsBase64(file) {
  const buffer = await file.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const chunkSize = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
  }
  return btoa(binary);
}

$("#import-submit").addEventListener("click", async () => {
  if (!state.selected) return alert("Select a run first.");
  const fileInput = $("#import-file");
  const file = fileInput.files[0];
  if (!file) return alert("Choose a .ris or .nbib file first.");
  try {
    const contentBase64 = await readFileAsBase64(file);
    const result = await request(`/api/runs/${state.selected}/import`, {
      method: "POST",
      body: JSON.stringify({ filename: file.name, content_base64: contentBase64 }),
    });
    $("#import-result").textContent = JSON.stringify(result.import, null, 2);
    fileInput.value = "";
    await refreshSelected();
  } catch (error) { alert(error.message); }
});

function renderDiagnostic(action, result) {
  const el = $("#diagnostic-result");
  if (!el) return;
  if (action === "doctor" && result && result.doctor) {
    const d = result.doctor;
    const lines = [`doctor: ${d.passed ? "PASSED" : "FAILED"} (exit_code ${d.exit_code})`];
    if (d.stderr) lines.push(`stderr:\n${d.stderr}`);
    lines.push(`report:\n${JSON.stringify(d.report, null, 2)}`);
    el.textContent = lines.join("\n\n");
  } else {
    el.textContent = JSON.stringify(result, null, 2);
  }
}

// "dedup"/"lock" render into their own pane (not "#diagnostic-result",
// which is reserved for "doctor") so a dedup/lock artifact's sha256 content
// hash -- the "verify artifact hashes" half of stage 3's contract -- stays
// visible to the operator instead of being overwritten by the next
// diagnostic run.
function renderDedupLock(action, result) {
  const el = $("#diagnostic-result-dedup-lock");
  if (!el) return;
  const detail = result && result[action];
  if (detail && detail.artifact && detail.sha256) {
    el.textContent = `${action}: wrote ${detail.artifact}\nsha256: ${detail.sha256}`;
  } else {
    el.textContent = JSON.stringify(result, null, 2);
  }
}

// "screen" renders into its own pane (not "#diagnostic-result" or
// "#diagnostic-result-dedup-lock") so the abstained/blocked summary a
// screen run reports stays visible instead of being overwritten by an
// unrelated doctor/dedup/lock run.
function renderScreen(result) {
  const el = $("#screen-result");
  if (!el) return;
  const detail = result && result.screen;
  if (detail && detail.artifact && detail.sha256) {
    const lines = [
      `screen: wrote ${detail.artifact}`,
      `sha256: ${detail.sha256}`,
      `blocked: ${detail.blocked}`,
    ];
    if (detail.abstained_ids && detail.abstained_ids.length) {
      lines.push(`abstained: ${detail.abstained_ids.join(", ")}`);
    }
    el.textContent = lines.join("\n");
  } else {
    el.textContent = JSON.stringify(result, null, 2);
  }
}

function renderExtraction(result) {
  const el = $("#extraction-result");
  if (!el) return;
  const detail = result && result.extraction;
  if (detail && detail.artifact) {
    el.textContent = [
      `extraction: wrote ${detail.artifact}`,
      `source: ${detail.source}`,
      `sha256: ${detail.sha256}`,
      `record_hash: ${detail.record_hash}`,
      `field counts: ${JSON.stringify(detail.field_counts)}`,
    ].join("\n");
  } else {
    el.textContent = JSON.stringify(result, null, 2);
  }
}

function renderRob(result) {
  const el = $("#rob-result");
  if (!el) return;
  const detail = result && result.rob;
  if (detail && detail.artifact) {
    el.textContent = [
      `risk of bias: wrote ${detail.artifact}`,
      `sha256: ${detail.sha256}`,
      `rob_hash: ${detail.rob_hash}`,
      `record_id: ${detail.record_id}`,
      `overall judgment: ${detail.overall_judgment}`,
      `domains: ${detail.domain_count}`,
    ].join("\n");
  } else {
    el.textContent = JSON.stringify(result, null, 2);
  }
}

function renderSynthesis(result) {
  const el = $("#synthesis-result");
  if (!el) return;
  const detail = result && result.synthesis;
  if (detail && detail.artifact) {
    el.textContent = [
      `synthesis: wrote ${detail.artifact}`,
      `sha256: ${detail.sha256}`,
      `calculation_hash: ${detail.calculation_hash}`,
      `measure: ${detail.measure}`,
      `status: ${detail.status}`,
      `uncertainty flags: ${JSON.stringify(detail.uncertainty_flags)}`,
    ].join("\n");
  } else {
    el.textContent = JSON.stringify(result, null, 2);
  }
}

// "package"/"replay" render into their own pane so a package's manifest and
// a replay's byte-for-byte comparison stay visible instead of being
// overwritten by an unrelated doctor/dedup/lock/screen run.
function renderPackageReplay(action, result) {
  const el = $("#package-replay-result");
  if (!el) return;
  const detail = result && result[action];
  if (action === "package" && detail && detail.artifact) {
    el.textContent = `package: wrote ${detail.artifact}\nsha256: ${detail.sha256}\nmanifest:\n${JSON.stringify(detail.manifest, null, 2)}`;
  } else if (action === "replay" && detail && "byte_stable" in detail) {
    const lines = [`replay: byte_stable=${detail.byte_stable}`];
    if (detail.mismatched && detail.mismatched.length) {
      lines.push(`mismatched: ${detail.mismatched.join(", ")}`);
    }
    lines.push(`comparison:\n${JSON.stringify(detail.comparison, null, 2)}`);
    el.textContent = lines.join("\n");
  } else {
    el.textContent = JSON.stringify(result, null, 2);
  }
}

document.querySelectorAll("[data-action]").forEach((button) => button.addEventListener("click", async () => {
  if (!state.selected) return alert("Select a run first.");
  const action = button.dataset.action;
  try {
    let body = {};
    if (action === "extract") {
      const file = $("#extract-file").files[0];
      if (!file) return alert("Choose a UTF-8 full-text source first.");
      let fields;
      try {
        fields = JSON.parse($("#extract-fields").value);
      } catch (error) {
        return alert(`Fields JSON is invalid: ${error.message}`);
      }
      body = {
        filename: file.name,
        record_id: $("#extract-record-id").value.trim(),
        content_base64: await readFileAsBase64(file),
        fields,
      };
    } else if (action === "rob") {
      let domains;
      try {
        domains = JSON.parse($("#rob-domains").value);
      } catch (error) {
        return alert(`Risk-of-bias domains JSON is invalid: ${error.message}`);
      }
      body = {
        record_id: $("#rob-record-id").value.trim(),
        tool: $("#rob-tool").value.trim(),
        overall_judgment: $("#rob-overall").value,
        domains,
      };
    } else if (action === "synthesize") {
      let counts;
      try {
        counts = JSON.parse($("#synthesis-counts").value);
      } catch (error) {
        return alert(`Synthesis counts JSON is invalid: ${error.message}`);
      }
      body = {
        record_id: $("#synthesis-record-id").value.trim(),
        measure: $("#synthesis-measure").value,
        counts,
      };
    }
    const result = await request(`/api/runs/${state.selected}/${action}`, { method: "POST", body: JSON.stringify(body) });
    if (action === "dedup" || action === "lock") {
      renderDedupLock(action, result);
    } else if (action === "screen") {
      renderScreen(result);
    } else if (action === "extract") {
      renderExtraction(result);
    } else if (action === "rob") {
      renderRob(result);
    } else if (action === "synthesize") {
      renderSynthesis(result);
    } else if (action === "package" || action === "replay") {
      renderPackageReplay(action, result);
    } else {
      renderDiagnostic(action, result);
    }
    await refreshSelected();
  }
  catch (error) { alert(error.message); }
}));

$("#artifact-inspect-submit").addEventListener("click", async () => {
  if (!state.selected) return alert("Select a run first.");
  const name = $("#artifact-inspect-name").value.trim();
  if (!name) return alert("Enter an artifact name first.");
  const el = $("#artifact-inspect-result");
  try {
    const result = await request(`/api/runs/${state.selected}/artifacts/${name}`);
    const binary = atob(result.content_base64);
    let text;
    try {
      text = new TextDecoder("utf-8", { fatal: true }).decode(Uint8Array.from(binary, (c) => c.charCodeAt(0)));
    } catch (error) {
      text = "(binary content, showing base64)\n" + result.content_base64;
    }
    el.textContent = `${result.name} (${result.bytes} bytes, sha256 ${result.sha256}):\n\n${text}`;
  } catch (error) {
    el.textContent = error.message;
  }
});

request("/api/health").then((health) => {
  $("#health").textContent = health.offline_only && !health.credentials_loaded ? "Offline only" : "Check configuration";
}).catch((error) => { $("#health").textContent = error.message; });
refreshRuns().catch((error) => { $("#artifacts").textContent = error.message; });
