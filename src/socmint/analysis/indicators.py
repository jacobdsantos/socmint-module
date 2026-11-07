"""Indicator extraction from post text.

Pulls technical values out of social posts — domains, IPv4s, URLs, file hashes,
wallet addresses, Telegram handles and email addresses — and decides **what each
one is doing in the post**.

That second job matters as much as the first. A topic search returns posts that
*cite* things: the vendor advisory, the language documentation, the researcher's
blog. Those are references, not indicators. Listing `php.net` next to a
vulnerability write-up as though it were an IOC is wrong, and an analyst who
receives a list like that stops trusting the whole file.

Three rules govern this module:

1. **Classify before reporting.** Every value carries a role — ``indicator``,
   ``reference`` or ``unknown`` — with the reason it was assigned.
2. **Everything is defanged on output.** A report or export must never contain a
   live, clickable link. Defanging is applied at the model boundary.
3. **Extraction is not validation.** A role of ``indicator`` means "the post
   presents this as an artefact of compromise", not "this is malicious". No
   reputation, registration or hosting check is performed here.

The classifier is a heuristic over local evidence. It is deliberately explicit
about which rule fired so an analyst can overrule it.
"""

from __future__ import annotations

import ipaddress
import re
from collections import defaultdict

from ..models import Indicator, IndicatorKind, IndicatorRole, Post
from ..prose import plural

# ---------------------------------------------------------------------------
# Extraction patterns
# ---------------------------------------------------------------------------

_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b")
_DOMAIN_RE = re.compile(
    r"\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"(?:com|net|org|io|co|info|xyz|top|site|online|app|dev|me|ly|ru|cn|ir|ae|sa|ph|tr|tk|ml|ga|cf|gq|zip|mov)\b",
    re.IGNORECASE,
)
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_SHA256_RE = re.compile(r"\b[a-fA-F0-9]{64}\b")
_MD5_RE = re.compile(r"\b[a-fA-F0-9]{32}\b")
_TG_HANDLE_RE = re.compile(r"(?<![\w@])@([A-Za-z][A-Za-z0-9_]{4,31})\b")
_BTC_RE = re.compile(r"\b(?:bc1[a-z0-9]{25,62}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})\b")
_ETH_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")

# Values already defanged by the author (hxxp, [.], [@]). In threat-intel posting
# this is the single strongest signal that the author considers it hostile.
_DEFANGED_URL_RE = re.compile(r"hxxps?://[^\s<>\"')\]]+", re.IGNORECASE)
_DEFANGED_DOMAIN_RE = re.compile(
    r"\b(?:[a-z0-9-]+\[\.\])+[a-z]{2,}\b", re.IGNORECASE
)
_DEFANGED_HANDLE_RE = re.compile(r"\[@\]([A-Za-z][A-Za-z0-9_]{4,31})\b")

# ---------------------------------------------------------------------------
# Classification signals
# ---------------------------------------------------------------------------

# Domains that are infrastructure, not intelligence. A post linking to the
# platform it was posted on, to a creator's Patreon, or to a form has told the
# analyst nothing they can pivot on — and listing them as "references" still
# buries the real values. These are dropped at extraction, for both bare domains
# and URLs, before classification ever runs.
#
# Entries MUST be registrable forms ("ecr.aws", not "gallery.ecr.aws"), because
# both extraction paths reduce a host to its registrable domain before comparing.
_SERVICE_DOMAINS = frozenset(
    {
        # the platforms we collect from
        "youtube.com", "youtu.be", "ytimg.com", "t.me", "telegram.me",
        "twitter.com", "x.com", "facebook.com", "instagram.com", "tiktok.com",
        # creator / monetisation / community infrastructure
        "patreon.com", "buymeacoffee.com", "ko-fi.com", "paypal.me", "paypal.com",
        "discord.gg", "discord.com", "twitch.tv", "whatsapp.com", "wa.me",
        "forms.gle", "linktr.ee", "spotify.com", "ycombinator.com",
        "medium.com", "substack.com", "reddit.com", "quora.com", "linkedin.com",
        # generic hosting / infra where the domain is never the artefact
        "cloudflare.com", "wordpress.org", "wordpress.com", "amazonaws.com",
        "ecr.aws", "railway.com", "railway.app", "vercel.app", "netlify.app",
        "herokuapp.com", "azurewebsites.net", "googleusercontent.com",
    }
)

# Domains that are, structurally, sources rather than payloads. The registrable
# domain is what matters: a malicious file hosted on github.com is a malicious
# *file*, not a malicious domain, and the pivot is the URL or hash.
_REFERENCE_DOMAINS = frozenset(
    {
        # language / runtime / standards documentation
        "php.net", "python.org", "docs.python.org", "nodejs.org", "ruby-lang.org",
        "golang.org", "go.dev", "rust-lang.org", "java.com", "oracle.com",
        "developer.mozilla.org", "w3.org", "rfc-editor.org", "ietf.org",
        "kernel.org", "gnu.org", "apache.org", "nginx.org", "openssl.org",
        # security references
        "owasp.org", "cve.org", "nvd.nist.gov", "nist.gov", "cisa.gov",
        "mitre.org", "attack.mitre.org", "cwe.mitre.org", "first.org",
        "kb.cert.org", "cert.org", "sans.org", "isc.sans.edu",
        # code hosting and package registries
        "github.com", "gitlab.com", "bitbucket.org", "sourceforge.net",
        "pypi.org", "npmjs.com", "packagist.org", "rubygems.org",
        "crates.io", "nuget.org", "hub.docker.com", "docker.com",
        # vendors and research outlets that get cited constantly
        "microsoft.com", "learn.microsoft.com", "apple.com", "adobe.com",
        "cisco.com", "fortinet.com", "paloaltonetworks.com", "unit42.paloaltonetworks.com",
        "crowdstrike.com", "mandiant.com", "google.com", "cloud.google.com",
        "projectzero.google", "securelist.com", "kaspersky.com",
        "welivesecurity.com", "trendmicro.com", "sophos.com", "sentinelone.com",
        "huntress.com", "rapid7.com", "tenable.com", "qualys.com", "vulncheck.com",
        "bleepingcomputer.com", "thehackernews.com", "krebsonsecurity.com",
        "therecord.media", "cyberscoop.com", "darkreading.com", "securityweek.com",
        "hackerone.com", "bugcrowd.com", "exploit-db.com", "packetstormsecurity.com",
        # analysis / lookup services (people link to their own results)
        "virustotal.com", "urlscan.io", "shodan.io", "censys.io", "any.run",
        "hybrid-analysis.com", "app.any.run", "abuse.ch", "feodotracker.abuse.ch",
        # security vendors, education channels and industry statistics
        "aikido.io", "aikido.dev", "snyk.io", "socket.dev", "semgrep.dev",
        "liveoverflow.com", "hextree.io", "portswigger.net", "acunetix.com",
        "w3techs.com", "builtwith.com", "similarweb.com", "statista.com",
        "europa.eu", "enisa.europa.eu", "ncsc.gov.uk", "cyber.gov.au",
        "jvn.jp", "jpcert.or.jp", "cert-in.org.in",
        # encyclopedic / general
        "wikipedia.org", "archive.org", "web.archive.org", "stackoverflow.com",
        "stackexchange.com", "medium.com", "substack.com", "linkedin.com",
        "reddit.com", "quora.com", "youtube.com", "youtu.be", "twitter.com",
        "x.com", "facebook.com", "instagram.com", "tiktok.com", "t.me",
        "telegram.me", "discord.com", "bing.com", "duckduckgo.com",
        "play.google.com", "apps.apple.com", "bit.ly",
    }
)

# Paths that indicate an article, advisory, or documentation page rather than an
# endpoint someone would be sent to. `calif.io/blog/...` is a citation.
_ARTICLE_PATH = re.compile(
    r"/(blog|news|posts?|articles?|stories|insights?|research|advisories?|"
    r"threat-research|security|docs?|documentation|wiki|20\d\d)/",
    re.IGNORECASE,
)

# Vocabulary that marks the surrounding sentence as being about hostile artefacts.
_THREAT_CONTEXT = re.compile(
    r"\b(malicious|malware|c2|cnc|command[- ]and[- ]control|payload|dropper|"
    r"loader|beacon|exfiltrat\w*|phishing|smishing|backdoor|ransomware|"
    r"stealer|infostealer|keylogger|rootkit|botnet|rat|implant|"
    r"attacker|adversary|threat actor|exploited|exploitation|"
    r"blacklist|blocklist|blocked|takedown|sinkhol\w*|"
    r"indicator|ioc|iocs|hash|sample|artifact|artefact|"
    r"compromised|infection|infected|victim)\b",
    re.IGNORECASE,
)

# Vocabulary that marks a value as documentation or a download source.
# "verify" and "integrity" are deliberately absent: "verify your account at X" is
# a credential lure, and treating it as documentation flipped a phishing domain
# into a reference.
_BENIGN_CONTEXT = re.compile(
    r"\b(documentation|docs|manual|reference|read more|read the|source:|via|"
    r"according to|published|advisory|write-?up|blog post|article|report by|"
    r"checksum|sha256sum|release notes|changelog|download the tool)\b",
    re.IGNORECASE,
)

# Language that makes a nearby domain a lure rather than a citation.
#
# Deliberately narrow. "click here" was in the first version and it flagged a
# coaching site's YouTube description, because every marketing blurb says "click
# here to subscribe". A lure has to be about *credentials or money*, not merely
# about clicking.
_CREDENTIAL_LURE = re.compile(
    r"\b(verify your (account|identity|details|information|payment)|"
    r"enter your (password|credentials|otp|pin|card)|one[- ]time (code|password)|"
    r"re-?enter your (password|credentials)|"
    r"account (will be|has been) (closed|suspended|frozen|locked)|"
    r"deposits? (are|will be) frozen|"
    r"confirm your (identity|details|payment|bank)|"
    r"claim your (refund|prize|winnings)|"
    r"your (account|card) (is|has been) (compromised|suspended|locked))\b",
    re.IGNORECASE,
)

# Characters either side of a value to search for context. Tight on purpose:
# video descriptions and channel bios are long marketing text, and a wide window
# picks up "subscribe" and "buy my course" as if they were threat context.
_CONTEXT_WINDOW = 120


def defang(value: str) -> str:
    """Make an indicator safe to paste into a report or email."""
    out = value.replace("http://", "hxxp://").replace("https://", "hxxps://")
    out = out.replace(".", "[.]").replace("@", "[@]")
    return out


def _valid_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return not (ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_multicast)


def _registrable(domain: str) -> str:
    """Very small public-suffix approximation: keep the last two labels."""
    parts = domain.lower().strip(".").split(".")
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in {"co", "com", "org", "net", "gov", "ac"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _undefang(value: str) -> str:
    """Reverse the common defanging conventions back to a canonical value."""
    out = value.replace("hxxps://", "https://").replace("hxxp://", "http://")
    out = out.replace("[.]", ".").replace("(@)", "@").replace("[@]", "@")
    return out


def defanged_values(text: str) -> set[str]:
    """Values the author defanged themselves. Strong evidence of hostile intent."""
    found: set[str] = set()
    if not text:
        return found
    for url in _DEFANGED_URL_RE.findall(text):
        found.add(_undefang(url.rstrip(".,;:!?")))
    for domain in _DEFANGED_DOMAIN_RE.findall(text):
        found.add(_registrable(_undefang(domain)))
    for handle in _DEFANGED_HANDLE_RE.findall(text):
        found.add(f"@{handle}")
    return found


def _host_of(value: str) -> str:
    """Registrable host of a URL, or the value itself if it is already a domain."""
    if value.startswith(("http://", "https://")):
        rest = value.split("://", 1)[1]
        host = rest.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
        host = host.split("@")[-1].split(":")[0]
        return _registrable(host)
    return _registrable(value)


def _extract_raw(text: str) -> dict[IndicatorKind, set[str]]:
    found: dict[IndicatorKind, set[str]] = defaultdict(set)
    if not text:
        return found

    for url in _URL_RE.findall(text):
        cleaned = url.rstrip(".,;:!?")
        host = _host_of(cleaned)
        # A host without a dot is not a hostname. Text truncated mid-URL leaves
        # things like "https://www", which is a fragment of someone else's link,
        # not an indicator -- and it would otherwise outrank real values.
        if "." not in host:
            continue
        if host not in _SERVICE_DOMAINS:
            found[IndicatorKind.URL].add(cleaned)

    for email in _EMAIL_RE.findall(text):
        found[IndicatorKind.EMAIL].add(email.lower())

    for domain in _DOMAIN_RE.findall(text):
        base = _registrable(domain)
        if base not in _SERVICE_DOMAINS:
            found[IndicatorKind.DOMAIN].add(base)

    for candidate in _IPV4_RE.findall(text):
        if _valid_ip(candidate):
            found[IndicatorKind.IPV4].add(candidate)

    for digest in _SHA256_RE.findall(text):
        found[IndicatorKind.SHA256].add(digest.lower())
    for digest in _MD5_RE.findall(text):
        found[IndicatorKind.MD5].add(digest.lower())

    # Store the handle with its sigil so the defanged form reads as "[@]handle"
    # rather than a bare word that could be mistaken for prose.
    for handle in _TG_HANDLE_RE.findall(text):
        found[IndicatorKind.TELEGRAM_HANDLE].add(f"@{handle}")

    for wallet in _BTC_RE.findall(text):
        found[IndicatorKind.WALLET].add(wallet)
    for wallet in _ETH_RE.findall(text):
        found[IndicatorKind.WALLET].add(wallet)

    return found


def extract_from_text(text: str) -> dict[IndicatorKind, set[str]]:
    """Extract raw values from one block of text. Pure function.

    Runs twice: once on the text as written, and once on a normalised copy with
    ``hxxp`` / ``[.]`` / ``[@]`` reversed. The second pass is what catches values
    the author defanged — precisely the ones most likely to be hostile, and the
    ones a plain regex would silently miss because ``evil[.]com`` is not a domain.
    """
    found = _extract_raw(text)
    if not text:
        return found
    normalised = _undefang(text)
    if normalised != text:
        for kind, values in _extract_raw(normalised).items():
            found[kind] |= values
    return found


def _window(text: str, needle: str, width: int = _CONTEXT_WINDOW) -> str:
    """Text immediately around the first occurrence of *needle*."""
    index = text.find(needle)
    if index < 0:
        return ""
    return text[max(0, index - width) : index + len(needle) + width]


def classify(
    kind: IndicatorKind,
    value: str,
    text: str,
    *,
    defanged: set[str],
) -> tuple[IndicatorRole, str]:
    """Decide what a value is doing in a post, and record which rule decided.

    Order matters: the signals are checked strongest-first.
    """
    # 1. The author defanged it. Nothing beats the poster signalling hostility.
    if value in defanged:
        return IndicatorRole.INDICATOR, "author defanged it in the source text"

    # Hashes and wallets have almost no benign reading in a social post.
    if kind in {IndicatorKind.SHA256, IndicatorKind.MD5, IndicatorKind.WALLET}:
        return IndicatorRole.INDICATOR, "file hash or wallet address"

    if kind in {IndicatorKind.DOMAIN, IndicatorKind.URL}:
        host = _host_of(value)
        # A URL inherits its host's classification: if the author defanged the
        # domain, the full URL is equally hostile.
        if host in defanged:
            return IndicatorRole.INDICATOR, "host was defanged by the author in the source text"
        # 2. Structurally a source: documentation, registry, vendor, outlet.
        if host in _REFERENCE_DOMAINS:
            return IndicatorRole.REFERENCE, f"{host} is a known documentation or vendor domain"
        # 3. Looks like an article rather than an endpoint.
        if _ARTICLE_PATH.search(value):
            return IndicatorRole.REFERENCE, "URL path indicates an article or documentation page"
        # A bare domain inherits the role of the URL it appears in: if every
        # mention is inside an article-style link, the domain is being cited.
        if kind is IndicatorKind.DOMAIN and any(
            host in match.group(0) and _ARTICLE_PATH.search(match.group(0))
            for match in _URL_RE.finditer(text)
        ):
            return (
                IndicatorRole.REFERENCE,
                "only appears inside an article-style URL",
            )
        near = _window(text, value)
        # 4. A lure outranks benign attribution: "verify your account at X" is
        # the opposite of a citation, and an allowlisted name is no protection
        # against being abused in a credential-phishing sentence.
        if near and _CREDENTIAL_LURE.search(near):
            return IndicatorRole.INDICATOR, "appears in a credential-lure sentence"
        if near and _BENIGN_CONTEXT.search(near):
            return IndicatorRole.REFERENCE, "cited with documentation or attribution language"

    near = _window(text, value)
    if near and _THREAT_CONTEXT.search(near):
        return IndicatorRole.INDICATOR, "appears next to hostile-artefact language"

    if kind is IndicatorKind.EMAIL:
        return IndicatorRole.UNKNOWN, "email address with no supporting context"
    return IndicatorRole.UNKNOWN, "no signal either way in the post text"


_ROLE_PRIORITY = {
    IndicatorRole.INDICATOR: 3,
    IndicatorRole.UNKNOWN: 2,
    IndicatorRole.REFERENCE: 1,
}


def extract(posts: list[Post]) -> list[Indicator]:
    """Aggregate values across a corpus and classify each one."""
    registry: dict[tuple[IndicatorKind, str], Indicator] = {}

    for post in posts:
        haystack = " ".join(filter(None, [post.title, post.text]))
        if not haystack.strip():
            continue
        already_defanged = defanged_values(haystack)

        for kind, values in extract_from_text(haystack).items():
            for value in values:
                key = (kind, value)
                role, reason = classify(
                    kind, value, haystack, defanged=already_defanged
                )

                if key not in registry:
                    registry[key] = Indicator(
                        kind=kind,
                        value=value,
                        defanged=defang(value),
                        role=role,
                        role_reason=reason,
                        first_seen=post.published_at,
                    )
                indicator = registry[key]
                # Strongest role seen across posts wins: one post using a value
                # as a payload host outweighs another citing it as a reference.
                if _ROLE_PRIORITY[role] > _ROLE_PRIORITY[indicator.role]:
                    indicator.role = role
                    indicator.role_reason = reason

                if post.identity not in indicator.post_ids:
                    indicator.post_ids.append(post.identity)
                    platform = str(post.platform)
                    indicator.platform_counts[platform] = (
                        indicator.platform_counts.get(platform, 0) + 1
                    )
                if post.published_at and (
                    indicator.first_seen is None or post.published_at < indicator.first_seen
                ):
                    indicator.first_seen = post.published_at

    indicators = list(registry.values())
    for indicator in indicators:
        indicator.context = (
            f"observed in {plural(len(indicator.post_ids), 'post')} across "
            f"{plural(len(indicator.platform_counts), 'platform')}; not validated"
        )
    # Indicators first, then unknowns, then references; within a role, the most
    # widely observed first — those are the pivots worth an analyst's time.
    indicators.sort(
        key=lambda i: (
            -_ROLE_PRIORITY[i.role],
            -len(i.post_ids),
            i.kind.value,
            i.value,
        )
    )
    return indicators


def indicators_by_kind(indicators: list[Indicator]) -> dict[str, list[Indicator]]:
    grouped: dict[str, list[Indicator]] = defaultdict(list)
    for indicator in indicators:
        grouped[indicator.kind.value].append(indicator)
    return dict(grouped)


def indicators_by_role(indicators: list[Indicator]) -> dict[str, list[Indicator]]:
    grouped: dict[str, list[Indicator]] = defaultdict(list)
    for indicator in indicators:
        grouped[indicator.role.value].append(indicator)
    return dict(grouped)


def actionable(indicators: list[Indicator]) -> list[Indicator]:
    """Values worth pivoting on: indicators only, never references."""
    return [i for i in indicators if i.role is IndicatorRole.INDICATOR]
