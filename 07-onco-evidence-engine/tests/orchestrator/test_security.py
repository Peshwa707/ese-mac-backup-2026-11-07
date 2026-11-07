"""WP-M2.4 — security hardening (SSRF, scrub_exception, data-egress governance)."""
import pytest

from core.sr.orchestrator import security as sec
from core.sr.orchestrator.security import EgressDenied, SSRFError


# ---- SSRF ----
def test_loopback_endpoint_allowed():
    sec.validate_endpoint("http://localhost:11434")
    sec.validate_endpoint("http://127.0.0.1:11434/")


def test_metadata_endpoint_blocked():
    with pytest.raises(SSRFError, match="blocked address"):
        sec.validate_endpoint("http://169.254.169.254/latest/meta-data/")


def test_link_local_blocked():
    with pytest.raises(SSRFError, match="blocked address"):
        sec.validate_endpoint("http://169.254.10.5:8080")


def test_private_endpoint_requires_allow_remote():
    with pytest.raises(SSRFError, match="allow_remote"):
        sec.validate_endpoint("http://10.0.0.5:11434")


def test_remote_endpoint_must_be_https_even_with_allow_remote():
    with pytest.raises(SSRFError, match="https"):
        sec.validate_endpoint("http://8.8.8.8:11434", allow_remote=True)   # http -> rejected
    sec.validate_endpoint("https://8.8.8.8:11434", allow_remote=True)      # public https -> ok


def test_private_address_refused_even_with_allow_remote():
    # internal SSRF: a private/RFC1918 host must be refused even with allow_remote+https
    with pytest.raises(SSRFError, match="non-public"):
        sec.validate_endpoint("https://10.0.0.5:11434", allow_remote=True)


def test_ipv4_mapped_ipv6_metadata_blocked():
    with pytest.raises(SSRFError, match="blocked address"):
        sec.validate_endpoint("http://[::ffff:169.254.169.254]/latest/meta-data/")


def test_bad_scheme_rejected():
    with pytest.raises(SSRFError, match="scheme"):
        sec.validate_endpoint("ftp://localhost")


# ---- scrub_exception ----
def test_scrub_exception_strips_message():
    exc = RuntimeError("boom secret sk-DEADBEEF0123456789 payload")
    out = sec.scrub_exception(exc)
    assert out == "RuntimeError"
    assert "sk-DEADBEEF" not in out and "boom" not in out


def test_scrub_exception_includes_http_status():
    class _Resp:
        status_code = 401

    class _Err(Exception):
        response = _Resp()

    assert sec.scrub_exception(_Err("auth failed for key sk-x")) == "_Err http=401"


# ---- egress governance ----
def test_local_allows_all_content_classes():
    for cc in ("metadata", "abstract", "fulltext", "manuscript"):
        sec.assert_egress_allowed("local", cc)


def test_cloud_forbids_fulltext_by_default():
    sec.assert_egress_allowed("anthropic", "abstract")           # ok
    with pytest.raises(EgressDenied, match="forbids"):
        sec.assert_egress_allowed("anthropic", "fulltext")
    with pytest.raises(EgressDenied):
        sec.assert_egress_allowed("anthropic", "manuscript")


def test_unknown_provider_fails_closed():
    with pytest.raises(EgressDenied, match="no data policy"):
        sec.assert_egress_allowed("whoknows", "abstract")


def test_unknown_content_class_rejected():
    with pytest.raises(ValueError):
        sec.assert_egress_allowed("local", "bogus")


# ---- gateway wiring: local endpoint is SSRF-validated ----
def test_gateway_local_blocks_metadata_endpoint(monkeypatch):
    from core.llm import gateway
    from core.llm.gateway import ProviderSpec
    from pydantic import BaseModel

    class _S(BaseModel):
        ok: bool

    with pytest.raises(SSRFError):
        gateway.gateway_parse(_S, "s", "u", stage="screening",
                              spec=ProviderSpec(source="local", model="llama3",
                                                endpoint="http://169.254.169.254"))


def test_gateway_blocks_fulltext_to_cloud_before_egress(monkeypatch):
    from core.llm import client, gateway
    from core.llm.gateway import ProviderSpec
    from pydantic import BaseModel

    class _S(BaseModel):
        ok: bool

    # egress check happens before key/SDK use -> EgressDenied even with no key
    called = {"n": 0}
    monkeypatch.setattr(client, "_anthropic_parse",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or _S(ok=True))
    with pytest.raises(EgressDenied):
        gateway.gateway_parse(_S, "s", "u", stage="extraction",
                              spec=ProviderSpec(source="anthropic", model="claude-opus-4-8"),
                              content_class="fulltext")
    assert called["n"] == 0                       # never reached egress


def test_gateway_allows_abstract_to_cloud(monkeypatch):
    from core.llm import client, gateway
    from core.llm.gateway import ProviderSpec
    from pydantic import BaseModel

    class _S(BaseModel):
        ok: bool

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(client, "_anthropic_parse", lambda *a, **k: _S(ok=True))
    out = gateway.gateway_parse(_S, "s", "u", stage="screening",
                                spec=ProviderSpec(source="anthropic", model="claude-opus-4-8"),
                                content_class="abstract")
    assert out.ok


def test_gateway_allows_fulltext_to_local(monkeypatch):
    from core.llm import client, gateway
    from core.llm.gateway import ProviderSpec
    from pydantic import BaseModel

    class _S(BaseModel):
        ok: bool

    monkeypatch.setattr(client, "_ollama_parse", lambda *a, **k: _S(ok=True))
    out = gateway.gateway_parse(_S, "s", "u", stage="extraction",
                                spec=ProviderSpec(source="local", model="llama3",
                                                  endpoint="http://localhost:11434"),
                                content_class="fulltext")
    assert out.ok                                 # local policy allows full text
