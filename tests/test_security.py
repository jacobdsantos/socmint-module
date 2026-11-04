from __future__ import annotations

import pytest

from socmint import security


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "http://localhost/secret",
        "http://127.0.0.1:8080/",
        "http://169.254.169.254/latest/meta-data/",
        "https://example.com/not-allowed-host",
    ],
)
def test_unsafe_urls_rejected(url):
    with pytest.raises(security.UnsafeURLError):
        security.validate_public_url(url)


def _public_resolver(_host: str) -> list[str]:
    return ["93.184.216.34"]


def _private_resolver(_host: str) -> list[str]:
    return ["10.0.0.7"]


def _failing_resolver(_host: str) -> list[str]:
    return []


def test_allowed_host_accepted():
    """The allowlist logic is tested offline; resolution is injected."""
    assert security.validate_public_url("https://t.me/s/example", resolver=_public_resolver)


def test_allowed_host_resolving_to_private_address_is_rejected():
    """An allowlisted hostname that resolves inside the network is still SSRF."""
    with pytest.raises(security.UnsafeURLError, match="private address"):
        security.validate_public_url("https://t.me/s/example", resolver=_private_resolver)


def test_resolution_failure_fails_closed():
    """An SSRF guard that permits the request when DNS fails is not a guard."""
    with pytest.raises(security.UnsafeURLError, match="private address"):
        security.validate_public_url("https://t.me/s/example", resolver=_failing_resolver)


def test_csv_formula_injection_neutralised():
    assert security.csv_safe("=cmd|' /C calc'!A0").startswith("'")
    assert security.csv_safe("+1+1").startswith("'")
    assert security.csv_safe("@SUM(A1)").startswith("'")
    assert security.csv_safe("normal text") == "normal text"
    assert security.csv_safe(None) == ""


def test_write_csv_escapes_formula(tmp_path):
    path = tmp_path / "out.csv"
    security.write_csv(path, ["a"], [{"a": "=HYPERLINK(\"http://evil\")"}])
    content = path.read_text(encoding="utf-8")
    assert "'=HYPERLINK" in content
