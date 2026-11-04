"""Case templates.

One engine, four lenses. A template does **not** change what is collected or how
it is scored internally — it changes what the assessment foregrounds and which
threat framings count as in-focus for the risk heuristic.

That separation is deliberate. A "conflict disinformation" case and a "brand
impersonation" case run the same collection, the same clustering and the same
coordination detector; only the analyst's framing of the result differs. It also
means an evaluator can hand over an unexpected topic and still get a coherent
output by selecting ``custom``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import TLP, CaseTemplate, ThreatFraming


@dataclass(frozen=True)
class CasePreset:
    template: CaseTemplate
    label: str
    description: str
    focus_framings: tuple[ThreatFraming, ...] = ()
    default_tlp: TLP = TLP.AMBER
    # Framings that matter enough to raise the risk heuristic when present.
    analyst_prompts: tuple[str, ...] = field(default_factory=tuple)


PRESETS: dict[CaseTemplate, CasePreset] = {
    CaseTemplate.CONFLICT_DISINFORMATION: CasePreset(
        template=CaseTemplate.CONFLICT_DISINFORMATION,
        label="Conflict / disinfo",
        description=(
            "State-aligned or opportunistic information operations around an armed "
            "conflict, election, or disaster: competing casualty claims, forged "
            "official announcements, and coordinated amplification of a single line."
        ),
        focus_framings=(
            ThreatFraming.DISINFORMATION,
            ThreatFraming.DEEPFAKE,
            ThreatFraming.IMPERSONATION,
        ),
        default_tlp=TLP.AMBER,
        analyst_prompts=(
            "Which claims are checkable against a primary source?",
            "Do the accounts coordinate, or merely share a hashtag?",
            "Is the earliest observed post from the apparent originator or a re-poster?",
        ),
    ),
    CaseTemplate.IMPERSONATION_DEEPFAKE: CasePreset(
        template=CaseTemplate.IMPERSONATION_DEEPFAKE,
        label="Impersonation",
        description=(
            "Brand abuse and executive impersonation: lookalike domains, fake "
            "official accounts, urgency and credential-request language, and claims "
            "that a video or voice recording is synthetic."
        ),
        focus_framings=(
            ThreatFraming.IMPERSONATION,
            ThreatFraming.DEEPFAKE,
            ThreatFraming.FRAUD,
        ),
        default_tlp=TLP.AMBER,
        analyst_prompts=(
            "Who registered the lookalike domain, and when?",
            "Does the media claim come with a forensic artefact or only an assertion?",
            "Is the account verified on-platform, or only claiming to be?",
        ),
    ),
    CaseTemplate.HACKTIVIST_LEAK: CasePreset(
        template=CaseTemplate.HACKTIVIST_LEAK,
        label="Hacktivist / leak",
        description=(
            "Hacktivist claims, DDoS and defacement announcements, and data-leak "
            "extortion: actor-branded channels, victim naming, sample releases, and "
            "leak-site cross-posting."
        ),
        focus_framings=(
            ThreatFraming.HACKTIVISM,
            ThreatFraming.DATA_LEAK,
        ),
        default_tlp=TLP.AMBER_STRICT,
        analyst_prompts=(
            "Is the leak claim corroborated by a sample or only by a screenshot?",
            "Does the actor identity match an existing tracked cluster?",
            "Are the extracted indicators infrastructure or victim data?",
        ),
    ),
    CaseTemplate.CUSTOM: CasePreset(
        template=CaseTemplate.CUSTOM,
        label="Custom topic",
        description=(
            "No assumed threat pattern. Framings are still classified where the "
            "language supports it, but nothing is treated as in-focus and the risk "
            "heuristic only counts structural signals (coordination, indicators)."
        ),
        focus_framings=(),
        default_tlp=TLP.AMBER,
        analyst_prompts=(
            "What would count as a finding for this topic?",
            "Which of the extracted indicators are worth pivoting on?",
        ),
    ),
}


def resolve(template: CaseTemplate | str | None) -> CasePreset:
    """Look up a preset, falling back to ``custom`` for anything unrecognised."""
    if isinstance(template, str):
        try:
            template = CaseTemplate(template)
        except ValueError:
            template = CaseTemplate.CUSTOM
    return PRESETS.get(template or CaseTemplate.CUSTOM, PRESETS[CaseTemplate.CUSTOM])


def catalogue() -> list[dict[str, str]]:
    """Preset list for CLI help and the dashboard sidebar."""
    return [
        {
            "template": preset.template.value,
            "label": preset.label,
            "description": preset.description,
        }
        for preset in PRESETS.values()
    ]
