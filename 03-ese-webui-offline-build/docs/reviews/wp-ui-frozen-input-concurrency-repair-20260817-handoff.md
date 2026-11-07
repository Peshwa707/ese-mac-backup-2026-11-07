HANDOFF: WebUI frozen input stage — concurrency repair

INTENT
Complete the bounded frozen-input stage after independent review identified
two classes of failure: missing per-run import serialization and a regression
fixture that could not initialize a run through the engine stub.

SCOPE
The staged tree is the WebUI frozen-input package on top of the committed
operator shell. It does not expose deduplication, protocol locking,
screening, packaging, replay, live APIs, credentials, or network access.

REPAIR
- Serialize all actions for the same run with a process-local per-run lock.
- Keep the lock for the full import critical section, including cleanup,
  engine invocation, artifact verification, and persisted stage update.
- Make the concurrent-import fixture support the required `init` command.
- Close the test server explicitly in that regression's failure path so a
  failed assertion cannot cascade into misleading address-in-use failures.

TESTS
- `python -m pytest -q tests/test_webui.py` — 29 passed in 11.05s
- `python -m pytest -q` — 48 passed in 11.86s
- `git diff --cached --check` — passed

BOUNDARY
Offline only. No live APIs, network, credentials, subscriptions,
institutional access, or external communication were used. No engine source
was modified. Independent review is required before commit.

UNCERTAINTY
The reviewer should confirm that the process-local lock scope is sufficient
for this single-process local WebUI and that different run IDs remain able to
operate independently.
