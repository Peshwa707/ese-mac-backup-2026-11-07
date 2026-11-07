"""Idea→meta-analysis orchestration engine (M1: trust backbone).

A declarative dependency-DAG of stages, each wrapped by a uniform adapter, driven
over an atomically-written, lock-guarded, hash-keyed run state with machine-enforced
honesty gates. See docs/superpowers/specs/2026-06-16-idea-to-meta-analysis-
orchestration-engine-design.md and the M1 plan.
"""
