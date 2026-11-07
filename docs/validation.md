# How the output was checked

This document records what I actually verified, what I found wrong, and what
remains unverified. It is deliberately organised around the errors rather than
the successes, because the errors are the evidence that checking happened.

Three layers of checking, in increasing order of how much they cost:

| Layer | What it catches | Cost |
|---|---|---|
| Automated tests | Contract violations, parser edge cases, failure paths | Cheap, runs in CI |
| Hand-reading live output | Wrong-but-plausible results the tests cannot see | Expensive, done once per sample |
| Labelled synthetic fixture | Detector behaviour where a real positive does not exist | Built once, reused |

---

## 1. Automated tests

**227 tests, all passing, 84% statement coverage** across `src/socmint`
(`pytest --cov=src/socmint --cov-report=term-missing`).

Coverage on the cyber-threat-intelligence modules specifically: `presets.py`
100%, `prose.py` 100%, `models.py` 99%, `assessment.py` 97%, `grading.py` 97%,
`threat.py` 94%, `indicators.py` 85%.

| Area | What is asserted |
|---|---|
| Text normalisation | Tokenisation, hashtags, shingles, Jaccard, language detection |
| Collection parsing | Telegram page parsing, YouTube entry mapping, timestamp precision |
| Discovery | Query generation, date-bound operators, relevance filtering, channel cap |
| Collection flow | Pagination, early abort, rate-limit status mapping, empty results |
| Coordination | Episode construction, independence rule, popularity discounting, baseline |
| Analysis | Centring, degenerate vectors, clustering, labelling, framing, grading |
| Security | SSRF guard, host allowlist, size cap, CSV formula injection |
| Pipeline | Dedupe, language enrichment, in-scope filtering, end-to-end wiring |
| Exports + demo | Artefact set, no-overwrite, Admiralty columns, assessment output |
| CLI | Date parsing (inclusive local day → half-open UTC), invalid ranges |
| Presentation copy | Noun and verb agreement, storage keys rendered as reader-facing labels |

What the tests deliberately do **not** cover: live network behaviour. Network
tests are marked and skipped by default, because a flaky external dependency in
the suite trains you to ignore failures.

One test violated that rule and was fixed rather than tolerated.
`test_allowed_host_accepted` asserted that `https://t.me/s/example` passed the
SSRF guard, but `validate_public_url` resolves DNS, and behind corporate DNS
`t.me` resolves to a private address — so the test failed for environmental
reasons while the code was behaving correctly. The guard's fail-closed behaviour
is right; a unit test that depends on DNS is not. Resolution is now injectable,
the allowlist logic is tested offline, and two new tests assert the two cases
that actually matter: an allowlisted host resolving inside the network is still
rejected, and a DNS failure fails closed.

---

## 2. Threshold calibration

The clustering threshold is the one number that most affects output quality, so
it was measured rather than guessed.

**Measurement.** Encode pairs of posts from a live sample and record raw e5
cosine similarity:

| Pair type | Raw cosine |
|---|---|
| Same event, paraphrased | ≈ 0.87 |
| Unrelated posts on the same topic | ≈ 0.76 |

A threshold between 0.87 and 0.76 is a 0.11-wide window on a metric that shifts
with corpus composition. That is not a defensible cut.

**Fix.** Subtract the batch mean from every embedding before clustering. This
removes the shared "social post about news" direction that inflates all pairwise
similarity, and opens the gap. The clustering threshold (`narrative_similarity =
-0.15`) is therefore expressed in **centred** cosine space, which is why the
number looks strange out of context.

**Coordination threshold** (`coordination_semantic_similarity = 0.45`) was set
above the measured live peak of 0.313 for unrelated accounts, so that
same-topic-but-different-subject pairs do not link.

---

## 3. Hand-checking live runs

Four topics were run live and the output read end to end.

| Topic | YouTube | Telegram | Narratives | Coordination |
|---|---|---|---|---|
| earthquake Philippines | 20 | 10 | 2 | 0 |
| typhoon Philippines | 18 | 0 | 2 | 0 |
| Mayon volcano | 17 | 2 | 2 | 0 |
| West Philippine Sea | 18 | 0 | 1 | 0 |

**The zero coordination results are correct negatives, not failures.** For the
earthquake sample I inspected the account pairs directly: measured peak
cross-account similarity was **0.313** against a **0.45** threshold, because the
two Telegram accounts discovered were covering *different* earthquakes. Linking
them would have been a false positive.

That left the detector undemonstrated, so a labelled synthetic scenario was built
to exercise it (see §4). Reporting "0 on live data, demonstrated on a fixture"
is the honest outcome. Reporting "3 coordinated groups found" by lowering the
threshold until something fired would not have been.

**What hand-reading found that tests could not:**

- Telegram discovery is non-deterministic — the same query returns different
  channels across calls. Documented as a coverage weakness, not fixed, because it
  is a property of the search backend.
- Relevance matching is precision-first (all significant words must appear). It
  misses paraphrases. This was a deliberate trade: recall loss is visible in the
  coverage numbers, whereas false inclusion is invisible once a post is in the
  corpus.

---

## 4. The labelled synthetic fixture

`src/socmint/fixtures/coordinated_demo.json` — a fictional "Acme Bank collapse"
scenario, 28 posts, three accounts posting near-identical text minutes apart,
plus varied filler posts and a denial narrative.

Purpose: create a **known positive** so the detector's firing can be inspected,
and so the CTI layer has something to extract from.

| Check | Result |
|---|---|
| Coordination groups detected | 1 (3 accounts, 6 matched episodes) |
| Time-shuffle baseline | 0.0 qualifying pairs by chance |
| Indicators extracted | 4 (domain, SHA256, wallet, Telegram handle) |
| Impersonation signals | 4 distinct kinds, high severity, medium confidence |
| Lookalike domain | `acme-bank-secure.com` flagged via two brand words |
| Risk score | 60/100, itemised across three factors |

Every artefact from this fixture is tagged `synthetic_fixture` in the run config
and manifest, and the CLI prints a warning line before the summary. It is never
mixed into a live evidence set.

The fixture is labelled synthetic **because a real coordinated campaign cannot be
manufactured and attributed to real people for a demo.** Using it to prove
implementation behaviour is legitimate; using it to claim real-world detection
accuracy would not be.

---

## 5. Bugs that checking found

These are the substantive corrections made during the build. Each one was found
by looking at output that was wrong in a way the tests did not catch.

### Collection

1. **`safe_get` re-decompressed decoded bytes.** Rebuilding an `httpx.Response`
   from already-decoded content made httpx re-apply `Content-Encoding: gzip`,
   raising `DecodingError`. Fixed by returning a `FetchResult` value object
   instead of reconstructing a response.
2. **DDGS "No results found." counted as a failure.** A normal empty result was
   inflating the error count. Now mapped to `StageStatus.EMPTY`.
3. **YouTube returned 0 in-window posts.** Keyword search ranks by relevance, not
   date, so a two-week window yielded nothing. Fixed with `after:`/`before:`
   operators — 20/20 in-window versus 0–2 unbounded.
4. **Discovery kept every search hit.** Irrelevant channels consumed retrieval
   budget. Fixed with a word-hit relevance filter before the budget is spent.
5. **Channel abort counted in-window posts instead of topic matches.** A valid
   news channel was discarded because its matching posts fell outside the window.
6. **429 was logged as `failed`,** so `rate_limited` never surfaced. Status codes
   are now mapped explicitly.

### Analysis

7. **Centring identical vectors produced zero vectors,** and cosine clustering
   raised `ValueError`. Fixed with a degenerate-row fallback to the original
   direction.
8. **Information grade counted coordination as corroboration.** My first
   implementation graded information by counting accounts inside a cluster. That
   is wrong twice: identical text across accounts is coordination, not
   independent support; and a cluster spanning most of the corpus upgrades every
   post for free. Fixed by skipping catch-all clusters (>60% of the corpus) and
   treating identical text as a coordination signal. After the fix, the synthetic
   demo correctly grades every post **6** — cannot be judged — because its only
   cluster is a catch-all.
9. **Impersonation severity summed repetitions.** Six posts saying "urgent"
   scored six times, letting a noisy corpus outrank a targeted campaign. Fixed:
   severity counts *distinct signal kinds*, not per-post occurrences.
10. **Brand tokens never matched.** `topic_terms("Acme Bank collapse")` returns
    the whole phrase, so the lookalike comparison ran against
    `"acmebankcollapse"` and never matched `acme-bank-secure.com`. Fixed by
    splitting the topic into a compacted phrase (for typo distance) and
    individual words (for containment).
11. **A "mentions the brand" signal was emitting on the whole corpus,** inflating
    the distinct-kind count for zero information. Removed.

### Application

12. **Streamlit could not read its own database.** `@st.cache_resource` created
    the SQLite connection on one worker thread; the next render ran on another,
    raising `SQLite objects created in a thread can only be used in that same
    thread`. Fixed by opening with `check_same_thread=False` **and** serialising
    access with a lock — the thread check is replaced by real synchronisation,
    not simply switched off.
13. **Dark-on-dark headings.** Styling `.stApp` fought Streamlit's own theme.
    Fixed by configuring the theme in `.streamlit/config.toml` rather than
    overriding it in CSS.
14. **An SSRF test depended on live DNS.** `validate_public_url` resolves the
    hostname, so the unit test passed or failed with the machine's DNS. Fixed by
    making the resolver injectable; the guard still fails closed in production.
15. **Newsroom names with underscores never matched the grade rule.**
    `_NEWSROOM_PATTERN` uses `\b`, which does not break on `_`, so
    `manila_bulletin_news` was graded F instead of C. Fixed by normalising
    separators before matching.
16. **Telegram handles lost their sigil.** The regex captured the bare name, so
    the defanged form was `acmebank_press` instead of `[@]acmebank_press`. Fixed
    by storing the handle with its `@`.

### Indicators

17. **Every mentioned domain was reported as an indicator.** Found by running a
    `php2shell` search: `php.net`, `huntress.com` and a researcher's blog all
    appeared on the IOC list. The module had no concept of a *reference*. Fixed
    by adding an ordered role classifier (indicator / reference / unknown) that
    records which rule fired. See `docs/decisions.md` §10.
18. **The role classifier then misclassified the demo's own phishing domain.**
    `verify` was in the benign-language pattern (meaning "verify the checksum"),
    so *"verify your account at acme-bank-secure.com"* scored as a citation. Fixed
    by adding a credential-lure rule that outranks attribution language and
    removing `verify`/`integrity` from the benign pattern.
19. **Author-defanged values were recorded but never extracted.** `evil[.]com` is
    not a domain to a regex, so the strongest indicator signal produced no
    value at all. Fixed by running extraction a second time on a normalised copy
    of the text.
20. **The classifier's first version was still wrong on real data.** Re-running it
    over a live `php2shell` collection produced six "indicators": a coaching
    site's course pages, a Google Form, and a security vendor's own app. The
    cause was `click here` in the lure pattern — every YouTube description says
    it — plus a 220-character context window that swept marketing text into the
    threat context. Fixed by excluding service/infrastructure domains at
    extraction, narrowing the lure to credential and financial language, and
    cutting the window to 120 characters. Result on the same run: 195 values →
    126, six false indicators → zero, and 88 correctly classified references.

    This is the clearest example in the project of why hand-reading live output
    matters. Every automated test passed while the classifier was producing a
    list no analyst would trust.

### Verification environment

21. **A running dashboard serves the code it imported at start-up.** After fixing
    the classifier, the already-running Streamlit process still rendered the old
    numbers: Streamlit reloads `app.py` on each render but does not reload the
    imported `socmint` package. The dashboard has to be restarted after any
    change under `src/`. The same applies to stored runs — an analysis persisted
    before a classifier change keeps its old roles until it is re-analysed, which
    is why the live `php2shell` run in the demo database was re-analysed from its
    stored posts (no new collection) rather than left as a pre-fix snapshot.

    Consequence for the judgement text: with 88 references and no indicators the
    key judgement used to read `0 technical indicator(s) were extracted: none`,
    which reads like a failure rather than a finding. It now reads
    `No technical indicators were extracted; 88 cited reference(s) were
    classified as references and excluded from the indicator set`.

### Adding sources

22. **Federated copies of one Mastodon post counted as four posts.** A live
    "typhoon Philippines" collection returned the same status once per instance.
    The cause is that Mastodon's `id` is instance-local, so the same federated
    status has a different `id` on every instance that relays it. Fixed by
    keying identity on the canonical `uri`. Live result went from 4 posts (2
    unique) to 2.

23. **GDELT's 429 needed a backoff longer than the retry budget.** The published
    limit is one request every five seconds per IP with no `Retry-After`; a
    2-second backoff burned an attempt and still returned 429. The floor is now
    six seconds and grows per attempt. This did not save GDELT on a shared
    corporate NAT — every call still returned 429 after a minute idle, while
    Google News answered immediately from the same machine — which is why GDELT
    ships disabled by default.

24. **Google News returns zero items for a quoted phrase combined with a date
    operator.** `"typhoon Philippines" when:11d` and `"typhoon Philippines"
    after:... before:...` both returned an empty feed, while the unquoted forms
    returned 30–50 items. The failure is silent: it looks exactly like "no
    coverage". Fixed by building the query unquoted and letting the exact window
    be applied locally. Because an unquoted query is a loose AND, relevance is
    then decided per headline — on the live run that cut 49 retrieved articles
    to 7 that actually contain both topic words.

25. **Adding platforms silently turned the test suite into a live-network
    client.** `pipeline.collect` runs every enabled platform, and two tests in
    `test_cli.py` mocked only YouTube and Telegram. With Mastodon and Google
    News enabled by default, those tests began issuing real requests: the suite
    went from about five minutes to more than fifteen, and it would have failed
    on a machine without internet. Fixed with an autouse fixture in
    `conftest.py` that replaces the fetch entry point in every collector with
    one that raises, so a live call fails loudly instead of quietly slowing the
    suite. A test that wants a response still patches it in its own body, which
    runs after the fixture and wins.

26. **Reddit's public JSON is blocked but its OAuth API is not.** From this
    network `www.reddit.com/r/<sub>/new.json` returns a 403 bot-block page while
    `www.reddit.com/api/v1/access_token` returns Reddit's own
    `{"message":"Unauthorized","error":401}`. That difference is what made the
    adapter worth writing: the protocol is reachable, only the unauthenticated
    surface is blocked. The exchange was exercised against the live endpoint
    with deliberately wrong credentials before the adapter was trusted — it
    returned a clean 401, so the code path is real rather than mocked.

27. **`.env` was not in `.gitignore`.** Adding `.env.example` to document the
    Reddit credentials created the exact situation where someone copies it to
    `.env`, fills in a real secret, and commits it. `.env` is now ignored and
    `.env.example` is not, which was verified with `git check-ignore` rather
    than assumed.


### Metered spend

28. **The ledger and the collector could disagree about the policy.** The first
    version passed a `CreditPolicy` into the collector while the ledger held its
    own copy. The test for "spending is disabled" failed because the ledger had
    been built with `allow_spend=True` and its copy was the one that decided. Two
    copies of a spend policy is a bug waiting to be a surprise, so the parameter
    was removed: the ledger owns the policy and is the only thing consulted.

29. **Charging after a successful call under-counts.** If the process dies
    between the request and the ledger write, the credits are gone from the
    account but absent from the ledger — the one direction that overspends. The
    collector now charges *before* the call and appends a refund if the call
    fails. The refund is a negative entry, not a deletion, so the history still
    shows what was attempted.

30. **X cannot be exercised end to end without a key.** Everything up to the
    network call is tested against a fake SDK: missing key, missing package,
    disabled spend, exhausted reserve, window filtering, and refund-on-failure.
    The SDK surface itself was inspected from the installed package
    (`xpoz==0.10.1`) rather than taken from its README, so the field names in
    `to_post` are the real ones. What remains unverified until a key exists is
    whether the endpoint is reachable from this network — the same check that
    disqualified `public.api.bsky.app`.

31. **The dashboard's tab and chip styling silently stopped applying.** The
    stylesheet targeted `[data-baseweb="tab"]` and `[data-baseweb="tag"]`, which
    is the BaseWeb component model. Streamlit 1.66.1 renders both with
    react-aria instead, so the rules matched nothing: tabs fell back to the
    default sans-serif mixed-case labels and multiselect tokens to a solid
    primary fill that reads as an alert. Dead CSS fails silently — the page
    still renders, just not as designed. Found by querying the live DOM
    (`document.querySelectorAll('[data-baseweb]')` returned `[]`) rather than
    by reading the source, and fixed by targeting `[role="tablist"]`,
    `[data-testid="stTab"]`, and `[data-testid="stMultiSelectTagsContainer"]`.

---

## 6. False positives and false negatives observed

| Type | Observed | Assessment |
|---|---|---|
| Coordination false positive | Not observed in the live samples (detector stayed silent) | The two-episode rule and the baseline are the guards; unmeasured at scale |
| Coordination false negative | Likely — two Telegram channels is a thin sample | Acknowledged; no recall figure claimed |
| Narrative false positive | Same-topic clusters can merge opposing stances | Mitigated by reviewer split/merge controls, not by the algorithm |
| Framing false positive | A post *discussing* a deepfake is flagged as a deepfake claim | The signal is explicitly labelled `deepfake_claim` and the caveat says so |
| Indicator false positive | `bit.ly` and other reference domains excluded by allowlist; unknown shorteners can slip through | Extraction is a lead, not a verdict |
| Language detection | Short posts and code-switching produce low-confidence guesses | Confidence is stored and shown, not discarded |
| Timestamp | Date-only Telegram posts cannot support minute-level synchrony | Such posts are excluded from coordination pairing |

**No precision or recall figure is claimed for live data.** No ground truth
exists for the sampled topics, and inventing a percentage from four runs would be
worse than stating the gap.

---

## 7. What is still unverified

| Unverified | Why it matters |
|---|---|
| Coordination precision at scale | Four samples, zero positives. A precision estimate would need a labelled corpus |
| Cross-language grouping quality | Tested on English/Filipino/Spanish examples; the model claims 100 languages, which is not the same as validated on them |
| Telegram discovery recall | Web search surfaces a fraction of relevant channels; the fraction is unknown |
| Framing classifier accuracy | Keyword matching has no held-out evaluation; it is a lens, not a classifier |
| Lookalike domain judgement | String distance only; no registration, hosting, or content check |
| Risk score calibration | It is an unvalidated heuristic by construction. The itemisation is what makes it defensible, not the number |

---

## 8. Reproduction

```bash
.venv/bin/pytest -q                                   # 227 tests
.venv/bin/pytest -q --cov=src/socmint --cov-report=term-missing
.venv/bin/socmint demo --export exports               # labelled synthetic scenario
.venv/bin/socmint run --topic "<topic>" --start <date> --end <date> --export exports
```
