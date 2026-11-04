# Design decisions

Every choice below is a decision I made, with the alternative I rejected and the
reason. Where the decision was forced by access rather than preference, I say so.

---

## 1. Scope: two platforms, not seven

**Decision.** Collect from YouTube and Telegram. Document the other five as
out of scope with the specific access barrier for each.

**Rejected.** A shallow connector for all seven.

**Why.** The brief lists seven platforms. Six of them cannot be collected
broadly without either a paid tier, an allowlisted research account, or
authenticated scraping:

| Platform | Barrier | Evidence |
|---|---|---|
| X | Paid API tiers; free tier cannot search | `docs.x.com` pricing page |
| TikTok | Research API requires institutional eligibility; no anonymous search | developers.tiktok.com |
| Instagram | Graph API covers owned accounts only; no topic search | Meta docs |
| Facebook | Content Library requires approved research access | Meta docs |
| Snapchat | Public Profile API is allowlisted; no discovery surface | Snap developer docs |
| YouTube | **Works** — anonymous `ytsearch` + metadata via yt-dlp | Day-1 gate |
| Telegram | **Works** — public `t.me/s/` preview, no account | Day-1 gate |

A seventh shallow connector would have produced seven platforms' worth of
failure modes and two platforms' worth of evidence. Two platforms that genuinely
work, with honest coverage reporting, is the more defensible deliverable. If an
evaluator supplies credentials for another platform, the adapter interface is
one file.

**Cost of the decision.** No X coverage. X is where a lot of English-language
disinformation research happens, and its absence is a real gap, not a rounding
error. It is stated as a gap in every report rather than papered over.

---

## 2. Discovery before retrieval

**Decision.** Every run starts with topic discovery against public search, then
retrieves from whatever sources that discovery surfaced. No seeded channel list.

**Rejected.** A curated list of known-relevant channels, which would have been
far more reliable.

**Why.** A seeded list answers "what are these channels saying" and silently
fails the actual question, which is "what is being said about this topic I have
never seen before". The evaluator will supply an unseen topic; a seed list cannot
cover it. The cost is real: discovery is noisy and non-deterministic, and
Telegram coverage is the weakest part of the system because of it. I accepted a
worse average result for a system that does not need to be pre-trained on the
answer.

**Mitigation.** Discovery results are relevance-filtered by word hits before
they consume retrieval budget, so a bad search result costs a query, not a run.

---

## 3. Anonymous-first access

**Decision.** No accounts, no API keys, no cookies.

**Why.** Three reasons in order of weight:

1. **OPSEC.** A SOCMINT collection identity is an attribution risk. An account
   that queries a topic leaves a trace tied to the analyst. Anonymous reads of
   public previews do not.
2. **Reproducibility.** A key-free system runs on the evaluator's machine with
   `git clone && uv sync`. Nothing to provision, nothing to leak.
3. **Honesty about limits.** With no account there is no risk of accidentally
   using an authenticated surface whose terms forbid the analysis.

**Rejected.** Free official credentials, which the brief permitted. I did not
need them for two platforms, and adding a key would have added a secret to
manage for no coverage gain.

---

## 4. Media as references, not artefacts

**Decision.** Store thumbnail URLs and permalinks. Never download, transcribe,
OCR, or run forensic analysis on media.

**Why.** Downloading platform media has its own legal and terms exposure,
storage cost, and chain-of-custody problem — and it buys nothing the analyst
cannot get by clicking the link. It also keeps the module out of the deepfake
detection business, where a wrong verdict is worse than no verdict.

**Consequence for the deepfake use case.** The module detects *claims* that media
is synthetic, not synthetic media. That distinction is enforced in the type
system (`ImpersonationSignal.kind == "deepfake_claim"`) and repeated in the
caveats. An analyst who needs a forensic verdict runs a separate tool on the file.

---

## 5. Multilingual embeddings with centring

**Decision.** `intfloat/multilingual-e5-small`, 384-dim, MIT licence, run
locally on CPU, with **batch-mean centring** before clustering.

**Why this model.** 100 languages, small enough for CPU inference on a laptop,
permissive licence, no API dependency. A larger model would cluster better and
would not run in the demo environment.

**Why centring — the non-obvious part.** Raw e5 cosine similarity does not
separate related from unrelated text well enough to threshold. Measured on this
corpus: related pairs scored around 0.87 and unrelated pairs around 0.76. A
threshold between those numbers is fragile. Subtracting the batch mean from every
vector removes the shared "this is a social media post about news" direction and
opens the gap. The clustering threshold is expressed in **centred** cosine space
(`-0.15`), which is why it looks unusual.

**Rejected.** A generative LLM for summarisation and labelling. It would produce
nicer prose, cost money per run, send corpus text to a third party, and — most
importantly — produce labels I could not trace to evidence. Extractive labels
from distinctive keywords are uglier and auditable.

**Degradation.** If the model cannot load, the pipeline falls back to TF-IDF
lexical clustering and sets `bundle.degraded = True`. The dashboard and CLI both
surface that flag. Cross-language grouping is explicitly *not claimed* in that
mode.

---

## 6. Coordination: a conservative subset of Pacheco et al.

**Decision.** Implement shared-URL, near-duplicate text, and semantic-similarity
signals combined with time proximity; require **≥2 independent episodes** between
the same accounts; discount ubiquitous links; compare against an
activity-preserving time-shuffle baseline.

**Why the subset.** The full method needs account-level metadata (creation date,
follower graph, profile images) that anonymous collection cannot see. Claiming
the full method while implementing a third of it would be dishonest.

**Why ≥2 episodes.** A single shared link plus a coincidental timestamp is the
single largest source of false positives in coordination detection. Two
independent matches between the same pair is a much stronger claim.

**Why the baseline.** Without it, "these accounts posted the same thing within
15 minutes" sounds damning. With it, I can say "3 pairs qualified, and 0.2 pairs
would qualify by chance under a time shuffle". That comparison is what makes the
number interpretable.

**Honest result.** On four live runs the detector returned **zero** groups. That
is a correct negative, not a failure: measured peak cross-account similarity was
0.313 against a 0.45 threshold, because the two Telegram accounts found were
covering different earthquakes. The detector is demonstrated instead on a
labelled synthetic fixture where the observed overlap (3 pairs) exceeds the
baseline (0.2).

---

## 7. Admiralty grading as a provisional proxy

**Decision.** Assign A–F / 1–6 to every post, mark every grade
`provisional=True`, attach the rule that produced it, and display the rationale
next to the badge.

**Why provisional.** True source reliability requires history: past accuracy,
corrections, ownership, editorial standards. From public posts I can observe only
proxies — whether the account name looks like a newsroom, how much it posts,
whether other accounts independently carry the claim. Handing out "A1" to an
anonymous channel would be worse than handing out nothing, so the default for an
unknown low-volume account is **F6**.

**The corroboration trap.** My first implementation graded information by counting
accounts inside a narrative cluster. That is wrong twice over:

- *Identical text across accounts is coordination, not corroboration.* Five
  accounts posting the same sentence is one source, not five.
- *A catch-all cluster inflates everything.* If one cluster contains the whole
  corpus, every post is "supported by 3+ accounts across 2 platforms" and the
  entire run grades as 2.

Fix: skip any cluster spanning more than 60% of the corpus, and treat identical
text as a coordination signal rather than support. After the fix, the synthetic
demo correctly grades every post **6** (cannot be judged) because the only
cluster present is a catch-all.

---

## 8. Indicators: extract, defang, never validate

**Decision.** Regex-extract domains, IPv4s, URLs, SHA256/MD5, wallets, Telegram
handles and emails. Defang at the model boundary. Never enrich.

**Why never enrich.** Enrichment is a separate, attributable action with its own
terms and its own risk of being wrong. A module that silently enriches produces
IOCs whose provenance is unclear. Extraction is mechanical and auditable; the
analyst pivots from there.

**Defanging is enforced in the model.** `Indicator.value` holds the canonical
form and `Indicator.defanged` the safe form; every export and every dashboard
render uses `defanged`. There is no code path that prints a live malicious link.

**Hash rule.** Full digests only. A truncated hash is operationally useless, so
the regex requires the complete 32- or 64-character digest and the exporter
never truncates.

**Extraction alone was not enough.** Deciding *which* extracted values are
indicators is a separate problem, covered in §10.

---

## 9. Impersonation: count distinct signal kinds, not repetitions

**Decision.** Severity is driven by the number of *distinct* signal kinds
(lookalike domain, urgency, credential request, deepfake claim, authority claim),
not by how many posts carry each.

**Why.** My first implementation summed signal weights across posts, so six posts
saying "urgent" scored six times. That is volume, not evidence — and it let a
noisy corpus outrank a genuinely targeted campaign. Distinct kinds is the honest
measure: one post with a lookalike domain, a credential request, and urgency is a
stronger signal than twenty posts that each say "act now".

**Lookalike matching requires most of the brand's words.** Testing a domain label
against a single brand word would flag every unrelated banking domain. The rule
is: edit-distance ≤2 against the compacted phrase (catches typosquats), **or**
containment of at least two brand words (catches `acme-bank-secure.com`). For
single-word brands the requirement drops to one.

**Rejected signal: "mentions the brand".** In a topic search, every post mentions
the topic. Emitting it would have inflated the distinct-kind count on the entire
corpus for zero information. Removed.

---

## 10. A mentioned domain is not an indicator

**Decision.** Every extracted value carries a role — `indicator`, `reference`, or
`unknown` — assigned by an explicit, ordered rule set, with the reason recorded.

**What went wrong without it.** A search for a vulnerability name returns posts
that *cite* things: the language documentation, the vendor advisory, the
researcher's blog. My first implementation put every one of them in
`indicators.csv`. `php.net`, `huntress.com` and a personal blog all appeared on an
IOC list. An analyst who receives that file stops trusting the rest of it, and
they are right to.

**The rules, strongest first.**

| # | Signal | Role |
|---|---|---|
| 1 | The author defanged it (`hxxp`, `[.]`, `[@]`) | indicator |
| 2 | It is a hash or wallet address | indicator |
| 3 | Its host is a known documentation/vendor/registry domain | reference |
| 4 | The URL path is an article (`/blog/`, `/2026/`, `/advisory/`) | reference |
| 5 | It sits in a credential-lure sentence | indicator |
| 6 | It sits in attribution language ("according to", "read the") | reference |
| 7 | It sits next to hostile-artefact vocabulary | indicator |
| 8 | Nothing above | unknown |

**Why the author's own defanging is the strongest signal.** In threat-intel
posting, writers defang precisely because they consider the value hostile. It is
a human judgement already made, and it costs nothing to read.

**Why rule 5 exists, and why `verify` is banned from the benign list.** The first
version of the benign-language pattern included the word `verify` (meaning
"verify the checksum"). The demo's own phishing post says *"verify your account at
acme-bank-secure.com"* — so the malicious domain was classified as a reference.
A credential lure now outranks attribution language, and `verify` / `integrity`
are excluded from the benign pattern entirely.

**A URL inherits its host's role.** If the author defanged the domain, the full
URL is equally hostile. Conversely, a bare domain whose only appearances are
inside article-style URLs is a citation.

**Infrastructure is dropped, not classified.** Running the classifier over a real
`php2shell` collection left 195 values, of which the "indicators" were a coaching
site's course pages, a Google Form, and a vendor's own app — all flagged because
every YouTube description says "click here". Two corrections followed: a
`_SERVICE_DOMAINS` set drops platforms, creator/monetisation links and generic
hosting before classification, and the lure pattern now requires credential or
financial language rather than a click. On the same run the result went from 195
values (6 of them wrong) to 126 values, 0 false indicators, and 88 correctly
identified references.

**Unknown is a real answer.** Most values carry no signal in a short post. Saying
so is more useful than forcing a binary, and it is the honest state. After the
corrections above, a typical CVE collection is mostly references and unknowns —
because that is what a vulnerability discussion actually contains.

**The risk score only counts indicators.** A cited vendor advisory cannot raise
a case's risk score. References are reported separately and excluded by default
in the dashboard.

---

## 11. Risk score: additive, itemised, capped

**Decision.** `risk_score` is a 0–100 sum of coarse, named contributions, each
emitted as a string in `risk_factors`.

**Why not a model.** A learned score would need labelled data I do not have, and
would be unexplainable in a walkthrough. An additive score can be recomputed by
hand from the factor list, and a reviewer can disagree with a specific line.

**What it is not.** It is not a probability. 60 does not mean 60% likely to be a
campaign. It answers "which run do I look at first", nothing more. That sentence
appears in the assessment caveats, the manifest, and the dashboard.

**Degraded mode adds zero, but says so.** A degraded run does not get a penalty
— the evidence is what it is — but a `+0` factor states that framing and
coordination signals are weaker than the score implies.

---

## 12. Case templates change the lens, not the engine

**Decision.** Four presets (conflict/disinformation, impersonation/deepfake,
hacktivist/leak, custom). A preset selects which framings are *in focus* for the
risk heuristic and which analyst prompts are attached. It does not change
collection, clustering, or detection.

**Why.** The evaluator may supply a topic outside the three priority use cases.
If presets changed the pipeline, an unexpected topic would produce a degraded
result. With the split, an unexpected topic runs the identical engine and simply
scores without framing bonuses.

---

## 13. Streamlit, restyled rather than replaced

**Decision.** Keep Streamlit; replace the theme and rebuild the visual layer in
CSS to a deep-navy / crimson case-file aesthetic.

**Why not a custom frontend.** Five days. A React frontend would have consumed
the budget that went into analysis correctness and failure handling. Streamlit
gives reviewer controls, dataframes, and file downloads for free.

**Why restyle it at all.** The default Streamlit look signals "prototype" and
undermines the credibility of the numbers inside it. The restyle is cosmetic but
deliberate: monospace for machine-generated values (indicators, Admiralty codes),
uppercase tracked labels for metadata, crimson reserved for signal rather than
decoration, and a TLP banner that is impossible to miss. Colour is semantic here
— crimson means attention, not brand.

**Known limitation.** Streamlit re-renders the whole page on interaction, and its
DOM is not designed for deep CSS customisation. The theme is applied through
`config.toml` plus a scoped stylesheet; some selectors are version-sensitive
(pinned to Streamlit 1.66.1).

---

## 14. Never overwrite an export

**Decision.** `export()` uses `mkdir(exist_ok=False)` and raises on collision.

**Why.** Evidence directories are the record of what was known at a point in
time. A silent overwrite destroys the ability to say "this is what the run
produced before the analyst changed their mind". A re-run gets a new directory.

---

## 15. Explicit abstention everywhere

**Decision.** Unknown is `None` with a reason, never `0` or a guess. Empty
results are labelled as empty *for this sample*, not as absence of activity.

Examples enforced in code:

- `Engagement` fields are `None` with `missing_reason`, never `0`.
- `EarliestObserved.caveat` ships with every record: "earliest observed in the
  collected sample; not established as the true origin."
- `ClaimAssessment.verdict` defaults to `unreviewed`; automation never writes a
  verdict.
- Coverage notes state that `empty` does not mean nothing was posted.

This is the single most important design constraint. A SOCMINT tool that guesses
is worse than one that abstains, because the guess is indistinguishable from the
finding in the output.
