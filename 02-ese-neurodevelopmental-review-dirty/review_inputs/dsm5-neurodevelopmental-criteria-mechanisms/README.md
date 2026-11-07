# DSM-5 neurodevelopmental criteria review package

This is a protocol/configuration package, not a completed evidence review. It
is intentionally frozen with `search_status: not_started` until the protocol is
approved and registered.

## Included

- `criterion_crosswalk.yaml`: 47 stable criterion IDs with controlled paraphrases
- `domain_matrix.yaml`: shared-domain, mechanism, and intervention taxonomy
- `evidence_record_schema.yaml`: record-level provenance and gate contract
- `living_review.yaml`: staged snapshot → quarterly living-update rules
- `clinician_package_template.md`: draft-only clinician handoff format
- `verticals/dsm5-neurodevelopmental-criteria-mechanisms/`: runnable engine config

## Validate the package

```bash
cd /Users/neurologic/Projects/evidence-synthesis-engine-neurodevelopmental-review
python -m pytest tests/test_dsm5_review_package.py -q
```

To select the review in the engine, use:

```bash
SR_VERTICAL=dsm5-neurodevelopmental-criteria-mechanisms ese init --profile systematic
```

The review sequence is Codex AI Reviewer 1 → Claude secondary AI review → named
human Reviewer 2 → reconciliation → qualified clinical sign-off. AI reviewer
completion is never recorded as human Reviewer 2 approval. Do not run screening
or export a clinical package until the protocol freeze and the applicable gates
are recorded.
