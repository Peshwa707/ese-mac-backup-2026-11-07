# Phase 1 — n=3 pilot (working dir)

Validates the benchmark chain (protocol → vertical → run → score) before scaling to 50. Per the program: exhaustive retrieval, date-bounded to each protocol's search cutoff.

## Pilot slate
| # | Study | PMID | Stat path | Search cutoff | Status |
|---|---|---|---|---|---|
| 1 | Liang 2024 — BCMA CAR-T vs bispecific, 3L+ RRMM | 39551604 | proportion (CAR-T/BsAb) | 2024-05-31 | anchor; vertical `mm-bcma-3l` exists; key + score DONE |
| 2 | Blood PD-L1 in NSCLC on ICI | 37821941 | log_effect (HR) | 2023-02-23 | protocol + key drafted (human-verify) |
| 3 | T-DXd ILD/cardiotoxicity incidence | 37481956 | proportion (single-arm) | (in OA full text) | selected; protocol/key TBD |

## Free validation result (no engine re-run, $0)
Re-pooled the existing exhaustive Liang run (`runs/mm-bcma-3l`, deterministic) and scored it vs the published key (`liang-39551604_answer_key.json`):
- **12/12 outcomes matched** — every CAR-T/BsAb CR/ORR/CRS/cytopenia CI overlaps the published value (e.g. CR CAR-T eng 55.6% [46.1-64.9] vs pub 54% [42-69]; BsAb 33.3% [22.9-44.6] vs 35% [30-41]).
- Integrity: 15 ESTIMATED (flagged), 247 NR, 6 pooled-excluded — no silent fabrication.
- Confirms the Phase 0 scorer works end-to-end on a real exhaustive run, and re-confirms the Liang benchmark with the new tooling.

## Remaining gate
Fresh exhaustive runs for #2/#3 (screen+extract over full date-bounded corpora) need the cloud-vs-local cost decision. Verticals for #2/#3 not yet compiled.
