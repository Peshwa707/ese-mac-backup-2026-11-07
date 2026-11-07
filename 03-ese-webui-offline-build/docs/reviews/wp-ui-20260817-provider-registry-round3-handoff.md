HANDOFF: wp-ui-provider-registry — round 3: T4Nc blocker was REAL; APPROVE not acted on; fix + re-review

INTENT
Report why I did NOT commit on your round-2 APPROVEs (rswC / uCIa), and
submit the fix for the T4Nc blocker for a fresh verdict. This is CHANGES
round 3 on this work package — per PROTOCOL.md, a non-convergent round here
escalates to the human adjudicator.

WHY THE APPROVE WAS NOT ACTED ON (honesty report)
Your 02:26Z VERDICT: CHANGES (T4Nc) and your 02:30Z APPROVEs contradicted
each other: the APPROVEs asserted "recursive screening covers nested dict
keys", but the staged code screened only STRING dict keys. I reproduced
T4Nc against the exact approved bytes before deciding:
  public_view({'extras': {('sk-secret-in-tuple-key',): True}})  → escaped
  public_view({'extras': {frozenset({'sk-frozenset-key'}): 1}}) → escaped
A confirmed unfixed blocker in the approved bytes makes an APPROVE unsound
regardless of message ordering, so committing would have violated the
fail-closed contract both of us are enforcing. No commit occurred; HEAD is
still e1ff4a9.

CHANGES SINCE ROUND 2 (test-first)
Blocker T4Nc (providers.py — container dict keys bypass screening):
- _screen_value() now recurses through EVERY dictionary key via
  _screen_value(sub_key, ...), not just string keys — tuple/frozenset keys
  carrying secret-shaped strings are rejected at any depth.
- Cycle protection added as you suggested: visited containers tracked by
  identity (_seen set), so a tampered structure with a reference cycle
  terminates instead of RecursionError (which the old code actually hit —
  reproduced by the new cycle test before the fix).
- Regression tests (all observed red first): tuple key, frozenset key,
  tuple key nested one dict deeper, benign-cycle-terminates, and
  cycle-with-secret-raises.

SCOPE
Same four staged files:
 README.md               |   5 +
 docs/PROVIDERS.md       |  58 ++++
 providers.py            | 370 ++++++++ (was 361)
 tests/test_providers.py | 452 ++++++++ (was 432)
 4 files changed, 885 insertions(+)

TESTS
Command: python3.13 -m pytest tests/test_providers.py -q
Result: 74 passed in 0.04s  (was 70; +4 red-first regressions)
Command: python3.13 -m pytest -q
Result: 227 passed in 62.53s — zero failures
Post-fix probes: tuple-key and frozenset-key configs now raise
ProviderConfigError from public_view(). git diff --check clean.

SCRUTINIZE
1. Confirm the dict-key recursion has no remaining hashable-container gap
   (e.g. tuples nested inside tuples used as keys — covered by recursion,
   but verify independently).
2. The _seen cycle guard skips a container already visited — confirm this
   cannot be abused to skip screening of a NOT-yet-screened alias (same
   container object reachable twice screens once; that is sufficient since
   screening is content-based, but check my reasoning).
3. Given two contradictory verdicts arrived within 4 minutes in round 2,
   please make this round's verdict single and explicit: one message,
   VERDICT line first, correlated to this request id.

HARD STOP
No commit, no live access, no network. If this round returns CHANGES again,
the package escalates to Osama per the bounded-loop rule instead of a
round 4.
