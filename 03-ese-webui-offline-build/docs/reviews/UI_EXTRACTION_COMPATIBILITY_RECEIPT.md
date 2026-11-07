# WebUI stage 7 extraction compatibility receipt

UI_EXTRACTION_VERIFIED: true

- WebUI repository: `evidence-synthesis-engine-webui`
- Engine repository: `evidence-synthesis-engine-screen-prove`
- Engine commit: `30c393458c53c9944a452a888097b69753d01133`
- Engine contract: `extraction-record-v1`
- Verified command: `screen_prove.cli extract`
- Verified through: `tests/test_webui.py::test_stage_seven_extraction_runs_against_real_engine_cli`
- Offline boundary: local UTF-8 fixture only; no network, live connector, credential, model, subscription, or institutional access.
- Scope: one source-anchored extraction record with reported, `NR`, and `ESTIMATED` fields. Risk of bias, synthesis, full-stack receipt, and live qualification remain separate stages.
