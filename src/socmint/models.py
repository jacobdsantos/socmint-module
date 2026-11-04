"""Core data contracts.

Design rule: source facts, derived analysis, and human decisions are separate
types. Nothing here infers a missing value -- unavailable data is ``None`` with
an explicit reason, never zero or a guess.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(UTC)


class Platform(StrEnum):
    YOUTUBE = "youtube"
    TELEGRAM = "telegram"


class StageStatus(StrEnum):
    """Per-stage outcome. 'complete' means complete *within budget*, never platform-wide."""

    OK = "ok"
    PARTIAL = "partial"
    EMPTY = "empty"
    RATE_LIMITED = "rate_limited"
    BLOCKED_POLICY = "blocked_policy"
    BLOCKED_AUTH = "blocked_auth"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


class Precision(StrEnum):
    SECOND = "second"
    MINUTE = "minute"
    DAY = "day"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Intelligence-tradecraft types
# ---------------------------------------------------------------------------


class TLP(StrEnum):
    """Traffic Light Protocol marking for an assessment."""

    CLEAR = "TLP:CLEAR"
    GREEN = "TLP:GREEN"
    AMBER = "TLP:AMBER"
    AMBER_STRICT = "TLP:AMBER+STRICT"
    RED = "TLP:RED"


class CaseTemplate(StrEnum):
    """Analytic lens. Presets change queries and emphasis, not the engine."""

    CONFLICT_DISINFORMATION = "conflict_disinformation"
    IMPERSONATION_DEEPFAKE = "impersonation_deepfake"
    HACKTIVIST_LEAK = "hacktivist_leak"
    CUSTOM = "custom"


class ThreatFraming(StrEnum):
    DISINFORMATION = "disinformation"
    HACKTIVISM = "hacktivism"
    IMPERSONATION = "impersonation"
    DATA_LEAK = "data_leak"
    FRAUD = "fraud"
    DEEPFAKE = "deepfake"
    NONE = "none"


class IndicatorKind(StrEnum):
    DOMAIN = "domain"
    IPV4 = "ipv4"
    URL = "url"
    SHA256 = "sha256"
    MD5 = "md5"
    TELEGRAM_HANDLE = "telegram_handle"
    WALLET = "wallet"
    EMAIL = "email"


class IndicatorRole(StrEnum):
    """What a technical value is doing in the post.

    A domain mentioned in a post is not automatically an indicator. ``php.net``
    cited next to a vulnerability write-up is a *reference*; the same shape of
    string can also be a payload host. Conflating the two puts legitimate sites
    on an IOC list, which is both wrong and operationally harmful.
    """

    INDICATOR = "indicator"  # offered as an artefact of compromise
    REFERENCE = "reference"  # cited as context, documentation, or a source
    UNKNOWN = "unknown"  # cannot be judged from the post alone


class AdmiraltySource(StrEnum):
    """NATO/Admiralty source reliability. Provisional unless analyst-confirmed."""

    A = "A"  # completely reliable
    B = "B"  # usually reliable
    C = "C"  # fairly reliable
    D = "D"  # not usually reliable
    E = "E"  # unreliable
    F = "F"  # cannot be judged


class AdmiraltyInfo(StrEnum):
    """Admiralty information credibility."""

    ONE = "1"  # confirmed by other sources
    TWO = "2"  # probably true
    THREE = "3"  # possibly true
    FOUR = "4"  # doubtful
    FIVE = "5"  # improbable
    SIX = "6"  # truth cannot be judged


class AdmiraltyRating(BaseModel):
    source: AdmiraltySource = AdmiraltySource.F
    info: AdmiraltyInfo = AdmiraltyInfo.SIX
    provisional: bool = True
    rationale: str = ""

    @property
    def code(self) -> str:
        return f"{self.source.value}{self.info.value}"


class Indicator(BaseModel):
    kind: IndicatorKind
    value: str  # canonical, un-defanged
    defanged: str
    role: IndicatorRole = IndicatorRole.UNKNOWN
    role_reason: str = ""
    context: str = ""
    post_ids: list[str] = Field(default_factory=list)
    first_seen: datetime | None = None
    platform_counts: dict[str, int] = Field(default_factory=dict)


class ImpersonationSignal(BaseModel):
    kind: Literal[
        "executive_name", "brand_name", "lookalike_domain", "urgency",
        "credential_request", "authority_claim", "deepfake_claim", "unverified_official",
    ]
    detail: str
    weight: float = 1.0
    evidence: list[str] = Field(default_factory=list)


class ImpersonationFinding(BaseModel):
    finding_id: str
    subject: str  # brand / executive / account under suspicion
    post_ids: list[str] = Field(default_factory=list)
    signals: list[ImpersonationSignal] = Field(default_factory=list)
    severity: Literal["info", "low", "medium", "high"] = "info"
    confidence: Literal["low", "medium", "high"] = "low"
    caveats: list[str] = Field(default_factory=list)


class KeyJudgement(BaseModel):
    """An analytic conclusion with explicit confidence and evidence."""

    judgement_id: str
    statement: str
    confidence: Literal["low", "moderate", "high"] = "low"
    evidence_ids: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class Assessment(BaseModel):
    """Case-level synthesis. Deliberately short; the evidence lives elsewhere."""

    case_template: CaseTemplate = CaseTemplate.CUSTOM
    tlp: TLP = TLP.AMBER
    headline: str = ""
    key_judgements: list[KeyJudgement] = Field(default_factory=list)
    risk_score: int = 0  # 0-100, transparent additive heuristic
    risk_factors: list[str] = Field(default_factory=list)
    confidence: Literal["low", "moderate", "high"] = "low"
    caveats: list[str] = Field(default_factory=list)


class EvidenceQuality(StrEnum):
    LEAD = "lead"  # discovered reference only; not retrieved
    PARTIAL = "partial"  # retrieved but key fields missing
    ORIGINAL = "original"  # retrieved from the platform surface
    REVIEWED = "reviewed"  # analyst-confirmed


class MediaKind(StrEnum):
    PHOTO = "photo"
    VIDEO = "video"
    THUMBNAIL = "thumbnail"
    LINK = "link"


class MediaRef(BaseModel):
    kind: MediaKind
    url: str
    thumbnail_url: str | None = None
    alt: str | None = None


class Engagement(BaseModel):
    """Raw metrics as displayed at collection time. Never summed across platforms."""

    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    observed_at: datetime = Field(default_factory=utcnow)
    missing_reason: str | None = None


class Post(BaseModel):
    platform: Platform
    post_id: str
    url: str
    author_id: str | None = None
    author_name: str | None = None
    text: str = ""
    title: str | None = None
    language: str | None = None
    language_confidence: float | None = None

    published_at: datetime | None = None
    precision: Precision = Precision.UNKNOWN
    published_source: str | None = None  # which field/page element supplied it

    collected_at: datetime = Field(default_factory=utcnow)
    engagement: Engagement | None = None
    media: list[MediaRef] = Field(default_factory=list)

    # Explicit, source-stated relationships only.
    is_repost: bool = False
    reply_to: str | None = None
    referenced_urls: list[str] = Field(default_factory=list)

    evidence_quality: EvidenceQuality = EvidenceQuality.ORIGINAL
    provenance: str | None = None

    # Topic relevance, decided by literal term matching (see text.matches_topic).
    topic_match: bool = True
    topic_terms_matched: list[str] = Field(default_factory=list)

    # Provisional Admiralty rating, assigned from observable evidence only.
    admiralty: AdmiraltyRating | None = None
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)

    @field_validator("text", "title")
    @classmethod
    def _strip_nul(cls, v: str | None) -> str | None:
        return None if v is None else v.replace("\x00", "")

    @property
    def identity(self) -> str:
        return f"{self.platform}:{self.post_id}"

    @property
    def has_timestamp(self) -> bool:
        return self.published_at is not None and self.precision != Precision.UNKNOWN


class DateRange(BaseModel):
    start: datetime
    end: datetime

    @field_validator("end")
    @classmethod
    def _end_after_start(cls, v: datetime, info) -> datetime:
        start = info.data.get("start")
        if start and v <= start:
            raise ValueError("end must be after start")
        return v

    def contains(self, ts: datetime | None) -> bool:
        if ts is None:
            return False
        return self.start <= ts < self.end

    def label(self) -> str:
        return f"{self.start.date().isoformat()}..{(self.end).date().isoformat()}"


class QueryLog(BaseModel):
    platform: Platform
    query: str
    kind: Literal["discovery", "search", "page", "retrieve"]
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    status: StageStatus = StageStatus.OK
    detail: str | None = None
    results: int = 0


class PlatformReport(BaseModel):
    platform: Platform
    discovery: StageStatus = StageStatus.EMPTY
    retrieval: StageStatus = StageStatus.EMPTY
    analysis: StageStatus = StageStatus.EMPTY
    sources_found: int = 0
    posts_retrieved: int = 0
    posts_in_range: int = 0
    truncated: bool = False
    notes: list[str] = Field(default_factory=list)


class Run(BaseModel):
    run_id: str
    topic: str
    aliases: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    case_template: CaseTemplate = CaseTemplate.CUSTOM
    tlp: TLP = TLP.AMBER
    window: DateRange
    created_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    status: StageStatus = StageStatus.OK
    platforms: list[PlatformReport] = Field(default_factory=list)
    queries: list[QueryLog] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Analysis results (derived, always evidence-linked)
# ---------------------------------------------------------------------------


class NarrativeCandidate(BaseModel):
    narrative_id: str
    label: str
    keywords: list[str] = Field(default_factory=list)
    member_ids: list[str] = Field(default_factory=list)
    representative_ids: list[str] = Field(default_factory=list)
    platform_counts: dict[str, int] = Field(default_factory=dict)
    first_seen: datetime | None = None
    method: str = "embedding"
    reviewer_label: str | None = None
    reviewer_stance: str | None = None
    notes: list[str] = Field(default_factory=list)
    framing: ThreatFraming = ThreatFraming.NONE
    framing_evidence: list[str] = Field(default_factory=list)


class CoordinationSignal(BaseModel):
    kind: Literal[
        "shared_url", "near_duplicate_text", "semantic_similarity", "time_proximity"
    ]
    detail: str
    weight: float = 1.0


class CoordinationEpisode(BaseModel):
    account_a: str
    account_b: str
    post_a: str
    post_b: str
    signal_kinds: list[str]
    seconds_apart: float | None = None
    detail: str = ""


class CoordinationGroup(BaseModel):
    group_id: str
    account_ids: list[str] = Field(default_factory=list)
    platform: str | None = None
    episodes: list[CoordinationEpisode] = Field(default_factory=list)
    signals: list[CoordinationSignal] = Field(default_factory=list)
    confidence: Literal["insufficient_evidence", "candidate", "strong_candidate"] = (
        "insufficient_evidence"
    )
    baseline_episodes: float | None = None
    benign_explanations: list[str] = Field(default_factory=list)
    reviewer_decision: str | None = None
    limitations: list[str] = Field(default_factory=list)


class PropagationEdge(BaseModel):
    edge_id: str
    narrative_id: str
    from_post: str
    to_post: str
    kind: Literal["explicit", "inferred"]
    signal: str
    seconds_apart: float | None = None
    note: str = ""


class EarliestObserved(BaseModel):
    narrative_id: str
    post_ids: list[str] = Field(default_factory=list)
    accounts: list[str] = Field(default_factory=list)
    platforms: list[str] = Field(default_factory=list)
    observed_at: datetime | None = None
    caveat: str = (
        "Earliest observed in the collected sample within the requested window; "
        "not established as the true origin."
    )


class ClaimEvidence(BaseModel):
    url: str
    title: str | None = None
    excerpt: str | None = None
    publisher: str | None = None
    published_at: datetime | None = None
    retrieved_at: datetime = Field(default_factory=utcnow)


class ClaimAssessment(BaseModel):
    claim_id: str
    post_id: str
    platform: Platform
    claim_text: str
    language: str | None = None
    verdict: Literal[
        "unreviewed", "supported", "contradicted", "mixed", "unverified", "not_checkable"
    ] = "unreviewed"
    rationale: str | None = None
    evidence: list[ClaimEvidence] = Field(default_factory=list)
    reviewer: str | None = None
    reviewed_at: datetime | None = None
    existing_factcheck: str | None = None


class AnalysisBundle(BaseModel):
    narratives: list[NarrativeCandidate] = Field(default_factory=list)
    coordination: list[CoordinationGroup] = Field(default_factory=list)
    propagation: list[PropagationEdge] = Field(default_factory=list)
    earliest: list[EarliestObserved] = Field(default_factory=list)
    claims: list[ClaimAssessment] = Field(default_factory=list)
    method_notes: list[str] = Field(default_factory=list)
    degraded: bool = False

    # Cyber-threat-intelligence layer
    indicators: list[Indicator] = Field(default_factory=list)
    impersonation: list[ImpersonationFinding] = Field(default_factory=list)
    assessment: Assessment | None = None
