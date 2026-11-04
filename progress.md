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

- [x] 161 tests passing, `ruff` clean
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
- GDELT is IP-rate-limited and disabled by default; Google News is the news path.
- No ground-truth evaluation of coordination precision on live data.
- Platform terms restrict automated collection/AI use; see docs/access-and-limitations.md.

## Sources added after the initial build

Mastodon and Google News, both free and keyless, plus a GDELT adapter left
disabled by default.

- Mastodon federates: the same status has a different local `id` on each
  instance, so identity is keyed on `uri`. Without that, four instances return
  one post four times.
- News records are in `models.NEWS_PLATFORMS` and are excluded from coordination
  and from account-based corroboration. A press mention is not a second account.
- Tests are offline by default via an autouse fixture in `conftest.py` that
  blocks the fetch entry point in every collector. Adding platforms had silently
  turned `pipeline.collect` into a live-network call inside the suite.
