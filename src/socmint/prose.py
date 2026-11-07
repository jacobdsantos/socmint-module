"""Prose helpers shared by every analyst-facing surface.

Both the dashboard and the published case file read from these, so a count is
phrased the same way in both places. The rule they exist to enforce: a reader
sees "3 posts", never "3 post(s)". The latter is a developer writing a format
string, and it is the most common way tool output announces itself as tool
output.

Not to be confused with :mod:`socmint.text`, which normalises post content.
"""

from __future__ import annotations

#: Display names for storage keys. The enum values are what the database holds;
#: these are what a reader sees. A count of one must not render as "1 URLs".
_KIND_NAMES: dict[str, tuple[str, str]] = {
    "domain": ("domain", "domains"),
    "url": ("URL", "URLs"),
    "md5": ("MD5 hash", "MD5 hashes"),
    "sha1": ("SHA1 hash", "SHA1 hashes"),
    "sha256": ("SHA256 hash", "SHA256 hashes"),
    "ip": ("IP address", "IP addresses"),
    "ipv4": ("IP address", "IP addresses"),
    "email": ("email address", "email addresses"),
    "telegram_handle": ("Telegram handle", "Telegram handles"),
    "wallet": ("wallet address", "wallet addresses"),
    "cve": ("CVE", "CVEs"),
}

_PLATFORM_NAMES: dict[str, str] = {
    "mastodon": "Mastodon",
    "telegram": "Telegram",
    "youtube": "YouTube",
    "google_news": "Google News",
    "x": "X",
    "gdelt": "GDELT",
    "reddit": "Reddit",
}


def kind_label(kind: str, count: int = 1) -> str:
    """Prose name for an indicator kind, agreeing with ``count``.

    Singular by default: a call that does not say how many is naming one value.
    """
    one, many = _KIND_NAMES.get(kind, (kind.replace("_", " "), kind.replace("_", " ") + "s"))
    return one if count == 1 else many


def humanise(key: str) -> str:
    """'shared_url' -> 'shared URL' — a storage key a reader can parse."""
    words = key.replace("_", " ").split()
    return " ".join(w.upper() if w in {"url", "ip", "cve", "md5", "sha1", "sha256"} else w for w in words)


def platform_label(name: str) -> str:
    """Proper noun for a platform key: 'google_news' -> 'Google News'."""
    return _PLATFORM_NAMES.get(name, name.replace("_", " ").title())


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    """'1 post', '3 posts', '1 episode', '2 episodes'.

    ``plural_form`` covers nouns that do not simply take an ``s`` (e.g.
    ``plural(2, "match", "matches")``).
    """
    if count == 1:
        return f"{count} {singular}"
    return f"{count} {plural_form or singular + 's'}"


def agreement(count: int, one: str, many: str) -> str:
    """Pick the verb form that agrees with ``count``: 'was' / 'were'.

    ``plural()`` fixes the noun; the verb after it has to follow, or the
    sentence reads "1 post were excluded".
    """
    return one if count == 1 else many


def listing(parts: list[str]) -> str:
    """'a, b and c' — an Oxford-free list reads better inside a sentence."""
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


__all__ = ["agreement", "humanise", "kind_label", "listing", "platform_label", "plural"]
