"""Fetch and rendering safety.

Social posts are untrusted data. This module keeps retrieval inside approved
public hosts, blocks SSRF-style targets, caps response size, and makes CSV
output safe to open in a spreadsheet.
"""

from __future__ import annotations

import csv
import ipaddress
import json
import re
import socket
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        "t.me",
        "telegram.me",
        "www.youtube.com",
        "youtube.com",
        "m.youtube.com",
        "youtu.be",
        "i.ytimg.com",
        "yt3.ggpht.com",
        # Mastodon instances whose public tag timelines are readable without
        # an account. Keep in step with Budgets.mastodon_instances.
        "mastodon.social",
        "mastodon.online",
        "fosstodon.org",
        "mstdn.social",
        # Reddit: free OAuth app; token endpoint answers, public JSON is
        # bot-blocked, so the OAuth host is the only usable surface.
        "www.reddit.com",
        "oauth.reddit.com",
        # Free, keyless news indexes.
        "news.google.com",
        "api.gdeltproject.org",
    }
)

_BLOCKED_NETWORKS = [
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
]

_FORMULA_PREFIX = re.compile(r"^[=+\-@\t\r]")


class UnsafeURLError(ValueError):
    pass


def _resolve(host: str) -> list[str]:
    """Resolve a hostname to addresses. Fails closed: an error is an empty list."""
    try:
        return [info[4][0] for info in socket.getaddrinfo(host, None)]
    except socket.gaierror:
        return []


def _is_private(host: str, resolver: Callable[[str], list[str]] = _resolve) -> bool:
    """True if the host is a literal private address or resolves to one.

    Resolution failure is treated as private. That is deliberately fail-closed:
    an SSRF guard that permits a request when it cannot determine the destination
    is not a guard.
    """
    addresses = resolver(host)
    if not addresses:
        return True
    for addr in addresses:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return True
        if any(ip in net for net in _BLOCKED_NETWORKS):
            return True
    return False


def validate_public_url(
    url: str,
    *,
    allowed_hosts: Iterable[str] | None = ALLOWED_HOSTS,
    resolver: Callable[[str], list[str]] = _resolve,
) -> str:
    """Raise unless *url* is an allowed http(s) host that is not private.

    ``resolver`` is injectable so the allowlist logic can be tested without a
    network. The default does a real DNS lookup, which is what production uses.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeURLError(f"unsupported scheme: {parsed.scheme!r}")
    host = (parsed.hostname or "").lower()
    if not host:
        raise UnsafeURLError("missing host")
    if allowed_hosts is not None and host not in {h.lower() for h in allowed_hosts}:
        raise UnsafeURLError(f"host not allowed: {host}")
    if _is_private(host, resolver):
        raise UnsafeURLError(f"host resolves to a private address: {host}")
    return url


@dataclass(frozen=True)
class FetchResult:
    """Decoded response from an approved host.

    Deliberately not an ``httpx.Response``: reconstructing one from already
    decoded bytes makes httpx re-apply the ``Content-Encoding`` header, which
    raises a decompression error. Callers only need status, text and URL.
    """

    status_code: int
    text: str
    url: str
    headers: dict[str, str]

    def json(self) -> object:
        """Decode the body as JSON, raising ``ValueError`` on malformed input.

        Mirrors ``httpx.Response.json`` so callers do not have to know this is a
        value object rather than a real response.
        """
        return json.loads(self.text)


def safe_get(
    url: str,
    *,
    timeout: float = 25.0,
    max_bytes: int = 5_000_000,
    user_agent: str,
    allowed_hosts: Iterable[str] | None = ALLOWED_HOSTS,
) -> FetchResult:
    """GET a validated public URL, refusing oversized bodies."""
    return safe_request(
        "GET",
        url,
        timeout=timeout,
        max_bytes=max_bytes,
        user_agent=user_agent,
        allowed_hosts=allowed_hosts,
    )


def safe_request(
    method: str,
    url: str,
    *,
    timeout: float = 25.0,
    max_bytes: int = 5_000_000,
    user_agent: str,
    allowed_hosts: Iterable[str] | None = ALLOWED_HOSTS,
    headers: Mapping[str, str] | None = None,
    data: Mapping[str, str] | None = None,
    auth: tuple[str, str] | None = None,
) -> FetchResult:
    """Validated request on an approved host, refusing oversized bodies.

    ``safe_get`` is the read-only case. The extra parameters exist for OAuth
    token exchange (Reddit), which needs a POST and a basic-auth header -- the
    host allowlist and the private-address check apply identically either way,
    so authentication must not mean bypassing them.
    """
    validate_public_url(url, allowed_hosts=allowed_hosts)
    request_headers = {
        "User-Agent": user_agent,
        "Accept-Language": "en,*;q=0.5",
        **(headers or {}),
    }
    with httpx.stream(
        method.upper(),
        url,
        timeout=timeout,
        follow_redirects=True,
        headers=request_headers,
        data=dict(data) if data else None,
        auth=auth,
    ) as response:
        # Re-validate after redirects.
        if response.url.host:
            validate_public_url(str(response.url), allowed_hosts=allowed_hosts)
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > max_bytes:
                raise UnsafeURLError(f"response exceeded {max_bytes} bytes")
            chunks.append(chunk)
        body = b"".join(chunks)
        encoding = response.charset_encoding or "utf-8"
        return FetchResult(
            status_code=response.status_code,
            text=body.decode(encoding, errors="replace"),
            url=str(response.url),
            headers={k.lower(): v for k, v in response.headers.items()},
        )


def csv_safe(value: object) -> str:
    """Neutralise spreadsheet formula injection while preserving readable text."""
    if value is None:
        return ""
    text = str(value)
    if _FORMULA_PREFIX.match(text):
        return "'" + text
    return text


def write_csv(path, fieldnames: list[str], rows: Iterable[Mapping[str, object]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: csv_safe(row.get(k)) for k in fieldnames})
