# WebUI stage 8 risk-of-bias compatibility receipt

UI_ROB_VERIFIED: true

- WebUI repository: `evidence-synthesis-engine-webui`
- Engine repository: `evidence-synthesis-engine-screen-prove`
- Engine commit: `30c393458c53c9944a452a888097b69753d01133`
- Engine contract: `rob-assessment-v1`
- Verified command: `screen_prove.cli rob`
- Verified through: `tests/test_webui.py::test_stage_eight_rob_runs_against_real_engine_cli`
- Offline boundary: local fixture and locally resolved screening queue only; no network, live connector, credential, model, subscription, or institutional access.
- Scope: one bounded risk-of-bias assessment with domain rationale, source links, and explicit `not_assessed`; synthesis, full-stack receipt, and live qualification remain separate stages.
