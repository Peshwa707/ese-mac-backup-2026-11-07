"""Security hardening for the LLM gateway (WP-M2.4, spec §10).

Three fail-closed controls:
  * validate_endpoint  — SSRF guard for local/self-hosted model endpoints.
  * scrub_exception    — the ONLY string allowed into persisted error fields/logs
                         (typed code + http status; never a raw message/payload/key).
  * assert_egress_allowed — per-provider data-governance: don't send a content class
                         (e.g. paywalled full text / embargoed manuscript) to a provider
                         whose policy doesn't permit it.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

_METADATA_IPS = {ipaddress.ip_address("169.254.169.254"), ipaddress.ip_address("fd00:ec2::254")}


class SSRFError(RuntimeError):
    """An endpoint resolves to a blocked/unsafe address."""


class EgressDenied(RuntimeError):
    """A provider's data policy forbids sending this content class."""


def _resolve(host: str) -> list[str]:
    try:                                            # IP literal -> no DNS
        ipaddress.ip_address(host)
        return [host]
    except ValueError:
        pass
    infos = socket.getaddrinfo(host, None)
    return list({i[4][0] for i in infos})


def _norm(a: ipaddress._BaseAddress):
    """Collapse IPv4-mapped IPv6 (::ffff:169.254.169.254) to its IPv4 form so the
    blocklist can't be evaded via the mapped representation."""
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped is not None:
        return a.ipv4_mapped
    return a


def validate_endpoint(url: str, *, allow_remote: bool = False) -> None:
    """Fail closed unless the endpoint is loopback, or (with allow_remote) an https host
    that resolves ONLY to public addresses. Always rejects metadata/link-local/private/
    reserved/multicast/unspecified — so even allow_remote can't reach internal services.

    Caveat: this validates at call time; the connecting client re-resolves DNS, so this
    narrows but does not fully eliminate a rebinding TOCTOU — hence the public-only rule
    and the loopback default keep the blast radius off internal/metadata addresses."""
    p = urlsplit(url if "://" in url else "http://" + url)
    scheme = (p.scheme or "").lower()
    host = p.hostname or ""
    if scheme not in ("http", "https"):
        raise SSRFError(f"unsupported endpoint scheme {scheme!r}")
    if not host:
        raise SSRFError("endpoint has no host")
    addrs = [_norm(ipaddress.ip_address(ip)) for ip in _resolve(host)]
    if addrs and all(a.is_loopback for a in addrs):
        return                                      # loopback: any scheme ok (incl. ::1)
    for a in addrs:
        if (a.is_link_local or a.is_multicast or a.is_unspecified or a.is_reserved
                or a.is_loopback or a in _METADATA_IPS):
            raise SSRFError(f"endpoint resolves to a blocked address ({a})")
    if not allow_remote:
        raise SSRFError(f"non-loopback endpoint {host!r} requires allow_remote=True")
    if scheme != "https":
        raise SSRFError(f"non-loopback endpoint {host!r} must use https")
    for a in addrs:                                 # public-only even with allow_remote
        if not a.is_global:
            raise SSRFError(f"endpoint resolves to a non-public address ({a}); "
                            f"refused even with allow_remote")


def scrub_exception(exc: BaseException) -> str:
    """A safe, persistable error string: type + optional HTTP status only. Never the
    message/cause/payload (which can carry keys or clinical text)."""
    code = type(exc).__name__
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return f"{code} http={status}" if status is not None else code


# Conservative defaults: cloud providers may receive metadata/abstracts but NOT
# paywalled full text or embargoed manuscript unless a vertical declares a
# zero-retention policy. Local stays fully private.
_DEFAULT_POLICY: dict[str, dict] = {
    "local": {"allowed": {"metadata", "abstract", "fulltext", "manuscript"}},
    "anthropic": {"allowed": {"metadata", "abstract"}},
    "openai": {"allowed": {"metadata", "abstract"}},
    "gemini": {"allowed": {"metadata", "abstract"}},
}
_CONTENT_CLASSES = {"metadata", "abstract", "fulltext", "manuscript"}


def assert_egress_allowed(provider: str, content_class: str, *, policy: dict | None = None) -> None:
    if content_class not in _CONTENT_CLASSES:
        raise ValueError(f"unknown content class {content_class!r}")
    pol = (policy or _DEFAULT_POLICY).get(provider)
    if pol is None:
        raise EgressDenied(f"no data policy for provider {provider!r} (fail closed)")
    if content_class not in pol["allowed"]:
        raise EgressDenied(
            f"{provider} data policy forbids sending {content_class!r} content "
            f"(allowed: {sorted(pol['allowed'])})")
