# WebUI Compatibility Work Package Handoff

## Goal

Verify the WebUI's complete offline run path against the isolated Screen and
Prove engine checkout while preserving the WebUI's four-artifact package and
byte-replay contract. No live APIs, credentials, subscriptions, or network
access are in scope.

## Base and scope

- Repository: `/Users/neurologic/Projects/evidence-synthesis-engine-webui`
- Branch: `codex/webui-offline-build`
- Base commit: `d5d6dca3b4a1af16f8c85f597ff50a5edf4ca7c8`
- Intended files: `README.md`, `bridge.py`, `docs/UI_BUILD_LOOP.md`,
  `server.py`, `tests/test_webui.py`
- Unrelated untracked helper `tools/codex` is intentionally not staged.

## Changes

- Align `lock` and `screen` bridge calls with the real engine CLI and normalize
  the engine's `screening-v1.results` into the WebUI screening state.
- Derive the locked population from the real engine's dedup artifact when its
  protocol-lock artifact does not embed record rows, while retaining the stub
  lock-record contract used by offline tests.
- Align package/replay calls with the real engine CLI. The bridge preserves the
  engine package inside a WebUI wrapper containing the four artifact bytes and
  manifest; replay invokes the engine verifier and reconstructs the WebUI
  replay artifacts from that portable payload.
- Invalidate `decisions.json` and the screening cache whenever import, dedup,
  or lock is rerun, so cached screening inputs cannot survive a changed
  upstream record population.
- Keep stub replay mismatch and false-success tests fail-closed, including
  portable replay after moving the package away from the original run.
- Record the stage-6 compatibility contract in the build-loop documentation.

## Verification

Executed in the WebUI repository:

```text
python -m pytest -q
117 passed in 45.11s

python -m pytest -q tests/test_webui.py -k 'stage_six_full_pipeline_runs_against_real_engine_cli'
1 passed, 96 deselected in 0.84s

python -m pytest -q tests/test_webui.py -k 'bridge_package_run or bridge_replay_run or stub_replay_reconstructs_solely_from_package_content'
5 passed, 92 deselected in 0.02s

python -m pytest -q tests/test_webui.py -k 'upstream_rerun_discards_screen_inputs_when_record_ids_change'
1 passed

git diff --check
passed
```

The stage-6 test uses the local isolated engine checkout only. It does not
modify that checkout and does not perform live research or model calls.

## Review focus

- Confirm the real engine CLI argument shapes and artifact semantics are
  represented accurately.
- Confirm the WebUI wrapper does not silently convert engine verification into
  a claim of scientific validity.
- Confirm replay reconstruction is limited to the portable package path and
  that stub failure/mismatch tests remain fail-closed.
- Confirm unrelated files are excluded from the package.
- Confirm upstream reruns remove the decision map and cache before a changed
  record population is screened.

## Known caveats

- `docs/reviews/UI_COMPATIBILITY_RECEIPT.md` has not been created yet; it must
  be written only after this staged diff receives `VERDICT: APPROVE` and the
  independent reviewer confirms the live local compatibility test.
- This is an offline CLI compatibility result, not live-API qualification,
  scientific validation, or authorization to open network connectors.
