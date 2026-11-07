# ESE Offline Workbench

An independent WebUI package for the Evidence Synthesis Engine. It implements
stages 1 through 9 (operator shell, frozen input, deduplication and lock,
screening, replay and audit, compatibility, source-anchored extraction, and
bounded risk of bias and single-study synthesis)
of `docs/UI_BUILD_LOOP.md`: a local operator surface, not an AI dashboard.
It lets a user create or select a run, drive the offline screening workflow,
package and replay screened artifacts, and submit a frozen UTF-8 full-text
source for a tamper-detectable extraction record with reported, `NR`, and
`ESTIMATED` fields. No live database, model, subscription, credential, or
institutional connector is used.

Current boundary:

- Uses the isolated ESE Screen-and-Prove CLI through `bridge.py` for run
  initialization (`init`), the offline diagnostic (`doctor`), source import
  (`import`), deduplication (`dedup`), protocol locking (`lock`), cached
  screening (`screen`), source-anchored extraction (`extract`), bounded
  risk-of-bias assessment (`rob`), single-study synthesis (`synthesize`),
  packaging (`package`), and replay (`replay`).
- Runs on localhost with Python’s standard library.
- Every run record's `human_adjudication` field defaults to `off`. An
  operator can flip it at any time via the `human_adjudication` action; when
  it is `on`, any abstained screening decision opens a blocking queue entry
  that must be resolved (`include`/`exclude`) via the `adjudicate` action
  before the run's `screen` stage reads `complete` instead of `blocked`.
- `dedup` cannot run before a run's `import` stage is complete, `lock`
  cannot run before `dedup` is complete, `screen` cannot run before `lock`
  is complete, `package` cannot run before `screen` is complete, `rob` cannot
  run before `extraction` is complete, `synthesize` cannot run before `rob`
  is complete, and `replay` cannot run before `package` is complete (HTTP 409 otherwise);
  re-running an earlier stage invalidates any dependent `dedup`/`lock`/
  `screen`/`extraction`/`rob`/`synthesis`/`package`/`replay` artifacts (and any recorded adjudications)
  and stage completions built on top of it.
- `package` bundles a sha256 manifest of this run's frozen/dedup/lock/screen
  artifacts; `replay` deterministically reproduces those same artifacts and
  compares them, byte for byte, against that manifest. A replay that runs
  successfully but reproduces different bytes is reported as the run's
  `replay` stage reading `mismatched` (never `complete`), with the
  mismatching output kept on disk as audit evidence, not silently discarded.
- `import`'s, `dedup`'s, `lock`'s, `screen`'s, `package`'s, and `replay`'s
  compatibility with the real engine's CLI commands is exercised in this
  package's tests primarily through a deterministic stub, not the real
  engine checkout. `tests/test_webui.py`
  `test_stage_six_full_pipeline_runs_against_real_engine_cli` is the
  dedicated stage-6 "Compatibility" pinned engine-artifact contract check
  (skipped, not failed, when the isolated engine checkout is not present
  locally) -- see `docs/UI_BUILD_LOOP.md`'s "Stage 6 ('Compatibility')
  status" for what its presence does and does not claim.
- The WebUI's default path performs no live database calls, model calls,
  credential loading, purchases, subscriptions, or institutional access.
- `providers.py` is a credential-safe registry of *named* live providers
  (configuration validation only — credential references by env-var name or
  keychain identifier, live access rejected pending an explicit
  operator-authorized gate). The local Providers panel exposes validation,
  offline health plans, declarative manifests, and an explicit per-run gate /
  call-plan review surface. It does not read credentials or execute calls;
  issuing a gate or plan is still offline. See `docs/PROVIDERS.md`; this does
  not change the offline boundary above.
- `provider_executor.py` defines the separately authorized execution-edge
  contract for one versioned health-check fixture. It has no default
  credential resolver or transport; callers must inject both explicitly, and
  the WebUI does not wire this edge to an automatic live action yet.
- `full_text_access.py` and the WebUI's Full-text access routing panel build a
  deterministic, citation-preserving plan for PubMed Central, Crossref,
  Google Scholar, Scopus, Embase, EZproxy, publisher, interlibrary-loan, and
  manual-browser routes. The planner never logs in, reads browser sessions,
  bypasses paywalls, downloads articles, or claims that full text is verified;
  see `docs/FULL_TEXT_ACCESS.md`.
- The panel can also validate a reference-only institutional profile for
  Scopus, Embase, and EZproxy. It stores only connector mode, institution
  label, an environment-variable name, or an HTTPS proxy base URL; it never
  reads the variable, opens a session, or confirms entitlement.
- After a Scopus or Embase API-reference profile is validated, the panel can
  build an offline metadata/entitlement probe plan. The plan is not executed
  by the WebUI and cannot claim full-text retrieval.
- The panel also records manual access results from an authorized browser,
  publisher, EZproxy, Scholar, or library workflow. A full-text record is
  identity-verified only when the operator supplies matching DOI/PMID/title
  evidence; license/reuse status remains unassessed.
- The remaining user-owned live steps and stop conditions are documented in
  `docs/LIVE_AUTHORIZATION_RUNBOOK.md`; reading it does not authorize a call.
- Does not modify the original ESE checkout.

## Run locally

```bash
ESE_ENGINE_ROOT=/Users/neurologic/Projects/evidence-synthesis-engine-screen-prove \
python server.py --port 8787
```

Open <http://127.0.0.1:8787>. Run tests with `pytest -q`.

The UI package is intentionally versioned separately from the engine. Its
compatibility target is documented in `docs/UI_BUILD_LOOP.md`.
