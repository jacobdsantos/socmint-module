# SOCMINT Module

AI-native social threat intelligence for a topic and date range: multilingual
narrative candidates with threat framing, explainable coordination candidates,
extracted and defanged indicators, impersonation signals, Admiralty-graded
evidence, and a case assessment a human can defend.

Built for a five-day assessment in a **cyber threat intelligence** context — the
three priority cases are conflict disinformation, impersonation/deepfakes, and
hacktivist data leaks. One engine serves all three; the case template changes the
analyst's lens, not the pipeline.

It is a **research prototype**, not a surveillance product.

---

## What it does

Given a topic and a date range, the module:

1. **Discovers** topic-relevant public sources without a preconfigured watchlist.
2. **Retrieves** original public records from YouTube and Telegram.
3. **Groups** posts into multilingual narrative candidates (semantic clustering)
   and labels the threat framing each cluster resembles.
4. **Flags** account groups with repeated, time-aligned shared-content evidence,
   compared against an activity-preserving time-shuffle baseline.
5. **Extracts** technical indicators (domains, IPs, hashes, wallets, handles) and
   **defangs** every one of them on output.
6. **Surfaces** impersonation and brand-abuse signals, including lookalike
   domains, urgency, credential requests, and deepfake claims.
7. **Grades** every post with a provisional Admiralty source/information rating
   and shows the rule that produced it.
8. **Synthesises** a case assessment: itemised risk score, key judgements with
   confidence and caveats, TLP marking.
9. **Reconstructs** earliest-observed spread and explicit vs. inferred links.
10. **Queues** checkable claims with candidate evidence for human review.
11. **Exports** JSON (authoritative) and CSV tables, and serves a local dashboard.

### What it explicitly does *not* do

| Not done | Why |
|---|---|
| Label accounts as bots, malicious, or state-linked | Coordination ≠ intent; no validated classifier exists here |
| Declare a claim true or false automatically | Only a reviewer assigns a verdict |
| Call the first collected post the "origin" | It is the earliest *observed in sample* |
| Decide that a video or recording is synthetic | Media forensics needs the file; this tracks *claims* about synthetic media |
| Validate or enrich extracted indicators | Extraction is mechanical; enrichment is a separate, attributable step |
| Treat a coordination match as corroboration | Identical text across accounts is coordination, not independent support |
| Present search results as exhaustive | Coverage is a sample and is reported as such |
| Bypass logins, CAPTCHAs, or rate limits | No account rotation, no evasion |
| Download video/audio or run media forensics | Previews and links only |

---

## Platforms

| Platform | Kind | Access | Seeding | Key |
|---|---|---|---|---|
| **YouTube** | social | `yt-dlp` keyword search + metadata | keyword | no |
| **Telegram** | social | Public `t.me/s/<channel>` web preview | channel (via web search) | no |
| **Mastodon** | social | Public `/api/v1/timelines/tag/` | hashtag (derived from topic) | no |
| **Reddit** | social | Free OAuth app, read-only | keyword (global search) | yes, free |
| **X** | social | Xpoz API, **metered** | keyword | yes, paid credits |
| **Google News** | news | Public RSS search | keyword | no |

Four access models, all free: anonymous scraping of public surfaces, a public
RSS index, a free OAuth app, and — where the network allows — the GDELT DOC API
(off by default; see below).

Reddit is the only source needing a credential. Register a **script** app at
<https://www.reddit.com/prefs/apps> (free, instant) and set:

```bash
export REDDIT_CLIENT_ID=...
export REDDIT_CLIENT_SECRET=...
```

Without them Reddit reports `unsupported` — not `empty`, because "I have no key"
and "nobody posted" are different findings and the output should not confuse
them. Use `--no-reddit` to skip it deliberately.

### X is metered — and that is enforced in code

X is the only source that costs money, so it has two explicit gates on top of
each other:

```bash
export XPOZ_API_KEY=...
pip install -e ".[xpoz]"
socmint run --topic "..." --start ... --end ... --x --allow-spend
```

Without `--x` it is not collected. With `--x` but no `--allow-spend` it reports
`blocked_policy` and spends nothing. With both, a persistent ledger holds back a
**200-credit demo reserve** that testing cannot cross and caps each run at 40
credits. Check the balance any time:

```bash
socmint credits
```

Two further limits apply to every call, because a metered source should return a
sample rather than a corpus:

| Limit | Default | Override |
|---|---|---|
| Posts kept per query | 10 | `--x-max-results N` |
| Seconds between billable calls | 30 | `--x-cooldown S` (0 disables) |

The cooldown is measured from the **last charge in the ledger**, not from process
state, so restarting the collector or re-running the command does not reset it.
A call refused by the cooldown costs nothing. `socmint credits` shows both
settings and how long the cooldown still has to run.

That reserve exists because Xpoz's free tier is a one-time grant of 500 credits
at 2 per query — there is no monthly reset. A prototype that spends freely while
being tested has no credits left on demo day.

Reddit's public JSON endpoints return a 403 bot-block from many networks; the
OAuth API is the usable surface. That was verified from the target network
before the adapter was written — the token endpoint answered with Reddit's own
401 JSON, while `www.reddit.com/r/<sub>/new.json` returned a block page.

Two of the social sources are **seeded** rather than keyword-indexed. Telegram
channels are discovered by web search first; Mastodon is read by hashtag. A
topic with no hashtag yields nothing from Mastodon, which is why cyber topics
(CVE identifiers, malware names) get little from it while conflict and disaster
topics do well.

**News is not social.** A Google News record has a publisher domain, not an
account. It cannot join a coordination group, and a headline that repeats the
topic is not a second account corroborating a social post. Both rules are
enforced in `models.NEWS_PLATFORMS` and asserted by tests, because counting a
press mention as independent support would silently upgrade an information grade.

### Why GDELT is off by default

GDELT's DOC API is free and keyless, and the adapter is included. But it enforces
one request every five seconds **per IP** with no `Retry-After`, and behind a
shared corporate NAT that quota is routinely already spent: every call returned
429 even after a minute idle, while Google News RSS answered immediately from the
same network. Enable it with `enable_gdelt=True` where the network allows it.

### Known limitations (disclosed, not hidden)

- A keyword search returns what the platform's search yields; date-filtering those
  results is **not** an exhaustive historical archive.
- Telegram's public preview only exists for channels that enable it.
- Telegram API/content terms restrict automated collection and AI/ML use without
  permission. This prototype demonstrates the technical pipeline; **it does not
  grant or imply usage rights**. Confirm terms with your organisation before
  operational use.
- YouTube developer policies restrict derived metrics and audiovisual copying.
  Unofficial extraction is not a permission grant either.

---

## Install

Requires Python 3.11+.

```bash
# with uv (recommended)
uv venv .venv
uv pip install --python .venv/bin/python -e ".[ml,ui,dev]"

# or with pip
python -m venv .venv && . .venv/bin/activate
pip install -e ".[ml,ui,dev]"
```

The ML extras pull CPU PyTorch and `sentence-transformers`. The first run
downloads `intfloat/multilingual-e5-small` (~120 MB) and caches it locally. No
API keys are needed.

To install CPU-only torch (smaller):

```bash
uv pip install --python .venv/bin/python --index-url https://download.pytorch.org/whl/cpu torch
uv pip install --python .venv/bin/python -e ".[ml,ui,dev]"
```

---

## Usage

### Collect and analyse

```bash
.venv/bin/socmint run \
  --topic "earthquake Philippines" \
  --start 2026-10-20 \
  --end 2026-10-31 \
  --case conflict_disinformation \
  --tlp TLP:AMBER \
  --export exports
```

Dates are inclusive local calendar days (`--tz`, default UTC) converted to a
half-open UTC window.

Useful flags:

| Flag | Effect |
|---|---|
| `--case <template>` | `conflict_disinformation`, `impersonation_deepfake`, `hacktivist_leak`, `custom` |
| `--tlp <marking>` | `TLP:CLEAR` … `TLP:RED`; defaults from the case template |
| `--alias "lindol"` | Add a literal search/relevance term (repeatable) |
| `--evidence` | Retrieve candidate evidence pages for claim candidates |
| `--offline-analysis` | Skip the embedding model; lexical grouping (marked degraded) |
| `--max-posts 200` | Lower the per-run post cap |
| `--no-claims` | Skip claim triage |

The case template changes the **analytic lens**, not the collection: it selects
which threat framings count as in-focus for the risk heuristic and attaches
analyst prompts. An unexpected topic runs the identical engine under `custom`.

### Demonstrate coordination detection (synthetic)

Live collection on a short window may legitimately contain **no** repeated
cross-account coordination, which leaves the detector undemonstrated. This command
loads a clearly-labelled fictional scenario:

```bash
.venv/bin/socmint demo --export exports
```

Output is tagged `source: synthetic_fixture` in the manifest and the console says
so explicitly. It demonstrates the detector firing on repeated, time-aligned,
near-identical posting, and separating a rumour narrative from a denial
narrative. **Never mix it into a live evidence set.**

### Publish a case file (Cloudflare Pages / Vercel)

Collection needs a long-lived Python process — `yt-dlp`, PyTorch, SQLite, and
runs measured in minutes — so it cannot run on Cloudflare or Vercel. The
**output** can. One command turns a stored run into a single self-contained HTML
file with no server, no build step, and no network at view time:

```bash
.venv/bin/socmint export-site --run <run-id> --out dist/case
wrangler pages deploy dist/case          # Cloudflare Pages
vercel deploy dist/case --prod           # Vercel
```

`index.html` is ~76 KB: CSS and JS inlined, no fonts, no CDN, no network at view
time. It carries the whole case — assessment, impersonation signals, indicators
with roles, narratives, coordination, claim review, spread, coverage, and the
evidence table as an appendix — plus a filter box that searches values, claims
and posts together. Post text is escaped on the way in, so untrusted content
never becomes markup. A print stylesheet turns it into a legible black-on-white
document for PDF. An existing case file is never overwritten.

If you have no Vercel account, `vercel deploy dist/case --temporary` publishes a
working link without logging in, and prints a claim URL to keep it.

### Inspect stored runs

```bash
.venv/bin/socmint list
.venv/bin/socmint show <run-id>
```

### Dashboard

```bash
.venv/bin/streamlit run app.py
```

Serves at <http://127.0.0.1:8511>, loopback only (set in
`.streamlit/config.toml`).

**Overview** is the default tab and answers the whole case on one screen:
triage risk and the counts, a stacked daily volume chart by platform, the main
narratives with volume and first-observed plus a trend sparkline, the account
groups with the signal behind each, where each narrative was first seen, and how
it spread. Its last section states what the page cannot show — account creation
dates, reused profile photos and amplification edges are not available from
these public surfaces.

The remaining tabs are the detail behind that summary: Assessment, Indicators,
Narratives, Coordination, Evidence, Coverage, Claims, Spread.

### Export layout

```
exports/<run-id>/
  manifest.json            # schema, statuses, coverage, TLP, risk, caveats
  results.json             # full nested analysis (authoritative)
  assessment.json          # case assessment: judgements, risk factors, caveats
  posts.csv                # one row per post, incl. Admiralty columns
  narratives.csv           # narrative candidates + threat framing
  narrative_members.csv    # post -> narrative membership
  coordination_groups.csv  # account groups + limitations
  coordination_episodes.csv# the evidence for each group
  propagation_edges.csv    # explicit vs inferred
  earliest_observed.csv    # per-narrative earliest observed
  claims.csv               # claim queue + verdicts
  indicators.csv           # technical values + role (indicator/reference/unknown)
  impersonation.csv        # brand-abuse signals, one row per signal
```

Every value in `indicators.csv` carries a **role**. `indicator` means the post
presents it as an artefact of compromise; `reference` means it is cited
documentation, a vendor advisory, or a research outlet; `unknown` means the post
gives no signal. Only indicators feed the risk score, and references are hidden
by default in the dashboard.

A search for a vulnerability name returns posts that cite `php.net` and vendor
blogs — those are not IOCs. Neither are the platform a post was published on, a
creator's Patreon, or a Google Form: infrastructure is dropped before
classification. On a live `php2shell` collection this takes 195 extracted values
down to 126, with 88 correctly identified as references and no false indicators.

CSV cells are sanitised against spreadsheet formula injection. Post text is
untrusted data and is rendered as text, never as HTML.

---

## How the analysis works

### Narratives
Posts are embedded with a multilingual model (`multilingual-e5-small`, 384-dim,
100 languages). Because raw e5 cosine scores overlap between related and
unrelated text, embeddings are **centred on the batch mean** before clustering;
this widens the gap so one threshold separates them. Average-linkage cosine
clustering avoids the chaining that connected components suffer from.

Clusters are **topic/framing candidates**. Shared vocabulary is not shared
stance, and opposing posts often cluster together. Labels are extractive
keywords from member text — nothing is generated.

If the model is unavailable, the pipeline falls back to TF-IDF and marks the run
`degraded`; cross-language grouping is then explicitly not claimed.

### Indicators
Values are extracted twice — as written, and with `hxxp` / `[.]` / `[@]` reversed
— then classified into a **role**: indicator, reference, or unknown. The rule
that fired is recorded for each. Only indicators feed the risk score.

### Admiralty grading
Every post gets a provisional A–F (source) / 1–6 (information) rating with the
rule that produced it. Identical text across accounts counts as coordination,
**not** corroboration; clusters spanning most of the corpus are excluded so a
catch-all cannot upgrade everything to "probably true".

### Coordination
A conservative subset of Pacheco et al. (ICWSM 2021):

- features: canonicalised shared URLs, identical normalised text
- require **≥ 2 independent episodes** per account pair
- require **time proximity** (default 15 min) *and* content similarity
- discount features used by more than 25% of accounts (generic links/boilerplate)
- report every supporting episode; connected components only group, they do not
  imply every member coordinated with every other
- compare against an **activity-preserving time-shuffle baseline**

Groups carry benign explanations by default, because coordination is frequently
legitimate (newsrooms, advocacy, fan communities).

### Propagation
Explicit edges (platform-stated reply/forward) are separated from inferred edges
(identical text + chronology). Inferred edges are associations, not transmission.

### Claims
Automation proposes checkable sentences and can retrieve candidate evidence. The
verdict field starts `unreviewed` and only changes when a human records one.

---

## Development

```bash
.venv/bin/ruff check src tests
.venv/bin/pytest -q
.venv/bin/pytest -q --cov=src/socmint --cov-report=term-missing
.venv/bin/pytest -q -m network   # live checks, opt-in
```

Tests are offline by default and use fixtures. Live network tests are marked.

---

## Project layout

```
src/socmint/
  models.py        # Pydantic contracts (facts vs analysis vs review)
  settings.py      # budgets and analysis thresholds
  security.py      # URL allowlist, SSRF guard, safe fetch, CSV escaping
  store.py         # SQLite persistence; reviews survive re-analysis
  pipeline.py      # orchestration + coverage summary
  cli.py           # argparse entry point
  exports.py       # JSON/CSV writers
  text.py          # normalisation, language detection, shingles, keywords
  collection/
    discovery.py   # topic -> candidate channels (DDGS)
    youtube.py     # yt-dlp adapter
    telegram.py    # t.me/s parser + pagination
    mastodon.py    # public tag timelines, federated dedupe by uri
    reddit.py      # OAuth client-credentials search
    xpoz.py        # X via Xpoz SDK, gated by the credit ledger
    news_rss.py    # Google News RSS search
    gdelt.py       # GDELT DOC 2.0 (optional, off by default)
  analysis/
    narratives.py  # embeddings, centring, clustering
    coordination.py# episodes, discounting, baseline
    propagation.py # explicit/inferred edges, earliest observed
    claims.py      # claim triage + evidence retrieval
    indicators.py  # IOC extraction + defanging
    grading.py     # provisional Admiralty grading
    threat.py      # threat framing + impersonation/brand abuse
    assessment.py  # key judgements + itemised risk score
  presets.py       # case templates (lens, not engine)
  spend.py         # credit ledger + spend policy for metered APIs
app.py             # Streamlit dashboard
```

---

## Accuracy and evaluation

**Verified against ground truth:** no. This prototype has not been validated for
coordination precision on live data, because no labelled ground truth exists for
the sampled topics.

**What is actually measured:**

- 216 offline tests, 84% statement coverage.
- Behavioural tests: coordination fires on repeated near-identical posting and
  does **not** fire on an organic breaking-news burst, on a generic shared link,
  on same-account reposts, or on undated posts.
- Window handling: half-open UTC boundaries, inclusive local dates, undated posts
  excluded from timing evidence.
- Failure handling: 429 surfaces as `rate_limited`; a failed platform makes the
  run `partial`; an empty result is distinct from a failed one.
- CTI-layer guards: a catch-all cluster cannot upgrade Admiralty information
  grades; impersonation severity counts distinct signal kinds rather than
  repetitions; a single brand word does not flag an unrelated domain; every
  extracted indicator is defanged on output.

### Live-run results (2026-11-03)

| Topic | YouTube | Telegram | Narratives | Coordination |
|---|---|---|---|---|
| earthquake Philippines | 20 | 10 | 2 | 0 |
| typhoon Philippines | 18 | 0 | 2 | 0 |
| Mayon volcano | 17 | 2 | 2 | 0 |
| West Philippine Sea | 18 | 0 | 1 | 0 |

Zero coordination groups on live data is a **correct negative**. In the
earthquake sample, peak cross-account similarity was 0.313 against a 0.45
threshold — the two Telegram accounts were covering *different* earthquakes. The
tool did not cry wolf.

No precision/recall figure is claimed. Treat groups as leads.

## Documentation

| Document | What it answers |
|---|---|
| [`docs/purpose.md`](docs/purpose.md) | What SOCMINT is for and what problem this solves |
| [`docs/decisions.md`](docs/decisions.md) | Why each design choice was made, and what was rejected |
| [`docs/validation.md`](docs/validation.md) | How the output was checked, what was found wrong, what remains unverified |
| [`docs/methodology.md`](docs/methodology.md) | What is computed at each stage, including the CTI layer |
| [`docs/access-and-limitations.md`](docs/access-and-limitations.md) | Platform terms, deliberate refusals, coverage and accuracy limits |
| [`docs/demo-script.md`](docs/demo-script.md) | 40-minute walkthrough runbook with likely questions |

## Known weaknesses

1. **Telegram discovery is the weakest link.** Web-search discovery is noisy and
   non-deterministic; the same query returns different channels on different
   calls. Some runs collect little or nothing from Telegram.
2. **Mastodon only works if the topic has a hashtag.** It is tag-seeded, not
   keyword-indexed, so a CVE identifier or a malware name will usually return
   nothing. That is a property of the access path, not a bug to fix.
3. **Strict relevance.** A post must contain every significant topic word. This
   favours precision and misses paraphrases and cross-language mentions.
4. **No recall estimate.** The module cannot say what fraction of platform
   activity it captured.
5. **Coordination needs volume.** With two accounts and a 15-minute window,
   organic coordination is rarely observed.
6. **No X coverage.** There is no free automated read path left; every remaining
   route is metered. See `docs/decisions.md` §1.

---

## Licence

Code: MIT. The data it collects remains subject to each platform's terms. Do not
redistribute collected content without checking those terms.
