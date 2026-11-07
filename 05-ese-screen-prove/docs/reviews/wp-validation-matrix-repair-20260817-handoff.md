HANDOFF: validation matrix repair

INTENT
Repair the offline validation matrix using the five concrete gaps identified
by Claude's independent read-only audit.

SCOPE
Only these project files are staged:
- screen_prove/dedup.py
- screen_prove/validation.py
- tests/test_validation.py

CHANGES
- Add published-label evaluation coverage and required-coverage accounting.
- Exercise human_adjudication=off with an abstention.
- Exercise replay with missing cache evidence.
- Add golden risk-difference assertions and an odds-ratio case.
- Add deliberate malformed-record rejection at the dedup boundary.

TESTS
- `python -m pytest -q tests/test_validation.py` — 60 passed in 0.30s
- `python -m pytest -q` — 296 passed in 2.68s
- `git diff --cached --check` — passed

BOUNDARY
Offline only. No live APIs, network, credentials, subscriptions,
institutional access, or external databases were used. Existing unrelated
planning files and handoffs remain unstaged and untouched. Independent review
is required before commit.

UNCERTAINTY
The one minimal behavior change is the explicit `IntegrityError` guard for
malformed deduplication records; the reviewer should confirm that it matches
the existing downstream record contract.
