# Access, restrictions, and limitations

This file records what the module actually accesses, what the platform terms say,
and what the implementation deliberately refuses to do. It is the document to
read before using the tool on anything other than public research.

## Access paths used

| Platform | Mechanism | Account needed | Cost |
|---|---|---|---|
| YouTube | `yt-dlp` search + metadata extraction | No | Free |
| Telegram | Public web preview `t.me/s/<channel>` | No | Free |
| Discovery | `ddgs` metasearch library | No | Free |

No API keys, subscriptions, proxies, or accounts are required. The multilingual
embedding model is downloaded once and cached locally.

## Platform terms — read before operational use

### Telegram

Telegram's [Terms of Service for Content Licensing](https://telegram.org/tos/content-licensing)
state that access to user content for any purpose other than ordinary use is
prohibited, and explicitly prohibit scraping, indexing, harvesting, aggregating,
or using platform data to train, validate, or deploy AI/ML models absent
narrowly-scoped explicit user consent.

The [API Terms](https://core.telegram.org/api/terms) §1.5 restates this.

**What this means for this module:** reading the public web preview and running
embeddings over the text is a technical demonstration, not a granted permission.
Using deterministic code, a public feed, or a manual import does not change the
terms. Confirm with your organisation (legal/policy) before operational use, and
prefer a permission-backed path where one exists.

### YouTube

The [API Services Developer Policies](https://developers.google.com/youtube/terms/developer-policies)
restrict creating derived data or metrics from API data and copying audiovisual
content without prior written approval. Section III.L describes an approval route
for audited analytics developers.

**What this means:** unofficial extraction is not an exemption, and an API key is
not an analytics licence. The module stores metadata and links only — it does not
download video or audio.

### Other platforms in the original brief

| Platform | Why it is not a core adapter here |
|---|---|
| X | Official reads are pay-per-use; no dependable anonymous search path was demonstrated |
| TikTok | Research access is application- and eligibility-gated; `TikTokApi` search is user-search, not anonymous video-keyword search |
| Facebook | Meta Content Library requires qualified institutional affiliation and approval |
| Instagram | APIs serve professional accounts and limited hashtag/business discovery, not arbitrary historical keyword search |
| Snapchat | Public Profile API is allowlist-only; `yt-dlp` handles known Spotlight URLs only |

## What the module refuses to do

These are not unimplemented features; they are deliberate exclusions.

- **No account, cookie, or session rotation.** Identity cycling to evade rate
  limits or blocks is out of scope.
- **No CAPTCHA or access-control bypass.**
- **No private groups, private profiles, or authenticated surfaces.**
- **No proxy pools.** One request in flight per domain, with bounded backoff.
- **No media downloading, transcription, or forensic authenticity analysis.**
- **No automated truth verdicts, bot labels, or intent attribution.**
- **No cross-platform identity merging** without explicit evidence.

## Rate limiting and blocking

- A single request in flight per domain.
- Exponential-ish backoff, at most two retries, then the stage is marked
  `rate_limited` or `failed`.
- HTTP 429 is surfaced, never hidden.
- The acquisition deadline (default 15 minutes) stops a run rather than letting
  it grind.
- A blocked or failed platform is reported as such. An empty result and a failed
  collection are different states in the export.

Anonymous access can still be rate-limited or blocked by the platform. The module
does not claim this will never happen; it claims it will not be disguised.

## Coverage limits

- **Not exhaustive.** Coverage is sample-based. "Empty" does not mean nothing was
  posted; it means nothing was collected.
- **Search is not an archive.** Date operators improve recall; they do not make
  keyword search a complete historical index.
- **Telegram is channel-seeded.** Undiscovered channels are invisible.
- **Public preview only.** Channels without the web preview cannot be read.
- **No recall estimate.** The module cannot estimate what fraction of platform
  activity it captured.

## Accuracy limits

- Coordination is a **candidate generator**. It has no validated precision/recall
  on live data.
- Narratives are **topic clusters**, not verified narratives or stances.
- Earliest observed is **not** the origin.
- Claim extraction is heuristic and deliberately strict; it will miss many
  checkable claims.
- Language detection is unreliable on short text and returns `None` rather than
  guessing.
- **Threat framing is keyword matching.** It says the vocabulary resembles a
  pattern, not that the pattern is present. A post *discussing* a deepfake is
  flagged as a deepfake claim.
- **Admiralty grades are provisional proxies.** True source reliability requires
  history the module cannot observe. The default for an unknown account is F6.
- **Indicators are extracted and classified, not validated.** No reputation,
  registration, or hosting check is performed. A domain appearing in a post says
  nothing about who owns it or whether it is malicious. Values cited as
  documentation, vendor advisories, or research outlets are classified as
  `reference` and excluded from the indicator set, but the classifier is a
  heuristic over local text and will get some of them wrong in both directions.
- **Impersonation findings are signal co-occurrence.** A legitimate brand post
  can contain urgency and authority language. Lookalike domains are string
  distance only.
- **No media authenticity analysis.** The module tracks claims that media is
  synthetic; it never inspects the file.
- **The risk score is a triage heuristic, not a probability.** 60/100 does not
  mean 60% likelihood of anything. It is additive and itemised so it can be
  recomputed and disputed.

## Data handling

- Exports contain public post text and links. Platform terms may restrict
  redistribution — check before sharing an export outside the research context.
- Credentials: none are stored. If you add optional keys, keep them in the
  environment, never in the repository.
- The dashboard binds to `127.0.0.1` only. It has no authentication and must not
  be exposed publicly.
- Posts are treated as untrusted input: rendered as text, never HTML; CSV cells
  are sanitised against formula injection; remote fetches are restricted to an
  allowlist of platform hosts and blocked from private address ranges.
- **Every extracted indicator is defanged on output** (`hxxp`, `[.]`, `[@]`).
  There is no code path that emits a live, clickable indicator, and hashes are
  never truncated.
- Each assessment carries a TLP marking (default `TLP:AMBER`, `TLP:AMBER+STRICT`
  for hacktivist/leak cases). The marking is a handling instruction for the
  recipient, not a security control — the export is an unencrypted file on disk.

## Reproducibility

Each run records the topic, window, per-query logs, per-platform statuses, budget
caps, and the analysis configuration. Model revision should be pinned for
archival work; a model update can change cluster boundaries.
