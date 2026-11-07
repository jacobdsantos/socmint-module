"""An optional model-written summary, grounded in findings that already exist.

The rest of this package derives every sentence from a record. This module is
the one place a language model writes prose, so it is built around two
constraints that keep it from undermining that:

**It condenses, it does not conclude.** The prompt receives the findings as
structured counts and labels. It is told that it may not add a fact. Anything it
writes has to be a rephrasing of something already on the page.

**The output is checked against the bundle before it is used.** A model that
names an account group, an indicator or a narrative that does not exist has
produced a fabrication, and a fabricated case summary is worse than none. If any
identifier in the generated text is not in the bundle, the summary is dropped.

What is deliberately *not* sent: post text, account handles, and indicator
values. The digest carries counts, narrative keywords and group identifiers —
enough to summarise, nothing that would export private indicators to a third
party.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from .models import AnalysisBundle, Run
from .prose import kind_label, platform_label, plural

logger = logging.getLogger(__name__)

#: The Interactions API replaced ``generateContent``; this is its endpoint.
_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/interactions"
_API_REVISION = "2026-05-20"
DEFAULT_MODEL = "gemini-3.8-flash"

_SYSTEM = (
    "You write one paragraph of plain prose summarising a social-media "
    "investigation, for an analyst who will read the detail below it.\n\n"
    "Rules:\n"
    "- State only what the findings contain. Add no fact, cause, motive, "
    "attribution or conclusion of your own.\n"
    "- Never invent an identifier, account, domain, hash or number.\n"
    "- No bullet points, no headings, no markdown, no preamble.\n"
    "- 90 to 140 words, in the present tense, in the register of a case note.\n"
    "- Where the findings show a limit, say so plainly; do not hedge vaguely."
)

#: Identifier shapes a summary must not invent. Counts are not checked: a model
#: restating "16 narratives" is not a fabrication in the way a new IOC is.
_ID_PATTERNS = (
    re.compile(r"\b(?:n|c|e|imp|q)\d{3}\b"),               # narrative/group/edge ids
    re.compile(r"\b[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)+\b"),  # live domains
    re.compile(r"\b[a-z0-9][a-z0-9-]*(?:\[\.\][a-z0-9-]+)+\b"),  # defanged domains
    re.compile(r"\b[0-9a-f]{32}\b"),                        # md5
    re.compile(r"\b[0-9a-f]{40}\b"),                        # sha1
    re.compile(r"\b[0-9a-f]{64}\b"),                        # sha256
)

#: Prose that looks like an identifier but is not one.
_NOT_IDENTIFIERS = {"e.g", "i.e", "u.s", "a.m", "p.m", "vs", "etc"}


class SummaryUnavailable(Exception):
    """No summary was produced. The caller renders the deterministic brief only."""


@dataclass(frozen=True)
class ModelSummary:
    text: str
    model: str
    generated_at: datetime

    @property
    def provenance(self) -> str:
        stamp = self.generated_at.astimezone(UTC).strftime("%-d %b %Y %H:%M UTC")
        return f"{self.model} · {stamp}"


def _digest(run: Run, bundle: AnalysisBundle, post_count: int) -> str:
    """The findings as plain lines. Counts and labels only — no post text."""
    lines = [
        f"TOPIC: {run.topic}",
        f"WINDOW: {run.window.start.date()} to {run.window.end.date()} (exclusive end)",
        f"POSTS: {plural(post_count, 'post')} collected and analysed",
    ]

    if bundle.narratives:
        lines.append(f"\nNARRATIVE CANDIDATES ({len(bundle.narratives)}):")
        for narrative in bundle.narratives[:12]:
            platforms = ", ".join(
                f"{platform_label(name)} {count}"
                for name, count in sorted(
                    narrative.platform_counts.items(), key=lambda kv: -kv[1]
                )
            )
            parts = [f"- {narrative.narrative_id}: {plural(len(narrative.member_ids), 'post')}"]
            if platforms:
                parts.append(platforms)
            if narrative.label:
                parts.append(f"keywords: {narrative.label}")
            if narrative.framing.value != "none":
                parts.append(f"framing: {narrative.framing.value}")
            if narrative.first_seen:
                parts.append(f"first observed {narrative.first_seen:%Y-%m-%d %H:%M} UTC")
            lines.append("; ".join(parts))
        if len(bundle.narratives) > 12:
            lines.append(f"- and {len(bundle.narratives) - 12} further candidates")

    if bundle.coordination:
        lines.append(f"\nACCOUNT GROUPS ({len(bundle.coordination)}):")
        for group in bundle.coordination[:6]:
            signals = ", ".join(s.kind.replace("_", " ") for s in group.signals[:4])
            line = (
                f"- {group.group_id}: {plural(len(group.account_ids), 'account')}, "
                f"{plural(len(group.episodes), 'matching episode')}, "
                f"confidence {group.confidence.replace('_', ' ')}"
            )
            if signals:
                line += f"; signals: {signals}"
            lines.append(line)
    else:
        lines.append(
            "\nACCOUNT GROUPS: none met the repeated-evidence threshold in this sample"
        )

    actionable = [i for i in bundle.indicators if i.role.value == "indicator"]
    references = [i for i in bundle.indicators if i.role.value == "reference"]
    if actionable:
        kinds: dict[str, int] = {}
        for indicator in actionable:
            kinds[indicator.kind.value] = kinds.get(indicator.kind.value, 0) + 1
        breakdown = ", ".join(f"{count} {kind_label(kind, count)}" for kind, count in kinds.items())
        lines.append(
            f"\nINDICATORS: {plural(len(actionable), 'value')} presented as artefacts of "
            f"compromise ({breakdown}); {plural(len(references), 'cited reference')} "
            "classified separately. Values are not listed in this digest."
        )
    else:
        lines.append(
            f"\nINDICATORS: none presented as artefacts of compromise; "
            f"{plural(len(references), 'cited reference')} classified separately"
        )

    if bundle.claims:
        assessed = sum(1 for c in bundle.claims if c.verdict != "unreviewed")
        lines.append(
            f"\nCLAIMS: {plural(len(bundle.claims), 'claim')} proposed, "
            f"{assessed} assessed"
        )

    if bundle.impersonation:
        finding = bundle.impersonation[0]
        lines.append(
            f"\nIMPERSONATION: {plural(len(bundle.impersonation), 'finding')}; strongest is "
            f"{finding.severity} severity at {finding.confidence} confidence"
        )

    if bundle.degraded:
        lines.append("\nNOTE: analysis ran degraded; see the method notes.")
    return "\n".join(lines)


def _text_from_response(payload: dict) -> str:
    """Pull the generated text out of an Interactions response.

    The documented shape is ``steps[-1].content[].text``. ``output_text`` is an
    SDK convenience the raw JSON may or may not carry, so it is a fallback.
    """
    steps = payload.get("steps") or []
    for step in reversed(steps):
        parts = step.get("content") or []
        chunks = [p.get("text", "") for p in parts if isinstance(p, dict) and p.get("text")]
        if chunks:
            return "".join(chunks).strip()
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"].strip()
    raise SummaryUnavailable("the response carried no text")


def _known_identifiers(run: Run, bundle: AnalysisBundle) -> set[str]:
    known = {n.narrative_id for n in bundle.narratives}
    known |= {g.group_id for g in bundle.coordination}
    known |= {e.edge_id for e in bundle.propagation}
    known |= {f.finding_id for f in bundle.impersonation}
    known |= {c.claim_id for c in bundle.claims}
    for indicator in bundle.indicators:
        known.add(indicator.defanged.lower())
        known.add(indicator.value.lower())
    return {k.lower() for k in known if k}


def _check_grounded(text: str, known: set[str]) -> None:
    """Raise if the model named an identifier the bundle does not contain."""
    lowered = text.lower()
    unknown: list[str] = []
    for pattern in _ID_PATTERNS:
        for match in pattern.findall(lowered):
            token = match.strip(".,;:()[]")
            if not token or token in _NOT_IDENTIFIERS:
                continue
            # A version number ("3.8") is not an identifier either.
            if not any(ch.isalpha() for ch in token):
                continue
            if token not in known and token not in unknown:
                unknown.append(token)
    if unknown:
        raise SummaryUnavailable(
            "the summary named values absent from the findings: "
            + ", ".join(unknown[:5])
        )


def _tidy(text: str) -> str:
    """One paragraph, no markdown, no stray quotes."""
    text = re.sub(r"[*_#`]+", "", text)
    text = re.sub(r"\s*\n\s*", " ", text).strip()
    text = re.sub(r"^(?:summary|overview)\s*[:\-—]\s*", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", text)


def build_summary(
    run: Run,
    bundle: AnalysisBundle,
    *,
    post_count: int,
    api_key: str,
    model: str = DEFAULT_MODEL,
    timeout: float = 45.0,
) -> ModelSummary:
    """Ask the model to condense the findings. Raises :class:`SummaryUnavailable`."""
    if not api_key:
        raise SummaryUnavailable("no API key configured")

    body = {
        "model": model,
        "system_instruction": _SYSTEM,
        "input": _digest(run, bundle, post_count),
        "generation_config": {"max_output_tokens": 400, "temperature": 0.2},
    }
    try:
        response = httpx.post(
            _ENDPOINT,
            headers={
                "x-goog-api-key": api_key,
                "Content-Type": "application/json",
                "Api-Revision": _API_REVISION,
            },
            content=json.dumps(body),
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise SummaryUnavailable(f"request failed: {exc}") from exc

    if response.status_code >= 400:
        detail = response.text[:200].replace("\n", " ")
        raise SummaryUnavailable(f"HTTP {response.status_code}: {detail}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise SummaryUnavailable("the response was not JSON") from exc

    text = _tidy(_text_from_response(payload))
    if not text:
        raise SummaryUnavailable("the model returned nothing usable")

    _check_grounded(text, _known_identifiers(run, bundle))
    return ModelSummary(text=text, model=model, generated_at=datetime.now(UTC))


__all__ = [
    "DEFAULT_MODEL",
    "ModelSummary",
    "SummaryUnavailable",
    "build_summary",
]
