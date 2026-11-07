# WebUI stage 9 synthesis compatibility receipt

UI_SYNTHESIS_VERIFIED: true

- WebUI repository: `evidence-synthesis-engine-webui`
- Engine repository: `evidence-synthesis-engine-screen-prove`
- Engine commit: `30c393458c53c9944a452a888097b69753d01133`
- Engine contract: `synthesis-calculation-v1`
- Verified command: `screen_prove.cli synthesize`
- Verified through: `tests/test_webui.py::test_stage_nine_synthesis_runs_against_real_engine_cli`
- Offline boundary: local fixture only; no network, live connector, credential, model, subscription, or institutional access.
- Scope: one protocol-supplied single-study calculation with effect-measure and uncertainty validation. Cross-study pooling, meta-analysis, treatment conclusions, full-stack receipt, and live qualification remain separate stages.
