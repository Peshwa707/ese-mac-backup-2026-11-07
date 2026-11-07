"""PHI de-identification — safety-critical, fail-closed.

Two passes: redact known PHI patterns, then re-scan. If high-confidence PHI
survives the scrub, raise ``PHIDetectedError`` — the pipeline hard-stops before
any external (connector or LLM) call (spec §4.2, §8). PHI patterns are not
domain-specific, so this lives in ``core``.

LIMITATION — names. Regex de-id reliably catches *structured* identifiers
(email, SSN, phone, MRN, DOB, dates, addresses, ages ≥90) and names that carry a
syntactic signal: a title ("Dr."), a label ("Patient:"), a "Surname, Given"
comma form, or an ALL-CAPS run. It CANNOT match an arbitrary bare mixed-case
name with no such signal ("John Smith presented…") without also destroying
ordinary noun phrases, so that case is out of scope here. The fail-closed second
pass therefore does not make the system safe against names it cannot
pattern-match — that gap is closed by running input through a dedicated PHI NER
(e.g. Presidio/scrubadub) at this ``deidentify`` seam plus the human-in-the-loop
review the spec requires.
"""

from __future__ import annotations

import re
from functools import lru_cache

_REDACTED = "[REDACTED]"


class PHIDetectedError(Exception):
    """Raised when identifiable PHI survives de-identification."""


# Generic, domain-agnostic ALL-CAPS tokens that are never person names. Domain
# clinical abbreviations are NOT hardcoded here — they are supplied per-call via
# ``caps_allowlist`` from the active vertical config, so this module stays
# domain-neutral (spec §2 boundary rule).
_BASE_CAPS_ALLOWLIST = (
    "DOB", "MRN", "SSN", "ID", "PHI", "HIPAA", "USA", "US", "UK", "DNA", "RNA",
)

# ALL-CAPS tokens that are structurally never surnames: roman numerals and the
# stage codes built from them. These never count as a "name token" when deciding
# whether an ALL-CAPS run is a person name, so clinical phrases such as
# "STAGE IV" or "GRADE III" are not mistaken for a name.
_NON_NAME_CAPS = frozenset({
    "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII",
    "IA", "IB", "IC", "IIA", "IIB", "IIC",
    "IIIA", "IIIB", "IIIC", "IVA", "IVB", "IVC",
})

# Each rule: (label, compiled pattern). Order matters — most specific first.
_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),

    # SSN: dash-delimited, space-delimited, in SSN/Social-Security context, OR a
    # bare 9-digit run. The bare-9-digit branch closes the leak where an
    # intervening word ("SSN is 123456789") detached the keyword from the digits;
    # over-redacting an unrelated 9-digit number is the safe (fail-closed) side.
    ("SSN", re.compile(
        r"\b\d{3}-\d{2}-\d{4}\b"
        r"|\b\d{3}\s\d{2}\s\d{4}\b"
        r"|(?:SSN|Social\s+Security(?:\s+Number)?)\s*[:#]?\s*\d{9}\b"
        r"|\b\d{9}\b",
        re.I,
    )),

    ("PHONE", re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")),

    # MRN — extended prefix set.
    ("MRN", re.compile(
        r"\b(?:MRN|MR#|medical record(?:\s*(?:no|number|#))?|"
        r"Patient\s+ID|Patient\s+number|Chart\s+#|Record\s+#|Acct)"
        r"\s*[:#]?\s*\d{4,}\b",
        re.I,
    )),

    # Explicit DOB label.
    ("DOB", re.compile(r"\b(?:DOB|date of birth)\s*[:#]?\s*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", re.I)),

    # Spelled-out prose dates: "January 5, 1962" / "Mar 1942" / "March 15 2001".
    ("DATE_PROSE", re.compile(
        r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
        r"\.?\s+\d{1,2}(?:st|nd|rd|th)?[,\s]+\d{4}\b"
        r"|\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
        r"\.?\s+\d{4}\b",
        re.I,
    )),

    # Bare calendar dates — MM/DD/YYYY, MM-DD-YYYY, and ISO 8601 YYYY-MM-DD / YYYY/MM/DD.
    # Bare 4-digit years alone are NOT redacted (HIPAA Safe Harbor exempts year-only).
    ("DATE", re.compile(
        r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"
        r"|\b\d{4}[/-]\d{1,2}[/-]\d{1,2}\b"
    )),

    # Named persons introduced by a title.
    ("NAME", re.compile(r"\b(?:Mr|Mrs|Ms|Dr|Mx|Prof)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?")),

    # Untitled names after explicit label keywords (Patient:, Name:, etc.). The
    # name group is one OR MORE capitalized words, so a single labeled given name
    # ("Patient: John") is caught, not only multi-word names.
    ("NAME_BARE", re.compile(
        r"\b(?:Patient|Name|Pt|Client|Resident)\s*:\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b",
        re.I,
    )),

    # "Surname, Given" / "SURNAME, Given" dictation form ("Smith, John";
    # "SMITH, John M"). The given name must be Title-case so clinical pairs like
    # "Stage, IV" (IV is not Title-case) do not match.
    ("NAME_COMMA", re.compile(
        r"\b[A-Z][A-Za-z]+,\s+[A-Z][a-z]+(?:\s+[A-Z]\.?)?\b"
    )),

    # NAME_CAPS (ALL-CAPS person names) is handled separately by
    # ``_redact_caps_names`` because the decision is per-token against the
    # vertical allowlist, not a single static pattern.

    # Street address — case-insensitive.
    ("ADDRESS", re.compile(
        r"\b\d{1,5}\s+[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*\s+"
        r"(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Lane|Ln|Drive|Dr|Court|Ct)\b",
        re.I,
    )),

    # Ages over 89 — HIPAA Safe Harbor requires aggregating 90+.
    # Matches: 90yo, 95 y/o, 100-year-old, 102 years old, 91year old, etc.
    ("AGE90", re.compile(
        r"\b(?:9[0-9]|1\d{2})[-\s]?(?:yo|y/o|year[-\s]?old|years[-\s]?old)\b",
        re.I,
    )),
]

# Hard-stop labels: if one still matches on the second pass, the first pass
# missed real PHI and we abort. Bare DATE/ADDRESS/AGE90 are scrubbed but not
# hard-stop triggers (the first pass removes them; a residual is low-confidence).
# ALL-CAPS names are enforced separately via ``_residual_caps_name``.
_HARD_STOP = {"EMAIL", "SSN", "PHONE", "MRN", "DOB", "NAME", "NAME_BARE", "NAME_COMMA", "DATE", "DATE_PROSE"}

# Any run of two or more consecutive ALL-CAPS words of 2+ letters.
_CAPS_RUN_RE = re.compile(r"(?<!\w)[A-Z]{2,}(?:\s+[A-Z]{2,})+(?!\w)")


@lru_cache(maxsize=None)
def _allow_set(allowlist: tuple[str, ...]) -> frozenset[str]:
    return frozenset(t.upper() for t in (_BASE_CAPS_ALLOWLIST + allowlist))


def _is_name_token(token: str, allow: frozenset[str]) -> bool:
    """A token looks like a person-name token (surname/given) when it is not an
    allowlisted clinical/generic abbreviation and not a roman numeral / stage
    code."""
    up = token.upper()
    if up in allow or up in _NON_NAME_CAPS:
        return False
    return token.isalpha()


def _run_is_name(run: str, allow: frozenset[str]) -> bool:
    """An ALL-CAPS run is treated as a person name when it contains at least one
    genuine name token. This catches "JOHN SMITH" and the collision case
    "AB CDEF" (AB is allowlisted, CDEF is not) while leaving all-allowlisted runs
    and roman-suffixed runs (e.g. an allowlisted code followed by "IV") intact —
    provided the abbreviations are supplied in the per-vertical allowlist."""
    return any(_is_name_token(t, allow) for t in run.split())


def _redact_caps_names(text: str, allow: frozenset[str]) -> tuple[str, int]:
    """Replace ALL-CAPS person-name runs with the redaction token. Returns
    (redacted_text, count_redacted)."""
    count = 0

    def _sub(m: re.Match[str]) -> str:
        nonlocal count
        if _run_is_name(m.group(0), allow):
            count += 1
            return _REDACTED
        return m.group(0)

    return _CAPS_RUN_RE.sub(_sub, text), count


def _residual_caps_name(text: str, allow: frozenset[str]) -> bool:
    """True if any ALL-CAPS person-name run survives in ``text`` (used by the
    fail-closed second pass)."""
    return any(_run_is_name(m.group(0), allow) for m in _CAPS_RUN_RE.finditer(text))


def scrub(text: str, caps_allowlist: tuple[str, ...] = ()) -> tuple[str, list[str]]:
    redactions: list[str] = []
    out = text
    for label, pattern in _RULES:
        def _sub(m: re.Match[str], _label: str = label) -> str:
            redactions.append(_label)
            return _REDACTED
        out = pattern.sub(_sub, out)

    out, caps_count = _redact_caps_names(out, _allow_set(tuple(caps_allowlist)))
    redactions.extend(["NAME_CAPS"] * caps_count)
    return out, redactions


def deidentify(text: str, caps_allowlist: tuple[str, ...] = ()) -> tuple[str, list[str]]:
    """Return (redacted_text, redaction_labels). Raises on residual PHI.

    ``caps_allowlist`` is the active vertical's domain abbreviations (so the
    generic ALL-CAPS name detector does not redact clinical acronyms).
    """
    allow = tuple(caps_allowlist)
    redacted, redactions = scrub(text, allow)
    # Second pass over the redacted text: any hard-stop pattern still matching
    # means our first pass missed real PHI (e.g. a scrub bug). This guards the
    # structured identifiers and signalled names in ``_HARD_STOP``; see the
    # module docstring for the bare-name case this cannot, by construction, cover.
    for label, pattern in _RULES:
        if label in _HARD_STOP and pattern.search(redacted):
            raise PHIDetectedError(
                f"Residual PHI of type {label} survived de-identification"
            )
    if _residual_caps_name(redacted, _allow_set(allow)):
        raise PHIDetectedError(
            "Residual PHI of type NAME_CAPS survived de-identification"
        )
    return redacted, redactions
