HANDOFF: wp-ui-provider-registry — round 2 re-review after CHANGES (rq-114505a86d76)

INTENT
Address both blockers from your controlling VERDICT: CHANGES (message id
20260818-022014-365520-TbhP; your correction note retracting the interim
APPROVE is acknowledged). Same work package, same boundary — still nothing
committed.

CHANGES SINCE ROUND 1 (both blockers reproduced first, then fixed test-first)
1. Blocker: unhashable provider value ([] / {}) raised raw TypeError at the
   registry membership check.
   - Reproduced: TypeError "unhashable type: 'list'" confirmed.
   - Fix (providers.py, validate_provider_config): provider id is now
     type-checked (`isinstance(provider_id, str)`) before the membership
     test, so every malformed provider value raises ProviderConfigError.
   - Regression tests: `{"provider": []}`, `{"provider": {}}`,
     `{"provider": None}` added to
     test_malformed_or_unknown_configuration_is_rejected — observed red
     (TypeError) before the fix, green after.
2. Blocker: _screen_config_strings screened only top-level strings plus one
   dict level; public_view() emitted secret-shaped strings nested in lists
   or deeper dicts, and never screened dict KEYS.
   - Reproduced: public_view returned a config carrying 'sk-nested-secret'
     inside a nested list.
   - Fix (providers.py): new recursive _screen_value() walks strings, dict
     keys AND values, and list/tuple/set/frozenset members at any depth;
     _screen_config_strings now delegates to it. Both
     validate_provider_config and public_view use the recursive screen.
   - Regression tests: test_public_view_screens_nested_structures_recursively
     (5 parametrized cases: list value, two-levels-deep dict, list-in-dict,
     secret-shaped dict KEY, tuple) — all observed red before the fix —
     plus test_validate_screens_nested_secret_material_in_config_values.

SCOPE
Same four staged files, no new files:
 README.md               |   5 +
 docs/PROVIDERS.md       |  58 +++++++
 providers.py            | 361 ++++++++++++++++ (was 344)
 tests/test_providers.py | 432 ++++++++++++++++ (was 396)
 4 files changed, 856 insertions(+)
Out of scope: unchanged from round 1 (server wiring, operator gate
implementation, suite-wide socket-blocking fixture, pai_loop/bridge/store).

TESTS
Command: python3.13 -m pytest tests/test_providers.py -q
Result: 70 passed in 0.05s  (was 61; +9 regression tests, all red-first)
Command: python3.13 -m pytest -q
Result: 223 passed in 63.41s — zero failures
Direct probes after fix:
- validate_provider_config({'provider': []}) → ProviderConfigError (was TypeError)
- validate_provider_config({'provider': {}}) → ProviderConfigError
- public_view({... {'inner': ['sk-nested-secret']}}) → ProviderConfigError (was silent escape)
git diff --check clean; HEAD unchanged at e1ff4a9 (no commit).

SCRUTINIZE
- The recursive screen ignores non-string scalars and does not recurse into
  arbitrary objects (only str/dict/list/tuple/set/frozenset). A custom
  object with a secret in its repr would pass the screen — but validation
  rejects such shapes long before public_view, and public_view is typed for
  validated dict configs. Flag if you consider that reachable.
- Screening now also covers dict keys; error text uses _safe_token so the
  offending key/value is never echoed. Check I didn't introduce an echo
  path in the new "where" breadcrumbs.
- Round-1 non-blocking notes (substring over-rejection, unbounded label)
  remain deliberately deferred per your APPROVE-note framing; say if either
  should be promoted to a blocker for this WP.

HARD STOP
Still no live APIs, credentials, network calls, or engine-repo changes.
Nothing committed; awaiting your fresh verdict.
