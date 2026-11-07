"""Regression tests for defects found in the pre-push review."""
import pytest

from core.sr.config import _valid_measure
from core.sr.driver.plan import error_hint
from core.secrets import read_secure_env


# --- error_hint must not crash on an empty/None error_code (HIGH: broke `ese status`) ---

def test_error_hint_empty_and_none_return_blank():
    assert error_hint("") == ""
    assert error_hint(None) == ""
    assert error_hint("   ") == ""


def test_error_hint_known_code_returns_hint():
    assert "ANTHROPIC_API_KEY" in error_hint("MissingKeyError")
    assert "ANTHROPIC_API_KEY" in error_hint("MissingKeyError http=401")  # first token
    assert error_hint("SomethingElse") == ""


# --- shared hardened secrets reader ---

def _write(path, text, mode=0o600):
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)
    return path


def test_read_secure_env_reads_0600_and_strips_quotes(tmp_path):
    f = _write(tmp_path / "providers.env",
               "# a comment\nWOS_STARTER_KEY=\"abc123\"\nSCOPUS_API_KEY=plain\n")
    assert read_secure_env("WOS_STARTER_KEY", f) == "abc123"   # quotes stripped
    assert read_secure_env("SCOPUS_API_KEY", f) == "plain"
    assert read_secure_env("MISSING", f) is None


def test_read_secure_env_refuses_world_readable(tmp_path):
    f = _write(tmp_path / "providers.env", "WOS_STARTER_KEY=x\n", mode=0o644)
    with pytest.raises(RuntimeError, match="permissions too open"):
        read_secure_env("WOS_STARTER_KEY", f)


def test_read_secure_env_refuses_symlink(tmp_path):
    real = _write(tmp_path / "real.env", "WOS_STARTER_KEY=x\n")
    link = tmp_path / "providers.env"
    link.symlink_to(real)
    with pytest.raises(RuntimeError, match="symlink"):
        read_secure_env("WOS_STARTER_KEY", link)


def test_read_secure_env_missing_file_returns_none(tmp_path):
    assert read_secure_env("WOS_STARTER_KEY", tmp_path / "nope.env") is None


def test_read_secure_env_invalid_utf8_does_not_mask_as_ebadf(tmp_path):
    # A read error (invalid UTF-8) must surface as a decode error, not OSError EBADF
    # from a double-close of the file descriptor.
    f = tmp_path / "providers.env"
    f.write_bytes(b"WOS_STARTER_KEY=\xff\xfe\n")
    f.chmod(0o600)
    with pytest.raises(UnicodeDecodeError):
        read_secure_env("WOS_STARTER_KEY", f)


# --- continuous effect-measure validation (LOW: bad value silently dropped an outcome) ---

def test_valid_measure_accepts_known_and_rejects_typo():
    assert _valid_measure("MD", "bw") == "MD"
    assert _valid_measure("smd", "ef") == "SMD"      # normalized
    with pytest.raises(ValueError, match="must be one of"):
        _valid_measure("MDD", "bw")
