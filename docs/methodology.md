# Methodology

This document explains what the module computes, why, and where the reasoning
stops. It is written so a reviewer can challenge any step.

## Pipeline

```
topic + date range
      │
      ├─ 1. Discovery        find public sources that discuss the topic
      ├─ 2. Retrieval        fetch original records from those sources
      ├─ 3. Relevance        decide which posts are in scope (literal matching)
      ├─ 4. Narratives       group posts by meaning (multilingual embeddings)
      ├─ 5. Coordination     link accounts by repeated shared-content evidence
      ├─ 6. Spread           earliest observed + explicit/inferred links
      ├─ 7. Claims           propose checkable claims for human review
      └─ 8. Review/Export    analyst decisions persist; artefacts are written
```

Each stage writes its own status. A run can end `partial` without being wrong.

## 1–2. Discovery and retrieval

**YouTube.** Queries are issued through `yt-dlp` with the search operators
`after:` and `before:`. This matters: an unbounded keyword search ranks by
relevance, so for a topic whose main event happened months earlier, the top
twenty results can all fall outside the requested window even when
contemporaneous posts exist. In testing, an unbounded query returned 0–2
in-window results while the bounded query returned 20 of 20.

**Telegram.** There is no anonymous global keyword index, so the module uses a
two-step process:

1. Web search (`site:t.me/s/ <topic>`) yields candidate public channels.
2. Each channel's public web preview (`t.me/s/<channel>`) is paged backwards by
   message id, keeping posts inside the window.

This is **channel-seeded** retrieval. It cannot enumerate every channel that ever
discussed a topic, and a channel with the public preview disabled is invisible.

**Mastodon.** Also seeded, and for the same reason: anonymous keyword search
(`/api/v2/search`) needs a token. The module derives candidate hashtags from the
topic — the joined form first (`#typhoonphilippines`), then each significant
word — and reads each instance's public tag timeline, paginating by `max_id`.
Instances that disable public preview return 401 and are skipped with a note.

Two consequences are stated rather than hidden. A topic with no hashtag yields
nothing, so cyber topics (CVE identifiers, malware names) get little from this
source while conflict and disaster topics do well. And four instances is a
sample of four instances, not of the fediverse.

Federated copies are de-duplicated by the status `uri`, not its instance-local
`id`: the same post carries a different `id` on every instance that relays it,
so keying on `id` returns one post four times.

**Google News.** A single keyless RSS query, phrase-quoted with aliases OR'd.
The feed is reverse-chronological and capped near 100 items, so the request asks
for the whole lookback window and the exact window is applied locally
afterwards.

**GDELT.** A DOC 2.0 `artlist` query bounded by `startdatetime`/`enddatetime`.
Included but **disabled by default**: it allows roughly one request every five
seconds per IP with no `Retry-After`, and behind a shared NAT that quota is
usually already spent. Enable it where the network allows.

### News records are metadata only

Google News and GDELT records carry a headline, a publisher domain, a
publication time and a permalink. The article body is never fetched, so their
`evidence_quality` is `partial` and no claim is verified from them. They are
also excluded from coordination and from account-based corroboration — see
`docs/decisions.md` §16.

### Why relevance is decided at post level

A discovered channel can be topically adjacent while most of its traffic is
unrelated. In an early run, one global alert feed supplied 144 of 152 posts, none
of which concerned the requested country. Post-level matching fixed the precision
and the narrative quality.

A post is in scope when it contains **every significant word** of the topic
(`"earthquake Philippines"` requires both words), or one alias phrase. Matching
is literal: no stemming, translation, or synonym expansion, so any match is
explainable as "this string occurred".

YouTube results inherit relevance from the query itself and are not discarded
merely because the description omits the term; literal hits are still recorded.

## 3. Dates

A bare `YYYY-MM-DD` is an inclusive local calendar date, converted to a
half-open UTC window `[start_midnight, end_midnight + 1 day)`. Full ISO
timestamps are taken literally.

Only **publication** timestamps count. A date written inside post text (an
earthquake event time, a quoted article date) is never used to place a post in
the window. Records with no publication timestamp stay in an undated bucket and
cannot support minute-level timing claims.

## 4. Narratives

Text is embedded with `intfloat/multilingual-e5-small` (384 dimensions, 100
languages per its model card).

**Centring.** Raw e5 cosine similarity sits in a narrow 0.7–1.0 band. Measured on
a small probe, unrelated text scored ~0.76 and related text ~0.87 — overlapping
distributions that no single threshold can separate. Subtracting the batch mean
before normalising widens the gap so that related and unrelated pairs no longer
overlap. All thresholds refer to *centred* cosine.

**Clustering.** Average-linkage agglomerative clustering on cosine distance.
Average linkage was chosen over connected components because single-linkage
chaining lets one bridging post merge two unrelated narratives.

**Labels.** Extractive keywords over-represented in the cluster relative to the
corpus. Plain frequency labels every cluster with the topic's own vocabulary
("earthquake / news"), which is useless for distinguishing frames.

**Limits.**
- A cluster is a topic/framing candidate, **not** a stance. Opposing posts often
  share vocabulary and will cluster together.
- Low-resource languages degrade. The model card says so; we do not claim equal
  performance across 100 languages.
- Inputs are truncated to 512 tokens by the model.
- If the model is unavailable the pipeline falls back to TF-IDF and marks the run
  `degraded`; cross-language grouping is then explicitly not claimed.

## 5. Coordination

A conservative subset of Pacheco et al., *Uncovering Coordinated Networks on
Social Media* (ICWSM 2021): behavioural traces → account-feature graph →
similarity projection → support and popularity filtering → group extraction.

Signals implemented:

| Signal | Rule |
|---|---|
| shared URL | canonicalised URL (tracking params stripped) in both posts |
| near-duplicate text | identical normalised text, plus shingle-Jaccard ≥ 0.7 |
| semantic similarity | centred cosine ≥ 0.45 **and** within the time window |
| time proximity | required for every pair, default 15 minutes |

**Thresholds.**

- At least **two independent episodes** per account pair. One coincidence is not
  a pattern.
- Features used by more than 25% of accounts (minimum 4) are discounted as
  generic links or boilerplate.
- Accounts must be distinct; same-account reposts never pair.
- Posts without a verified timestamp are excluded.

**Baseline.** Post timestamps are shuffled *within each account*, preserving both
activity volume and the multiset of times, then the same pipeline is re-run. This
shows how many qualifying pairs arise from chance alone. The baseline count is
reported alongside every group.

**What the output is not.** A group is a set of accounts whose posts repeatedly
matched. It is not a probability, not a bot label, and not evidence of common
ownership or intent. Connected components group accounts; they do not imply every
member coordinated with every other member. Every group ships with benign
explanations (newsroom networks, advocacy campaigns, scheduled publishing) and
explicit limitations.

## 6. Spread

Edges are split:

- **explicit** — the platform states the relationship (reply target, forward).
- **inferred** — identical normalised text plus chronology.

Inferred edges are associations. Two posts with the same text may both copy a
third source outside the sample. The reported "earliest observed" is the earliest
post *in the collected sample within the window*, never the origin.

## 7. Claims

Automation proposes sentences that contain both a factual verb and a concrete
figure, and skips promotional boilerplate. Each candidate starts `unreviewed`.

The only verdicts are: supported, contradicted, mixed, unverified, not checkable.
A verdict requires a human to record a rationale; the UI refuses a verdict without
one. Retrieving evidence is not verification, and a missing fact-check match means
"unverified", not "false".

## 8. Cyber-threat-intelligence layer

Four additions sit on top of the analysis above. None of them changes the
collection path; all of them are derived from the same evidence.

### 8.1 Threat framing

Each narrative cluster is labelled with the threat pattern its vocabulary
resembles: disinformation, hacktivism, impersonation, data leak, fraud, deepfake.
Matching is literal keyword lookup, and the matched terms are shown next to the
label (`framing_evidence`), so the label is auditable.

Two guards:

- A cluster spanning more than 60% of the corpus is a catch-all, typical of the
  lexical fallback. Framing is left unclassified rather than describing the whole
  sample as one narrative.
- A framing is a *lens*, never a conclusion about intent. The label says "this
  language resembles a data-leak narrative", not "this is a leak".

### 8.2 Indicators and roles

Regex extraction of domains, IPv4, URLs, SHA256, MD5, BTC/ETH wallets, Telegram
handles and email addresses, aggregated per post with first-seen timestamps and
per-platform counts.

Extraction runs twice: on the text as written, and on a normalised copy with
`hxxp` / `[.]` / `[@]` reversed. The second pass is what catches values the author
defanged themselves — the ones most likely to be hostile, and the ones a plain
domain regex cannot see, because `evil[.]com` is not a domain.

**Infrastructure is dropped before classification.** The platform a post was
published on, a creator's Patreon, a Google Form, a generic cloud host — these
are not pivots, and listing them as "references" still buries the real values.
They are excluded at extraction, for both bare domains and URLs. Entries are
registrable forms (`ecr.aws`, not `gallery.ecr.aws`), because both extraction
paths reduce a host before comparing.

**Every remaining value then gets a role.** A topic search returns posts that
cite the language documentation, the vendor advisory, and a researcher's blog.
Treating those as indicators puts legitimate sites on an IOC list.

| # | Signal | Role |
|---|---|---|
| 1 | The author defanged it | indicator |
| 2 | Hash or wallet address | indicator |
| 3 | Known documentation / vendor / registry domain | reference |
| 4 | URL path is an article (`/blog/`, `/2026/`, `/advisory/`) | reference |
| 5 | Sits in a credential-lure sentence | indicator |
| 6 | Sits in attribution language ("according to", "read the") | reference |
| 7 | Sits next to hostile-artefact vocabulary | indicator |
| 8 | None of the above | unknown |

The context window is 120 characters. Wider picks up marketing text from video
descriptions ("subscribe", "buy my course") as though it were threat context.
The lure pattern is deliberately narrow — about credentials or money, never
merely about clicking.

Rules are checked in that order and the firing rule is recorded in
`role_reason`. A URL inherits its host's role; a bare domain whose only
appearances are inside article-style URLs is a citation.

Only `indicator` values feed the risk score. References are reported separately
and hidden by default in the dashboard.

- **Defanged at the model boundary.** `Indicator.value` is canonical;
  `Indicator.defanged` is what every export and every dashboard render uses.
  There is no path that emits a live link.
- **Full hashes only.** The regex requires the complete 32- or 64-character
  digest, and nothing truncates it downstream.
- **Extraction is not validation.** A role of `indicator` means "the post
  presents this as an artefact of compromise", not "this is malicious". No
  reputation, registration, or hosting check is performed.

### 8.3 Impersonation and brand abuse

Per-post signal detection over five kinds: authority claim, urgency, credential
request, deepfake claim, and lookalike domain.

Lookalike matching uses two rules against the compacted topic and its words:

| Rule | Catches | Example |
|---|---|---|
| edit distance ≤ 2 vs. the compacted phrase | typosquats | `acmebnak.com` |
| containment of ≥ 2 brand words | combosquats | `acme-bank-secure.com` |

For single-word brands the containment requirement drops to one, otherwise no
domain could ever match.

**Severity counts distinct signal kinds, not repetitions.** Six posts saying
"urgent" are one weak signal, not six. This was a deliberate correction: summing
per-post weights let a noisy corpus outrank a genuinely targeted campaign.

**A "mentions the brand" signal is deliberately absent.** In a topic search every
post mentions the topic, so it would fire across the whole corpus and inflate the
distinct-kind count for zero information.

**No media forensics.** A deepfake claim is a claim in text. The module does not
inspect the video.

### 8.4 Admiralty grading

Every post gets a provisional A–F / 1–6 rating.

**Source grade** is a proxy from observable behaviour:

| Observation | Grade |
|---|---|
| Newsroom-like name and ≥ 3 posts in sample | C |
| Newsroom-like name but too few posts to judge | D |
| ≥ 5 posts, no verifiable editorial identity | D |
| Anonymous or low-volume | F |

**Information grade** comes from cross-account narrative support:

| Support | Grade |
|---|---|
| ≥ 3 accounts across ≥ 2 platforms | 2 |
| ≥ 2 accounts | 3 |
| ≥ 2 posts, single account | 4 |
| Otherwise | 6 |

Two corrections matter here, and both are enforced in code:

- **Catch-all exclusion.** A cluster covering more than 60% of the corpus is
  skipped, because otherwise every post inherits "3+ accounts across 2 platforms"
  and the whole run grades 2.
- **Coordination is not corroboration.** Identical text across accounts is a
  coordination signal, not independent support. Independent support means
  different accounts carrying the claim in their own words, which narrative
  membership across distinct accounts only approximates.

Every grade carries `provisional=True` and the rule that produced it. The
dashboard shows the rationale beside the badge.

### 8.5 Case assessment

`Assessment` holds a TLP marking, key judgements with confidence and caveats, and
an additive risk score.

Risk contributions (capped at 100, each emitted as a string):

| Signal | Points |
|---|---|
| Coordination group at `strong_candidate` | +25 each, max 50 |
| Coordination group at `candidate` | +10 each, max 20 |
| In-focus framing matched | +10 each, max 20 |
| Impersonation finding, high / medium / low | +25 / +15 / +5 |
| High-value indicator kinds present (hash, wallet) | +5 each, max 15 |

The score is **not a probability**. It answers "which run deserves attention
first", and can be recomputed by hand from the factor list. A degraded run adds
zero but states that its framing and coordination signals are weaker than the
score implies.

### 8.6 Case templates

Four presets: conflict/disinformation, impersonation/deepfake, hacktivist/leak,
custom. A preset selects which framings are *in focus* (for the risk heuristic)
and attaches analyst prompts. It does not change collection, clustering, or
detection — so an unexpected topic from an evaluator runs the identical engine.

## 9. Known error sources

| Source | Effect |
|---|---|
| Search ranking | Popular older posts outrank relevant recent ones; mitigated by date operators, not eliminated |
| Channel-seeded Telegram | Silent omission of undiscovered channels |
| Public-preview availability | Channels without web preview are invisible |
| Literal relevance | Paraphrases, misspellings, and cross-language mentions are missed |
| Embedding truncation | Long posts are compared on their first 512 tokens |
| Timezone | Date-only posts cannot establish minute-level synchrony |
| Platform deletion | Collected posts may disappear; exports are snapshots |
| Engagement | View/like counts are snapshots, not historical totals |

## Evaluation status

The coordination detector is validated against **synthetic fixtures** (positive:
repeated near-identical posting; negative: organic breaking-news burst; negative:
generic shared link). Those tests establish implementation behaviour, not
real-world detection accuracy. No precision or recall figure is claimed for
coordination on live data, because no ground truth exists for the sampled topics.

The CTI layer is covered by guards rather than accuracy claims:

| Guard | Asserted by |
|---|---|
| A catch-all cluster cannot upgrade information grades | `test_catch_all_cluster_does_not_upgrade_information_grade` |
| Severity counts distinct signal kinds, not repetitions | `test_severity_counts_distinct_kinds_not_repetitions` |
| One brand word does not flag an unrelated domain | `test_single_brand_word_does_not_flag_an_unrelated_domain` |
| Every extracted indicator is defanged | `test_every_extracted_indicator_is_defanged` |
| Case templates change the lens, not the score | `test_templates_do_not_change_the_engine_only_the_lens` |
| An absent coordination signal is reported as a negative | `test_no_coordination_is_reported_as_a_negative_not_a_failure` |

Full detail on what was checked, what was found wrong, and what remains
unverified is in `docs/validation.md`.
