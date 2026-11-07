# Orchestration Engine — M2 (Provider Gateway + Dual-LLM Reviewers + Security) Plan

> **For agentic workers:** Executed via the Claude↔Codex DualReview harness. Each WP gated on Codex `VERDICT: APPROVE` + full suite green. TDD within each WP.

**Goal:** Make Reviewer 1 / Reviewer 2 independently-powered by different models via a provider-agnostic gateway, run dual independent-LLM screening with κ-style agreement (honestly labeled) + reconciliation, and harden the secret/data-egress surface.

**Architecture:** A `gateway` module wraps the existing `core/llm/client.py` `parse` path (preserving the native Anthropic structured-output route cite-or-omit depends on) and adds per-`(stage|reviewer)` provider/model selection over Anthropic (native) + Ollama (local), with a pluggable seam for additional API providers (OpenAI/Gemini) — **no LiteLLM dependency in M2** (supply-chain/telemetry surface the gauntlet flagged). The flagship subscription R1=Claude/R2=Codex pairing remains the interactive agent-harness path; the gateway powers the **headless** dual-reviewer (R1=Anthropic/R2=local or, via the seam, R2=cloud-API).

**Tech stack:** Python 3.11, existing `core/llm/client.py` + `core/sr/reconcile.py` (WP6 κ/consensus), pydantic, stdlib (ipaddress for SSRF), pytest.

**Spec:** `docs/superpowers/specs/2026-06-16-...-design.md` §4, §7a (independence), §8 (llm_llm_agreement), §10 (security).

---

## WP-M2.1 — Provider gateway (resolver + Anthropic/Ollama + SecretStr keys)

**Files:** Create `core/llm/gateway.py`, `tests/llm/test_gateway.py`.

**Contract:**
```python
class SecretStr:                       # non-serializable key wrapper (repr/str raise-safe)
    def __init__(self, v): self._v = v
    def reveal(self) -> str: ...
    def __repr__(self): return "SecretStr(***)"
    __str__ = __repr__

@dataclass(frozen=True)
class ProviderSpec:
    source: str          # "anthropic" | "local"  (seam: "openai"/"gemini" later)
    model: str
    endpoint: str = ""   # local only

def load_key(provider: str) -> SecretStr | None     # env / keychain / ~/.config/onco-engine/providers.env (0600-checked); None for local
def gateway_parse(schema, system, user, *, stage, spec: ProviderSpec, max_tokens=16000)
    # anthropic -> client._anthropic_parse (native parse preserved); local -> client._ollama_parse
    # records stage_log with the ACTUAL provider+model; fails closed on missing key (api) / bad endpoint (local)
```

**Acceptance tests:** Anthropic spec routes to the native parse (monkeypatch `_anthropic_parse`, assert called + stage_log records model); local spec routes to `_ollama_parse`; missing key for an api spec fails closed; `SecretStr` never reveals via repr/str/json. (Real network never hit — monkeypatch the leaf parsers.)

---

## WP-M2.2 — Reviewer roster + independence preflight

**Files:** Modify `core/sr/config.py` (roster accessor), Create `core/sr/orchestrator/reviewers.py`, `tests/orchestrator/test_reviewers.py`.

**Contract:**
```python
def resolve_reviewer(cfg, name: str) -> ProviderSpec   # from cfg.reviewers[name]; defaults documented
def assert_independent(r1: ProviderSpec, r2: ProviderSpec) -> None
    # fail closed (ReviewerIndependenceError) if same (source, model) or same local endpoint,
    # or same model FAMILY (e.g. both claude-*). Records resolved identities for provenance.
```

**Acceptance tests:** identical specs raise; same-family (`claude-opus`/`claude-sonnet`) raises; same local endpoint raises; genuinely-different (anthropic vs ollama) passes; missing roster entry falls back to a documented default and is recorded.

---

## WP-M2.3 — Dual independent-LLM screening + honest agreement

**Files:** Create `core/sr/orchestrator/dual_screen.py`, wire into `engine.build_registry` (replace `screen` when a 2-reviewer roster is configured), `tests/orchestrator/test_dual_screen.py`.

**Contract:**
```python
def dual_screen_adapter(run_dir, ctx) -> StageResult
    # resolve r1,r2; assert_independent; screen to_screen with each via gateway (per-record);
    # reconcile via core.sr.reconcile.reconcile -> consensus + conflicts;
    # write screening_r1.json, screening_r2.json, screening.json (consensus), reconcile.json;
    # summary records llm_llm_agreement (NOT inter_rater_reliability) + n_conflicts + resolved reviewer ids
```
Honesty gate stays on the human conflict adjudication (the consensus is a draft until a human adjudicates — §7a). The agreement metric is labeled `llm_llm_agreement` everywhere (provenance + summary), never "inter-rater reliability".

**Acceptance tests (stub the gateway):** two stubbed reviewers with a disagreement produce a conflicts list + consensus (union rule) + an `llm_llm_agreement` number; same-family roster fails closed before any call; provenance records both resolved reviewer identities; metric is never labeled inter-rater reliability.

---

## WP-M2.4 — Security hardening

**Files:** Modify `core/llm/gateway.py` (egress + SSRF), Create `core/sr/orchestrator/security.py` (scrub_exception, endpoint validation, data policy), wire `scrub_exception` into `engine.run_stage` + gateway error paths, `.gitignore` (`~/.config/onco-engine` note), `tests/orchestrator/test_security.py`.

**Contract:**
```python
def validate_endpoint(url: str, *, allow_remote: bool = False) -> None
    # reject link-local/metadata (169.254/16, fe80::/10), require https or loopback,
    # reject non-loopback/private unless allow_remote; raise SSRFError
def scrub_exception(exc: BaseException) -> str        # typed code + provider/model + http status ONLY (no message/payload/keys)
def assert_egress_allowed(provider: str, content_class: str) -> None
    # per-provider data_policy (retention/trains_on_data/allowed_content_classes); fail closed
```

**Acceptance tests:** SSRF rejection of metadata/link-local/remote URLs (allow_remote opt-in passes a LAN URL); `scrub_exception` never returns key-shaped or message text (feed an exception carrying a fake `sk-...`); egress fail-closed when a provider's policy forbids full-text/manuscript content classes; `run_stage` persists only the scrubbed code on failure.

---

## Self-review
- §4 gateway + sources → WP2.1/2.2; independence preflight (§7a/§4) → WP2.2; llm_llm_agreement (§8) → WP2.3; SSRF + scrub + egress (§10) → WP2.4.
- LiteLLM deliberately deferred (security surface); OpenAI/Gemini via the WP2.1 seam when needed — documented, not built.
- Honesty gate on screening adjudication preserved (consensus is a draft).

## Execution
Dual-harness: WP-M2.1 → … → WP-M2.4, each gated on Codex APPROVE + suite green. Then plan M3.
