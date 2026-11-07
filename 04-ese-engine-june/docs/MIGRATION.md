# Migration from onco-evidence-engine

The systematic review + statistical analysis stack now lives in **evidence-synthesis-engine**.

| Old | New |
|-----|-----|
| `~/Projects/onco-evidence-engine` SR work | `~/Projects/evidence-synthesis-engine` |
| `SR_VERTICAL` | `ESE_VERTICAL` (alias kept) |
| `python -m core.tools recipe run full-review` | `ese init` + `ese next` loop |
| SystematicReview skill (old branch) | EvidenceSynthesis skill |
| Case-analysis web app | Stays in onco-evidence-engine (unchanged) |

Do not maintain two SR codebases. Use this repo as canonical upstream.
