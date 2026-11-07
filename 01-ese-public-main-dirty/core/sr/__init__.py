"""Systematic-review vertical: a PRISMA-style review pipeline that reuses the
engine's grounded retrieval / verification / provenance machinery.

Milestone 1 implements the live data slice (protocol Steps 3-6): execute the
PubMed search, dedup, verify every PMID, AI title/abstract screening (Reviewer 1),
PRISMA flow counts, and abstract-level data extraction with NR/ESTIMATED flags.
"""
