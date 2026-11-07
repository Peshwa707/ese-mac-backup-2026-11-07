# Orchestration Engine — M1 (Trust Backbone) Implementation Plan

> **For agentic workers:** This plan is executed via the **Claude↔Codex DualReview harness**. Each work package (WP) is implemented by the implementer, reviewed by the reviewer; commit ONLY on `VERDICT: APPROVE` with the full pytest suite green. Steps use checkbox (`- [ ]`) tracking. Within a WP, follow TDD: failing test → minimal impl → green → commit.

**Goal:** Build the orchestrator state machine, uniform stage adapters over the existing pipeline, hash-based resumable state, and machine-enforced honesty gates — the trust backbone the rest of the engine plugs into.

**Architecture:** A declarative dependency-DAG of stages, each wrapped by a uniform adapter `fn(run_dir, ctx) -> StageResult`. All progress lives in an atomically-written, lock-guarded `runs/<id>/run_state.json` keyed by content hashes. Honesty gates clear only via signed, content-bound human-approval records; export independently re-verifies them. Sequencing currently fused in `core/sr/pipeline.py:run_sr`/`build_run` migrates into the orchestrator (sentinel + provenance included).

**Tech stack:** Python 3.11, stdlib (hashlib, hmac, os.replace, fcntl), Pydantic v2, existing `core/sr/*`, pytest.

**Spec:** `docs/superpowers/specs/2026-06-16-idea-to-meta-analysis-orchestration-engine-design.md` (§3, §5, §6, §7a, §8, §15).

---

## File structure (new + modified)

New package `core/sr/orchestrator/`:
- `state.py` — `RunState` model, atomic write, run lock, integrity checksum, `schema_version` + `migrate()`.
- `hashing.py` — content hashing + artifact registry + staleness computation.
- `stages.py` — `Stage`, `RunContext`, `StageResult`, the `STAGES` DAG registry, topo scheduler, `next_stage`.
- `adapters.py` — one uniform adapter per existing stage function.
- `gates.py` — `GateRecord`, signing/verification, `--auto` quarantine, export re-verification.
- `provenance.py` — run-dir provenance (`provenance.json`, `environment.json`, `search_manifest.json`, `llm_io/`).
- `engine.py` — lifecycle: `create_run`, `next_stage`, `run_stage`, `advance`, `resume`, `approve`.

Modified:
- `core/sr/pipeline.py` — `run_sr` becomes a thin wrapper over the orchestrator; sentinel + provenance assembly move out of `build_run` into the orchestrator (`build_run` kept as the "assemble SRRun" adapter callee).
- `core/render/provenance.py` — accept a target dir (run dir) instead of hardcoded `/tmp`.
- `.gitignore` — add `runs/` and `~/.config/onco-engine` patterns; keep `verticals/` committable.

Tests under `tests/orchestrator/`.

---

## WP-M1.1 — Atomic, lock-guarded run-state store

**Files:** Create `core/sr/orchestrator/state.py`, `tests/orchestrator/test_state.py`.

**Contract:**
```python
SCHEMA_VERSION = 1

class StageState(BaseModel):
    name: str
    status: Literal["pending","running","awaiting_approval","approved","done","failed","stale"] = "pending"
    input_hashes: dict[str, str] = {}     # artifact-name -> sha256 consumed
    output_hashes: dict[str, str] = {}    # artifact-name -> sha256 produced
    params_hash: str = ""                 # config/code/prompt-version fingerprint
    error_code: str = ""                  # scrubbed; never raw exception text
    gate_record_id: str = ""              # set when an honesty gate clears (WP-M1.5)

class RunState(BaseModel):
    schema_version: int = SCHEMA_VERSION
    run_id: str
    profile: str = "systematic"
    auto: bool = False
    stages: dict[str, StageState] = {}
    created_utc: str                       # passed in; no Date.now in lib code

def load(run_dir: Path) -> RunState        # validates checksum; raises on corruption
def save(run_dir: Path, state: RunState)   # atomic: write tmp + fsync + os.replace; updates sidecar checksum
def migrate(raw: dict) -> dict             # bump older schema_versions forward
@contextmanager
def run_lock(run_dir: Path)                # fcntl.flock lease; raises if already held
```

**Acceptance tests (write first, must fail, then implement):**
- `test_atomic_save_survives_partial_write`: monkeypatch `os.replace` to raise after tmp write → original `run_state.json` intact and loadable.
- `test_checksum_detects_corruption`: flip a byte in `run_state.json` → `load` raises a clear error.
- `test_run_lock_excludes_second_holder`: acquiring the lock twice (same proc, non-reentrant) raises.
- `test_migrate_v0_to_current`: a dict without `schema_version` loads via `migrate` to current.

**Done when:** tests pass, suite green, Codex `APPROVE`. Commit.

---

## WP-M1.2 — Content hashing + staleness

**Files:** Create `core/sr/orchestrator/hashing.py`, `tests/orchestrator/test_hashing.py`.

**Contract:**
```python
def hash_bytes(b: bytes) -> str            # sha256 hex
def hash_file(p: Path) -> str
def hash_json(obj) -> str                  # canonical (sorted keys) sha256
def is_stale(stage: StageState, upstream: dict[str,str]) -> bool
    # True if any recorded input_hash != the current upstream output_hash for that artifact
```

**Acceptance tests:**
- `test_hash_determinism`: same bytes/JSON → same hash; key order irrelevant for `hash_json`.
- `test_is_stale_true_on_changed_upstream`: change an upstream output hash → downstream stale.
- `test_is_stale_false_when_unchanged`.

**Done when:** green + Codex `APPROVE`. Commit.

---

## WP-M1.3 — Stage model, dependency DAG, scheduler

**Files:** Create `core/sr/orchestrator/stages.py`, `tests/orchestrator/test_stages.py`.

**Contract:**
```python
@dataclass(frozen=True)
class Stage:
    name: str
    fn: Callable                     # fn(run_dir: Path, ctx: RunContext) -> StageResult
    requires: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()
    gate: bool = False
    honesty_gate: bool = False
    reproducibility: Literal["deterministic","stochastic"] = "deterministic"
    profiles: frozenset[str] = frozenset({"systematic","scoping","rapid","living"})

@dataclass
class RunContext:
    cfg: SRConfig
    run_dir: Path
    auto: bool

@dataclass
class StageResult:
    outputs: dict[str, Path]         # artifact-name -> path written
    summary: dict                    # human-facing, scrubbed
    deterministic_inputs: dict[str,str] = field(default_factory=dict)

def topo_order(stages: list[Stage], profile: str) -> list[Stage]   # profile-filtered, cycle-detected
def next_stage(state: RunState, stages: list[Stage]) -> Stage | None
    # first stage whose deps are all `done`/`approved` and which is pending or stale
def restale(state: RunState, stages: list[Stage], edited: str) -> set[str]
    # transitive descendants over `requires` only (NOT positional)
```

**Acceptance tests:**
- `test_topo_order_respects_requires`: a DAG with `b requires a`, `c requires a` → `a` precedes both.
- `test_topo_detects_cycle`: cyclic `requires` raises.
- `test_next_stage_skips_done_returns_first_runnable`.
- `test_restale_only_true_descendants`: editing an optional `risk_of_bias` node restales `grade`/`manuscript` but NOT `stats_nma` (the architect's case).
- `test_profile_filter_uses_phases`: a `scoping` profile excludes stats/GRADE stages.

**Done when:** green + Codex `APPROVE`. Commit.

---

## WP-M1.4 — Uniform stage adapters over existing functions

**Files:** Create `core/sr/orchestrator/adapters.py`, `tests/orchestrator/test_adapters.py`.

Wrap each existing function to the uniform signature, reading inputs from the run dir / `RunContext` and writing hashed artifacts. Adapters (existing fn → artifact):
- `search` → `gather_corpus(cfg, ...)` (async; adapter owns `asyncio.run`) → `corpus.json` + `search_manifest.json`.
- `prescreen` → `prescreen_records(corpus.verified)` → `prescreen.json`, `to_screen.json`.
- `screen` → `_screen_all`/`screen_batch` → `screening.json` (M1: single-reviewer via existing client path; dual-LLM is M2).
- `fulltext` → `resolve_many` (async) → `fulltext_coverage.json`.
- `extract` → `_extract_all` → `extractions.json`.
- `risk_of_bias` → existing RoB build/collect → `rob.json`.
- `grade` → `grade_outcomes` → `grade.json`.
- `stats` → `meta_analyze(extractions, modality_by_pmid, fig_dir, cfg=cfg)` → `stats.json` + `figures/` (adapter reconstructs `modality_by_pmid` from extractions).
- `assemble` → `build_run(corpus, screening, extractions, prescreen)` → `srrun.json` (sentinel runs here; see WP-M1.7).
- `manuscript` → `export_project(...)` → export dir (gated; WP-M1.5).

**Acceptance tests (use a small fixture corpus; stub the LLM stages so no network):**
- `test_search_adapter_writes_corpus_and_manifest`.
- `test_stats_adapter_reconstructs_modality_and_pools` (reuse the existing 62-extraction fixture path or a trimmed copy).
- `test_async_adapter_runs_under_sync_orchestrator` (search/fulltext complete when called synchronously).
- `test_every_adapter_records_output_hashes`.

**Done when:** green + Codex `APPROVE`. (The harness may split this WP per-adapter; each split still gated on `APPROVE`.) Commit.

---

## WP-M1.5 — §7a machine-enforced honesty gates

**Files:** Create `core/sr/orchestrator/gates.py`, `tests/orchestrator/test_gates.py`.

**Contract:**
```python
class GateRecord(BaseModel):
    gate: str                       # stage name
    actor: str                      # human identity (not "auto"/model id)
    ts_utc: str
    artifact_sha256: str            # hash of the exact approved bytes
    signature: str                  # HMAC-SHA256 over (gate|actor|ts|artifact_sha256) with a local key

LOCAL_KEY_PATH = ~/.config/onco-engine/gate.key   # 0600, gitignored; created on first approve

def sign_approval(gate, actor, artifact_path, key) -> GateRecord
def verify_record(rec: GateRecord, artifact_path: Path, key) -> bool
def approve(run_dir, gate, actor) -> GateRecord     # out-of-band human action; refuses if actor in {"auto",""}
def export_allowed(run_dir, state, stages) -> tuple[bool, list[str]]
    # True only if every honesty_gate stage has a valid record matching CURRENT artifact bytes AND no stage is stale
```

**Acceptance tests:**
- `test_hand_edited_approved_flag_without_signature_blocks_export`: set `status=approved` directly in JSON, no GateRecord → `export_allowed` False.
- `test_auto_cannot_clear_honesty_gate`: `approve(..., actor="auto")` raises; `--auto` run reaches `awaiting_approval` and `export_allowed` is False.
- `test_export_reverifies_against_current_bytes`: approve, then mutate the artifact → `verify_record` False → export blocked.
- `test_upstream_edit_unclears_downstream_gate`: editing screening restales + invalidates the extraction gate record.
- `test_signature_roundtrip`: `verify_record(sign_approval(...))` True; wrong key False.

**Done when:** green + Codex `APPROVE`. Commit.

---

## WP-M1.6 — Reproducibility provenance in the run dir

**Files:** Create `core/sr/orchestrator/provenance.py`, `tests/orchestrator/test_provenance.py`; modify `core/render/provenance.py` (accept a `dir` arg; default unchanged for back-compat).

Write under `runs/<id>/`:
- `provenance.json` — per-stage model/provider/version (from `_llm.stage_log()`), prompt-version hashes, gate decisions w/ actor, agreement metrics, curation drops, reproducibility tier.
- `environment.json` — engine git SHA + dirty flag, `python --version`, `pip freeze`, `R --version` + invoked package versions, OS (timestamps passed in, not generated in lib code).
- `search_manifest.json` — per-source query, UTC ts, frozen returned PMID/DOI set + retrieval hashes, retraction-status snapshot.
- `llm_io/<stage>.jsonl` — resolved model id, request/response ids, params, token counts, raw response; written through the scrubbed sink (full `scrub_exception` hardening is M2; M1 ensures no raw exception/key is written).

**Acceptance tests:**
- `test_provenance_written_under_run_dir` (not `/tmp`).
- `test_environment_captures_git_sha_and_versions`.
- `test_search_manifest_freezes_pmid_set`.
- `test_deterministic_stage_reproduces_hash`: re-running the stats adapter on identical inputs yields identical output hash.

**Done when:** green + Codex `APPROVE`. Commit.

---

## WP-M1.7 — Migrate `run_sr`/`build_run` into the orchestrator + lifecycle entrypoint

**Files:** Create `core/sr/orchestrator/engine.py`, `tests/orchestrator/test_engine_e2e.py`; modify `core/sr/pipeline.py`, callers in `core/tools/builtin.py`, `.gitignore`.

- Build the `STAGES` registry wiring the WP-M1.4 adapters with real `requires` edges and the §3 gate/honesty_gate/reproducibility flags.
- `engine.create_run/next_stage/run_stage/advance/resume/approve` drive the loop; `advance` records hashes + (for honesty gates) requires a GateRecord; sentinel runs in the `assemble` stage and still fails closed under `cfg.sentinel_strict`.
- `run_sr` becomes a thin compatibility wrapper that drives the orchestrator end-to-end (auto mode for the legacy path) and returns the same `SRRun`.
- Add `runs/` to `.gitignore`.

**Acceptance tests:**
- `test_orchestrated_run_matches_build_run`: on a fixture corpus + stubbed LLM stages, the orchestrated `SRRun` equals the legacy `build_run` output (counts, exclusion buckets, sentinel, named-trial capture).
- `test_sentinel_still_fails_closed`: a missing pivotal trial under `sentinel_strict` raises.
- `test_resume_continues_from_each_state`: kill after each stage, `resume` completes.
- `test_runs_dir_gitignored`.
- Full suite green (existing tests still pass — `run_sr` back-compat).

**Done when:** green + Codex `APPROVE`. Commit. **M1 complete.**

---

## Self-review (done)

- **Spec coverage:** DAG+adapters (§3) → WP1.3/1.4; hashed atomic state + lock + recovery (§5) → WP1.1/1.2/1.7; §7a enforcement → WP1.5; reproducibility provenance + run-dir move (§6/§8) → WP1.6; migration (§15) → WP1.7. Security hardening beyond run-dir move + no-raw-error is explicitly M2 (noted in WP1.6). Front-end, dual-LLM, evidence-strength gate, benchmark mode are M2–M4 (not M1) by design.
- **Placeholder scan:** none; every WP has concrete contracts + named acceptance tests.
- **Type consistency:** `StageState`/`RunState`/`Stage`/`RunContext`/`StageResult`/`GateRecord` names and fields are consistent across WPs; `next_stage`/`restale`/`export_allowed`/`approve` signatures stable.

## Execution

Executed via the **DualReview harness**: implement WP-M1.1 → Codex review → address `CHANGES` → commit on `APPROVE` (suite green) → next WP, through WP-M1.7. Then plan M2.
