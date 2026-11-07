HANDOFF: WP-05 — pre-live readiness/doctor command

INTENT
Build the smallest offline, repository-scoped `screen-prove doctor` command
(queue item 1 of `docs/AUTONOMOUS_BUILD_LOOP.md`) that checks: package
installation (console script registered), CLI smoke behavior (`init` actually
runs), repository cleanliness (`git status --porcelain`), test status
(`pytest -q`, skippable via `--skip-tests`), absent remotes (`git remote`), and
absence of live-credential-shaped content in project files (`git ls-files
--cached --others --exclude-standard`). Everything runs locally against the
repo/package under test — no network access, no environment-variable reads, no
external services.

SCOPE
Files touched: `screen_prove/doctor.py` (new), `screen_prove/cli.py` (add
`doctor` subcommand), `tests/test_doctor.py` (new).
Out of scope (do not flag): provider/connector boundary contracts, canary
specs, and the full offline qualification receipt — those are queue items 2-4
and are not part of this work package.

DIFF
git diff --cached --stat:
 screen_prove/cli.py    |  14 +++
 screen_prove/doctor.py | 257 +++++++++++++++++++++++++++++++++++++++++++++++++
 tests/test_doctor.py   | 311 +++++++++++++++++++++++++++++++++++++++++++++++++
 3 files changed, 582 insertions(+)

Key hunk (screen_prove/cli.py) — new `doctor` subcommand and dispatch:
```
+    doctor = sub.add_parser("doctor")
+    doctor.add_argument("--repo-root", default=".")
+    doctor.add_argument("--skip-tests", action="store_true")
+    doctor.add_argument("--output")
...
+    if args.command == "doctor":
+        report = run_doctor(_path(args.repo_root), skip_tests=args.skip_tests)
+        payload = report.to_dict()
+        if args.output:
+            write_json(_path(args.output), payload)
+        print(json.dumps(payload, indent=2, sort_keys=True))
+        return 0 if report.passed else 1
```

Full diff is staged in the working tree (`git diff --cached`); not pasted in
full here for length — open `screen_prove/doctor.py` directly, it is the core
of this work package.

TESTS
Command: `python3 -m pytest -q`
Result: `54 passed in 1.99s` (30 pre-existing + 24 new in `tests/test_doctor.py`).

Manual dogfood check (not part of the automated suite, included for reviewer
context): `screen-prove doctor --repo-root .` against this repo's own dirty
pre-commit tree correctly reports `passed: false` with only
`repository_clean` failing (accurate — this diff is staged but uncommitted)
and every other check passing, including `tests_pass` running the real
`pytest -q` subprocess (`49 passed in 1.50s`) and `no_live_credentials`
scanning 39 tracked/addable project files with no findings.

SCRUTINIZE
1. `check_no_live_credentials` scope decision: it scans `git ls-files
   --cached --others --exclude-standard` (tracked + addable-but-untracked
   files), which deliberately excludes anything already gitignored (e.g.
   `.env`, `.ese/gate.key`). Is "in the project" correctly read as
   "committed or committable," or should it also flag gitignored secret-shaped
   files present on disk?
2. The credential content regex is a narrow heuristic (provider-prefixed
   `_API_KEY` assignments to secret-shaped values, `sk-...`, `sk-ant-...`,
   AWS `AKIA...`). It is intentionally conservative to avoid false-positiving
   on placeholder text like `ANTHROPIC_API_KEY=your-key-here` (see
   `test_check_no_live_credentials_ignores_placeholder_text`). I'd like a
   second opinion on whether this heuristic is too narrow for a pre-live gate.
3. `check_tests_pass` defaults to actually spawning `pytest -q` as a
   subprocess of the running process. Tests never exercise this default path
   directly (that would recursively invoke the whole suite); they inject a
   fake runner instead (`_fake_runner`) and cover the recursion hazard only by
   inspection/design, not by an automated test. Flag if that gap needs a
   different treatment.
4. `check_repository_clean`/`check_no_remotes`/`check_no_live_credentials` all
   shell out to `git`. If `git` is missing or the target isn't a repo, they
   fail closed (`passed: False`) rather than raising — confirm that's the
   right failure mode for a readiness gate.

REVIEW RESPONSE
The first review identified a blocker: oversized or unreadable files were
silently skipped by the credential scan. That has been fixed fail-closed.
Any in-scope non-regular file, file above `TEXT_FILE_MAX_BYTES`, invalid
UTF-8 file, or read error now produces a failed check. Regression tests cover
oversized, invalid-UTF-8, simulated read-error, and symlink cases. Gitignored files
remain outside the scan by design; this preserves the existing contract that
the pre-live gate checks tracked and committable project content, not local
secret stores.
