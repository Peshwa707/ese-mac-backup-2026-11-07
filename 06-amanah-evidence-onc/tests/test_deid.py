import pytest

from core.grounding.deid import PHIDetectedError, deidentify, scrub


def test_redacts_identifiers(case_with_phi):
    redacted, labels = deidentify(case_with_phi)
    assert "John Smith" not in redacted
    assert "john.smith@example.com" not in redacted
    assert "4456789" not in redacted
    assert "[REDACTED]" in redacted
    assert {"NAME", "EMAIL", "MRN"}.issubset(set(labels))


def test_clinical_content_survives(case_with_phi):
    redacted, _ = deidentify(case_with_phi)
    assert "lung adenocarcinoma" in redacted


def test_clean_case_passes(deid_case_clean):
    redacted, labels = deidentify(deid_case_clean)
    assert redacted  # no exception


def test_residual_phi_hard_stops(monkeypatch):
    # Simulate a scrubber that misses an email by monkeypatching scrub to a no-op.
    import core.grounding.deid as deid_mod

    monkeypatch.setattr(deid_mod, "scrub", lambda text, caps_allowlist=(): (text, []))
    with pytest.raises(PHIDetectedError):
        deidentify("contact me at leak@example.com")


# ---------------------------------------------------------------------------
# Parametrized: each PHI type must be redacted
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("phi_type,text,absent", [
    # SSN variants
    ("SSN-dash",    "Patient SSN: 123-45-6789",          "123-45-6789"),
    ("SSN-space",   "SSN 123 45 6789 on file",           "123 45 6789"),
    ("SSN-labeled-undelim", "SSN: 123456789",            "123456789"),
    # PHONE
    ("PHONE",       "call 555-867-5309 for results",     "555-867-5309"),
    # DOB
    ("DOB",         "DOB: 03/04/1962",                   "03/04/1962"),
    # AGE90 variants
    ("AGE90-space", "a 92 year old female",              "92 year old"),
    ("AGE90-hyphen","a 95-year-old male",                "95-year-old"),
    ("AGE90-yo",    "91yo male",                         "91yo"),
    ("AGE90-y/o",   "93 y/o with NSCLC",                "93 y/o"),
    ("AGE90-100",   "a 100 year-old survivor",           "100 year-old"),
    # ADDRESS
    ("ADDRESS-mixed","123 Main Street in Springfield",   "123 Main Street"),
    ("ADDRESS-lower","123 main street",                  "123 main street"),
    # ISO 8601 date
    ("DATE-ISO",    "admitted 2024-03-15",               "2024-03-15"),
    ("DATE-ISO-slash","2024/03/15",                      "2024/03/15"),
    # Prose date
    ("DATE-PROSE-full","born January 5, 1962",           "January 5, 1962"),
    ("DATE-PROSE-mon-yr","diagnosed Mar 1942",           "Mar 1942"),
    # Untitled name after label
    ("NAME-bare",   "Patient: Jane Doe",                 "Jane Doe"),
    ("NAME-bare-name-kw", "Name: John Smith",            "John Smith"),
    # All-caps name
    ("NAME-caps",   "JOHN SMITH was admitted",           "JOHN SMITH"),
    ("NAME-caps-2", "patient EMILY JONES reviewed",      "EMILY JONES"),
])
def test_phi_type_redacted(phi_type, text, absent):
    redacted, _ = deidentify(text)
    assert absent not in redacted, (
        f"[{phi_type}] Expected {absent!r} to be redacted in {text!r}, "
        f"but got: {redacted!r}"
    )


# ---------------------------------------------------------------------------
# Partial-leak test: monkeypatched scrub leaves residual PHI
# ---------------------------------------------------------------------------

def test_partial_leak_raises_phi_detected_error(monkeypatch):
    """scrub that strips names but leaves an SSN triggers the second-pass guard."""
    import core.grounding.deid as deid_mod

    def _leaky_scrub(text: str, caps_allowlist=()):
        # Remove name patterns but intentionally leave SSN intact.
        import re
        cleaned = re.sub(r"\b(?:Mr|Mrs|Ms|Dr|Mx|Prof)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?", "[REDACTED]", text)
        return cleaned, ["NAME"]

    monkeypatch.setattr(deid_mod, "scrub", _leaky_scrub)
    with pytest.raises(PHIDetectedError) as exc_info:
        deidentify("Dr. Jane Doe SSN 123-45-6789 called")
    assert "SSN" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Regression: leaks/over-redaction found in code review (2026-06-04)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("phi_type,text,absent", [
    # SSN keyword detached from the digits by an intervening word.
    ("SSN-intervening-word", "Patient SSN is 123456789.", "123456789"),
    # Bare 9-digit SSN with no keyword at all.
    ("SSN-bare-9digit",      "the number 123456789 was on file", "123456789"),
    # Single given name after a label (not only multi-word names).
    ("NAME-single-labeled",  "Patient: John",                "John"),
    ("NAME-single-pt",       "Pt: Maria",                    "Maria"),
    # "Surname, Given" dictation form.
    ("NAME-comma-allcaps",   "SMITH, John presented",        "SMITH, John"),
    ("NAME-comma-titlecase", "seen by Doe, Jane today",      "Doe, Jane"),
])
def test_review_leak_now_redacted(phi_type, text, absent):
    redacted, _ = deidentify(text)
    assert absent not in redacted, (
        f"[{phi_type}] expected {absent!r} redacted in {text!r}, got {redacted!r}"
    )


def test_caps_name_allowlist_collision_is_redacted():
    """ALL-CAPS surname whose run starts with an allowlisted abbreviation (e.g.
    'OS BORG' — OS is the oncology 'overall survival' abbreviation) must still be
    redacted: the allowlist exempts a run only when EVERY token is allowlisted."""
    from core.config.vertical import load_vertical

    allow = load_vertical("oncology").deid_caps_allowlist
    for name in ("OS BORG", "CA LASETER", "OS WELLS"):
        redacted, _ = deidentify(f"{name} admitted overnight", allow)
        surname = name.split()[1]
        assert surname not in redacted, f"{name!r} leaked surname: {redacted!r}"


@pytest.mark.parametrize("phrase", [
    "STAGE IV NSCLC",
    "PARTIAL RESPONSE",
    "TUMOR BOARD",
    "CT CHEST",
    "COMPLETE REMISSION",
    "STABLE DISEASE",
])
def test_allcaps_clinical_phrases_survive_with_vertical_allowlist(phrase):
    """Common ALL-CAPS clinical phrases must NOT be redacted as person names when
    the oncology caps-allowlist is supplied (over-redaction regression)."""
    from core.config.vertical import load_vertical

    allow = load_vertical("oncology").deid_caps_allowlist
    redacted, _ = deidentify(f"Assessment: {phrase} noted on review.", allow)
    assert phrase in redacted, f"{phrase!r} wrongly redacted: {redacted!r}"


def test_allcaps_real_name_still_redacted_with_vertical_allowlist():
    from core.config.vertical import load_vertical

    allow = load_vertical("oncology").deid_caps_allowlist
    redacted, _ = deidentify("JOHN SMITH and EMILY JONES were seen.", allow)
    assert "JOHN SMITH" not in redacted and "EMILY JONES" not in redacted


# ---------------------------------------------------------------------------
# Clinical content must survive de-identification unredacted
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("clinical_term", [
    "Stage IV",
    "ECOG 1",
    "EGFR exon 19",
    "osimertinib",
    "2023",            # bare year — NOT a HIPAA date identifier
    "NSCLC",
    "DLBCL",
    "AML",
    "NCCN",
    "ASCO",
    "ESMO",
    "RCT",
])
def test_clinical_content_not_redacted(clinical_term):
    """Clinical terms and bare years must survive de-identification unchanged."""
    # Wrap in realistic clinical prose so there is surrounding context.
    text = f"Patient was treated per {clinical_term} guidelines." if clinical_term == clinical_term else clinical_term
    text = f"Diagnosis: {clinical_term} with standard workup."
    redacted, _ = deidentify(text)
    assert clinical_term in redacted, (
        f"Clinical term {clinical_term!r} was incorrectly redacted. Result: {redacted!r}"
    )
