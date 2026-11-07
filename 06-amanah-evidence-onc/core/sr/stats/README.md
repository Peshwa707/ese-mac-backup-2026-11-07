# R requirement (Phase 4 meta-analysis)

The quantitative meta-analysis uses R via `Rscript` (no rpy2). One-time bootstrap:

```
Rscript -e 'install.packages(c("metafor","meta"), repos="https://cloud.r-project.org")'
```

`metafor` is the only hard requirement (Freeman-Tukey pooled proportions, hazard-ratio
inverse-variance pooling, and forest plots). If R is absent, the Statistical Analysis
component falls back to an honest stub.

## Optional: Bayesian NMA (Phase F)
Network meta-analysis needs JAGS + gemtc:
```
brew install jags glpk
Rscript -e 'install.packages(c("rjags","igraph","Rglpk","gemtc"), repos="https://cloud.r-project.org")'
```
NMA only runs when a CONNECTED network exists (studies sharing a comparator node).
On abstract-level data without distinct comparator-arm labels, the engine reports
the network as infeasible rather than fabricating a shared comparator — wire in
full-text/IPD comparator arms (`build_nma_edges`) to enable it.

## Optional: Dose-response meta-analysis (dosresmeta + RCS)
```
Rscript -e 'install.packages(c("dosresmeta","rms"), repos="https://cloud.r-project.org")'
```
Models dose vs outcome with restricted cubic splines (Greenland–Longnecker two-stage).
HONESTY-GATED and additive: if the module is absent OR no per-dose-level data exists, it
reports "dose-response not performed — <reason>" and fabricates NO curve; every other stage
runs regardless. Needs >=3 studies each reporting the outcome at >=3 distinct dose levels in
the SAME unit. Abstract-level extraction has no dose tiers (default = honest "no per-dose
data"); supply a per-study dose table (`build_dose_data(..., dose_table=)`) to enable.
`dose_table = {pmid: {"unit": "...", "levels": [{"dose": x, "events": e, "n": n}, ...]}}`.
