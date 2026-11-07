# SOCMINT Module — Progress

Deadline: 5 days from 2026-11-03. Target: demonstrable **cyber-threat-intelligence**
SOCMINT module — topic tracking, multilingual narrative grouping with threat
framing, coordination candidates, extracted indicators, impersonation signals,
propagation, claim review, and a case assessment.

## Day 1 — Feasibility gate: PASSED

| Check | Result | Evidence |
|---|---|---|
| Network reachability | PASS | t.me 200, youtube 200, duckduckgo 302 |
| YouTube discovery (yt-dlp `ytsearch`) | PASS | keyword search works anonymously |
| YouTube full metadata | PASS | upload_date, timestamp, views, likes, comments, description, thumbnail, language, channel_id |
| YouTube date operators | PASS | `after:`/`before:` → 20/20 in-window (vs 0–2 unbounded) |
| Free topic discovery (DDGS) | PASS | finds t.me channels; noisy and non-deterministic |
| Telegram public channel parse | PASS | ISO-8601 `datetime`, text, permalink, views, photo count |
| Telegram pagination `?before=` | PASS | paged back to 2026-03-30 |
| Telegram within-channel `?q=` | PASS | keyword search inside channel works |
| Multilingual model on CPU | PASS | e5-small loads in ~33s, encodes 5 texts in 0.5s |

**Gate outcome: 2/2 platforms technically usable anonymously.**

## Day 2 — Collection + evidence: DONE

- YouTube: date-bounded queries, full metadata, topic inheritance from query.
- Telegram: discovery → channel paging → post-level relevance → early abort.
- Relevance: literal all-words matching (precision-first, documented).
- Provenance, dedupe, missing-data flags, coverage summary.

### Bugs found and fixed (root cause, not patch)
1. `safe_get` rebuilt an `httpx.Response` from decoded bytes → httpx re-applied
   `Content-Encoding` → decompression error. Replaced with a `FetchResult` value object.
2. DDGS "No results found." was counted as a failure. Now an empty result.
3. YouTube unbounded search returned 0 in-window results → added `after:`/`before:`.
4. Discovery kept every search hit → irrelevant channels burned the budget.
5. Channel abort counted *in-window* posts instead of *topic matches* → valid
   channels discarded when matching posts fell outside the window.
6. 429 was logged as `failed`, so `rate_limited` status was never surfaced.
7. Centring identical vectors produced zero vectors → cosine clustering raised.
8. `AnalysisBundle` has `method_notes`, not `notes`.

## Day 3 — Analysis: DONE

- Narratives: multilingual e5 embeddings, batch-mean centring (raw e5 scores
  overlap; centring separates related from unrelated), average-linkage cosine
  clustering, corpus-relative distinctive labels.
- Coordination: shared URL / near-duplicate text / semantic similarity + time
  proximity, ≥2 independent episodes, popularity discounting, activity-preserving
  time-shuffle baseline (averaged over 5 shuffles).
- Propagation: explicit vs inferred edges, earliest observed (never "origin").
- Claims: strict extraction (factual verb **and** figure), boilerplate excluded,
  defaults to `unreviewed`.

## Day 4 — Analyst workflow: DONE

- Streamlit dashboard: coverage, narratives, coordination, spread, claims, evidence.
- Reviewer decisions persist in SQLite and survive re-analysis.
- Export: JSON + 8 CSV tables, formula-injection safe, never overwrites.
- Security: host allowlist, SSRF guard, size caps, safe rendering.

## Day 5b — Cyber-threat-intelligence reframe: DONE

The brief is a threat-intelligence task, so the module was extended from a
generic topic tracker into a SOCMINT case workbench. Three priority use cases:
conflict disinformation, impersonation/deepfake, hacktivist data leak.

- [x] Models: `TLP`, `CaseTemplate`, `ThreatFraming`, `IndicatorKind`,
      `AdmiraltySource`/`AdmiraltyInfo`/`AdmiraltyRating`, `Indicator`,
      `ImpersonationSignal`/`ImpersonationFinding`, `KeyJudgement`, `Assessment`
- [x] `analysis/indicators.py` — IOC extraction + defanging at the model boundary
- [x] `analysis/grading.py` — provisional Admiralty grading with catch-all guard
- [x] `analysis/threat.py` — framing classifier + impersonation/lookalike detection
- [x] `analysis/assessment.py` — key judgements + itemised additive risk score
- [x] `presets.py` — four case templates that change the lens, not the engine
- [x] Wired into `pipeline.analyse`; `Run` carries case template + TLP
- [x] Exports: `assessment.json`, `indicators.csv`, `impersonation.csv`,
      Admiralty columns on `posts.csv`, framing on `narratives.csv` (schema 2.0)
- [x] CLI `--case` / `--tlp`; demo runs under `impersonation_deepfake`
- [x] Dashboard rebuilt: deep navy + crimson, terminal masthead, TLP banner,
      Assessment / Indicators tabs, Admiralty badges, framing chips
- [x] Docs: `decisions.md`, `validation.md`, `demo-script.md`; methodology §8

### Bugs found while wiring the CTI layer
1. Information grade counted coordination as corroboration → skip catch-all
   clusters (>60% of corpus); identical text is coordination, not support.
2. Impersonation severity summed repetitions → now counts distinct signal kinds.
3. Brand tokens never matched (`topic_terms` returns the phrase) → split into
   compacted phrase + individual words.
4. "Mentions the brand" signal fired on the whole corpus → removed.
5. Streamlit `@st.cache_resource` SQLite cross-thread error → `check_same_thread=False`
   plus a real lock.
6. **Every mentioned domain reported as an indicator.** A `php2shell` search put
   `php.net`, `huntress.com` and a researcher's blog on the IOC list. Added an
   ordered role classifier (indicator / reference / unknown) with a recorded
   reason; only indicators feed the risk score.
7. The role classifier then misclassified the demo's own phishing domain because
   `verify` sat in the benign-language pattern → added a credential-lure rule that
   outranks citation language.
8. Author-defanged values (`evil[.]com`) were recorded but never extracted, since
   a domain regex cannot see `[.]` → extraction now runs on a normalised copy too.

## Day 5 — Verification: COMPLETE

- [x] 216 tests passing, `ruff` clean
- [x] Statement coverage measured at 86%
- [x] README, `docs/methodology.md`, `docs/access-and-limitations.md` written
- [x] `docs/decisions.md`, `docs/validation.md`, `docs/demo-script.md` written
- [x] Live runs on 4 topics; 1 live run + 1 synthetic run retained in the DB
- [x] Dashboard verified in a browser (Playwright) across Assessment, Indicators,
      Narratives and Coordination tabs — renders, theme applied, no console errors
- [x] Export verified: 13 artefacts per run, formula-injection safe, IOCs defanged

### How to run
```bash
.venv/bin/socmint run --topic "Acme Bank collapse" --start 2026-10-20 --end 2026-11-02 \
  --case impersonation_deepfake --export exports
.venv/bin/socmint demo --export exports      # labelled SYNTHETIC coordination scenario
.venv/bin/streamlit run app.py               # http://127.0.0.1:8511
.venv/bin/pytest -q
```

## Honest findings from live runs

| Topic | YouTube | Telegram | Narratives | Coordination |
|---|---|---|---|---|
| earthquake Philippines | 20 | 10 | 2 | 0 |
| typhoon Philippines | 18 | 0 | 2 | 0 |
| Mayon volcano | 17 | 2 | 2 | 0 |
| West Philippine Sea | 18 | 0 | 1 | 0 |

**Zero coordination groups on live data is a correct negative, not a failure.**
Measured cross-account similarity in the earthquake sample peaked at 0.313
against a 0.45 threshold — the two Telegram accounts were covering *different*
earthquakes. The detector is demonstrated instead on a clearly-labelled synthetic
scenario (`socmint demo`), where the observed overlap (3 pairs) exceeds the
time-shuffle baseline (0.2).

Telegram coverage is the weak point: web-search discovery is noisy and
non-deterministic, and the public preview only shows recent posts.

## Open risks
- Telegram discovery quality varies per run.
- Mastodon is hashtag-seeded: no tag, no coverage. Cyber topics get little.
- Reddit needs a free OAuth app; without one it reports `unsupported`.
- GDELT is IP-rate-limited and disabled by default; Google News is the news path.
- No ground-truth evaluation of coordination precision on live data.
- Platform terms restrict automated collection/AI use; see docs/access-and-limitations.md.

## Sources added after the initial build

Mastodon and Google News, both free and keyless, plus Reddit (free OAuth app)
and a GDELT adapter left disabled by default.

- Mastodon federates: the same status has a different local `id` on each
  instance, so identity is keyed on `uri`. Without that, four instances return
  one post four times.
- Reddit's public JSON is bot-blocked from this network while the OAuth
  token endpoint answers; the credential is read from the environment and
  `.env` is gitignored.
- News records are in `models.NEWS_PLATFORMS` and are excluded from coordination
  and from account-based corroboration. A press mention is not a second account.
- Tests are offline by default via an autouse fixture in `conftest.py` that
  blocks the fetch entry point in every collector. Adding platforms had silently
  turned `pipeline.collect` into a live-network call inside the suite.

## Dashboard pass — demo-first ordering

The dashboard is the artifact an assessor actually looks at, so it was reduced
to what a reviewer needs rather than what the pipeline happens to produce.

- Tabs run Assessment → Indicators → Narratives → Coordination → Evidence →
  Coverage → Claims → Spread: the two that answer "should I care" first, the two
  that answer "can I trust this" before the follow-up work.
- The sidebar form is Topic + start + end + Run. Aliases, case template, TLP,
  timezone, post cap, lexical-only, and evidence retrieval moved into one
  `Advanced` expander; every one of them has a defensible default.
- Streamlit's own toolbar is hidden (`[client] toolbarMode = "minimal"`) and the
  sidebar starts `auto`, so it collapses on a narrow screen instead of eating
  half the viewport.
- Stored-run labels lead with the topic; the run id is a timestamp nobody reads
  and truncating it costs nothing.
- A bool column rendered as empty checkboxes ("truncated"), which reads as
  unfilled; it is now the same ✓/— as the evidence table.
- The stylesheet was targeting BaseWeb selectors that Streamlit 1.66 no longer
  emits, so tab and chip styling had silently stopped applying. See
  `docs/validation.md` #31.

Verified with Playwright at 1440 and 768 across Assessment, Indicators,
Coverage, and Evidence. 216 tests pass; ruff clean.

## Published case file — same design, whole case

`socmint export-site` is the artifact that actually gets hosted: one 76 KB
`index.html` with inline CSS/JS and no network at view time, deployable to
Cloudflare Pages or Vercel with no build step. It had drifted from the dashboard
— a different palette and three sections missing.

- Rewritten against the dashboard's tokens: same navy, crimson signal colour,
  mono for machine-generated values, terminal prompt in the masthead, stat tiles
  with the crimson rule, key judgements with confidence chips.
- Added the sections a published case needs and the old file did not have:
  impersonation signals, claim review, spread (earliest observed plus
  propagation edges), method notes, and a degraded-analysis banner.
- Section order is document order, not dashboard order: evidence is an appendix
  at the end rather than the fifth tab.
- One filter box searches values, claims and posts together; each result group
  carries its own `data-search` haystack so a row and its reason line hide as a
  unit.
- Repeated boilerplate is suppressed. Every `unknown` indicator carried the
  identical "no signal either way in the post text", and every unreviewed claim
  carried the identical auto-proposal rationale — twenty copies of one sentence
  is noise, and the badge or section note already says it.
- Added a print stylesheet: black on white, page-break rules, filter hidden. The
  case file is a document and someone will print it to PDF.

Verified by exporting a live run and reading it in a browser at 1440: all ten
sections render, the filter takes 43 rows to 3 on "gmanews", no external
resource requests (the only console error is a missing favicon). 216 tests pass;
ruff clean.

## X is capped per query and rate-limited

The credit ledger bounded what a *run* could spend, but not what a single query
could return: `limit` was the run's post budget (500 by default), so one call
could pull hundreds of tweets. On a source where the free tier is a one-time
grant, that is the wrong shape — the question a query answers is "is this being
talked about", not "give me everything".

- `CreditPolicy.max_results_per_query` (default 10) caps both what is asked for
  and what is kept. The request is also clamped by the run's post budget, so the
  lower of the two wins.
- `CreditPolicy.min_seconds_between_calls` (default 30s) gates billable calls.
  It is measured from the **last charge in the ledger**, not from process state,
  so restarting the collector does not reset it. A refused call costs nothing —
  the gate runs before the charge.
- Both are exposed as `--x-max-results` and `--x-cooldown`, and `socmint credits`
  prints them along with the seconds left on the current cooldown.

The result cap is enforced twice: once in the request, and again on the parsed
posts, because the API has returned more than asked before and an
off-topic-heavy page would otherwise exceed the run's own stated ceiling.

## Overview tab, and the narrative bug behind it

The dashboard had eight detail tabs and no answer to "what does this case say".
Worse, on a real 527-post run the Narratives tab showed a single card: the
clustering threshold merged all 522 in-scope posts into one cluster.

**Segmentation now adapts.** `_segment` checks the result and, while one cluster
holds more than 60% of the corpus, tightens the threshold and re-clusters. On
the same data that moved -0.15 → +0.05 centred cosine and produced 14 clusters,
the largest holding 18%. A fixed constant cannot survive a change of corpus size
in either direction; the self-check can.

**Overview is now the first tab and the default read.** Triage risk and counts;
a stacked daily volume chart by platform; the main narratives with volume,
first-observed and a trend sparkline; account groups with the signal behind
each; where each narrative was first seen; how it spread; and a closing section
naming what the page cannot show.

**Evidence shows media.** 201 of the 527 posts carry a photo or video; the table
now says which, per row, instead of silently dropping it.

**`.env` is loaded by both entry points.** It never was: the CLI only worked
because the key happened to be exported in that shell, and the dashboard
reported X as `unsupported`. Both now read `.env`, an exported variable still
wins, and the sidebar warns before a run rather than after it.

Verified on the `binance hack` run: 16 narratives, 2 account groups, 16 first
observations, 25 claims, 222 indicators, chart and sparklines rendering at 1600
wide. 216 tests pass; ruff clean.

---

## UI/UX professionalism pass

**Internal decisions are not content.** The masthead caption ("Topic-driven
collection. Anonymous, budgeted, evidence-first."), the word "metered", and the
"What this page does not show" block explained the tool's design to the reader.
A reader wants the finding, not the reasoning behind the interface. Removed from
both surfaces; the reasoning lives in `docs/methodology.md`, which is where
someone who wants it should look.

**The Overview is a written correlation, not a card wall.** `analysis/brief.py`
assembles a brief — Situation, What is being said, Account activity, Technical
indicators, Claims, Collection — from the stored bundle. It is deterministic:
no model summarises anything, and every sentence traces to a record. The same
brief renders in the dashboard Overview tab and at the top of the published case
file, so the two cannot drift.

**`post(s)` is a format string, not copy.** Twenty-odd analyst-facing strings
rendered counts as `3 post(s)`, `1 episode(s)`, `2 account group(s)`. A reader
notices that the way they notice a stray `</div>`. `socmint/prose.py` now owns
noun agreement (`1 post` / `3 posts`), verb agreement (`1 post was` / `3 posts
were`), reader-facing kind names (`telegram_handle` → `Telegram handle`) and
platform proper nouns (`google_news` → `Google News`). The assessment also sums
framings instead of listing them per narrative, so three hacktivist clusters read
as `hacktivism (375 posts)` rather than the same word three times.

**`--dim` failed contrast.** The token used for every small label — axis dates,
figcaptions, table headers, card IDs — sat at 3.38:1 on the page background.
That is why the chart's date labels looked clipped: the lower half of each glyph
was blending into the dark. Raised to 4.84:1. `--crimson` as small mono text was
4.42:1 and moved to 4.68:1. Both now clear AA.

**The volume chart carries dates.** Three labels only — first, peak, last. A
label under every bar turns a sparkline into a table the reader has to decode.

**`socmint reanalyse`.** The stored bundle holds rendered judgement strings, so a
copy fix could only be seen by collecting again — which spends credits. Analysis
is derived from stored posts, so `reanalyse --run <id>` rebuilds it without
touching a collector. Posts are untouched and analyst reviews are kept.

Verified on the `binance hack` run: 0 occurrences of `post(s)` in the exported
case file, 0 raw storage keys in rendered output, chart labels legible at 1440
wide. 227 tests pass, 84% coverage, ruff clean.
