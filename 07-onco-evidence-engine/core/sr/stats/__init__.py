"""Quantitative meta-analysis (protocol Step 8 / Phase B).

Pools proportion outcomes across included studies, optionally stratified by the
vertical's synthesis subgroup axis, using R's metafor (Freeman-Tukey double-arcsine,
random-effects REML) via an Rscript subprocess — deterministic and reproducible.
Studies whose value/denominator can't be parsed are excluded and counted, never
guessed. Time-to-event (HR) pooling and Bayesian NMA are separate follow-ups.
"""
