"""Onco-Evidence Engine — domain-agnostic clinical-evidence research engine.

Nothing in this package may hardcode domain (e.g. oncology) knowledge. All
domain specifics are read from the active vertical config under ``verticals/``.
See ``tests/test_boundary.py`` for the enforced boundary rule.
"""

__version__ = "0.1.0"
