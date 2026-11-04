# Demo runbook

A 40-minute walkthrough, ordered so that every claim you make on screen is one
you can back with a click. Times are cumulative and assume no waiting on network.

The single rule for the whole demo: **never say "found" when you mean
"collected", and never say "coordinated" when you mean "matched".** The tool's
credibility rests on that distinction, and an interviewer will test it.

---

## Before the room (10 min, offline)

```bash
cd socmint-module                       # the repository root
.venv/bin/pytest -q                      # expect: all green
.venv/bin/socmint list                   # note the run IDs you plan to show
.venv/bin/streamlit run app.py           # http://127.0.0.1:8511
```

Pre-warm the embedding model so the live run does not stall:

```bash
.venv/bin/python -c "from socmint.analysis.narratives import _load_model; _load_model('intfloat/multilingual-e5-small')"
```

Have these ready in a second terminal:

- `exports/` directory listing (to show artefact shape without waiting)
- A topic you have **not** run before, chosen by the interviewer

**If the network is down:** the entire analysis layer still runs. Say so, switch
to `--offline-analysis`, and demonstrate on the synthetic fixture. Do not pretend
the live path worked.

---

## 0:00–2:00 — What this is

> "This is a SOCMINT module for cyber threat intelligence. You give it a topic
> and a date range; it discovers public sources, collects original posts, groups
> them into narratives, looks for accounts behaving in lockstep, extracts
> technical indicators, and produces a case assessment you can hand to an analyst.
>
> It runs anonymously — no accounts, no API keys. Two platforms actually work:
> YouTube and Telegram. The other five from the brief are documented as out of
> scope with the specific access barrier for each, and I'll show you that table."

Show `docs/access-and-limitations.md` for 15 seconds. Do not dwell.

> "The more important thing to say up front is what it refuses to do. It does not
> decide that content is false. It does not decide that accounts are bots. It
> does not decide that a video is a deepfake. It produces leads with explicit
> caveats attached, and a human closes them."

---

## 2:00–5:00 — The three use cases, and why one engine

> "The brief was written for cyber threat intelligence, so I built for three
> cases: conflict disinformation, impersonation and deepfakes, and hacktivist
> data leaks. But the interviewer might hand me a topic that is none of those, so
> the case template only changes the *lens* — which threat framings are in focus
> and which prompts get attached. Collection and detection are identical."

Point at the sidebar case-template selector. Then:

> "That's a deliberate architectural choice. If the template changed the pipeline,
> an unexpected topic would produce a degraded result. This way it runs the same
> engine and just doesn't get framing bonuses in the risk score."

---

## 5:00–12:00 — Live run on an unseen topic

Take the interviewer's topic. Enter it with a two-week window.

> "Watch what it does and what it reports while it does it. Discovery first —
> it searches for public sources discussing this topic. Then retrieval from
> whatever it found. Then it tells me, per platform, whether discovery worked,
> whether retrieval worked, and whether it hit a budget cap."

While it runs, narrate the budget:

> "Every run has caps: 500 posts, six queries per platform, a fifteen-minute
> acquisition deadline. When a cap is hit, the platform is marked `partial`, not
> `ok`. That matters — 'ok' means 'completed within budget', not 'exhausted the
> platform'."

When it finishes, **read the status line before the numbers**:

> "Status is `{status}`. YouTube retrieval `{x}`, Telegram `{y}`. If Telegram
> discovery came back empty, that is a real and expected outcome — web-search
> discovery of Telegram channels is noisy and non-deterministic. I'll show you
> what that looks like rather than hide it."

---

## 12:00–18:00 — The Assessment tab

> "This is the case file. TLP marking at the top, case template, run ID.
>
> Down the right is the risk score. Before you ask: it is not a probability.
> It's an additive heuristic, and every point is itemised underneath. You can
> recompute it by hand and disagree with any line. Its only job is triage —
> which run do I look at first."

Read one factor aloud, e.g. `+25: 1 coordination group(s) met the strong-candidate
threshold`.

> "Each key judgement carries a confidence and its caveats. This one says
> 'coordination is not intent, automation, or shared ownership'. That sentence is
> not decoration — it is the difference between a lead and a defamation."

If impersonation findings exist, walk one:

> "Here's a lookalike domain: `acme-bank-secure[.]com` embeds two brand words.
> Note it's defanged. Note the caveat: string distance is a lead, not ownership —
> I did not check who registered it."

---

## 18:00–22:00 — Narratives and framing

> "These are semantic clusters, not verified narratives. The label comes from
> distinctive keywords in the cluster, not from a language model, because a label
> I can't trace to text is a label I can't defend.
>
> The framing chip — disinformation, hacktivism, impersonation — is matched on
> vocabulary, and it shows you exactly which terms matched. It's a lens for the
> analyst, not a conclusion about intent."

Click into a cluster, show representative posts with Admiralty badges.

> "Every post carries a provisional Admiralty grade. Provisional is the important
> word: true source reliability needs history I don't have. So the default for an
> unknown low-volume account is F6 — cannot be judged. I would rather grade
> nothing than grade generously."

**If asked "why is everything 6?"** — good question, have the answer ready:

> "Because information grade 6 means 'truth cannot be judged'. My first version
> counted accounts inside a cluster and upgraded everything. That was wrong twice:
> identical text across accounts is coordination, not corroboration; and a cluster
> that spans the whole corpus inflates every post. So I skip catch-all clusters
> and treat identical text as a coordination signal. The result is that a
> homogeneous sample correctly grades 6 across the board."

---

## 22:00–27:00 — Coordination and the baseline

> "Accounts linked by repeated, time-aligned shared content. Three signals:
> shared canonical URL, near-duplicate text, semantic similarity — combined with
> time proximity. And a hard rule: at least two independent episodes between the
> same pair. One shared link plus a coincidental timestamp is the biggest source
> of false positives in this whole field."

Open a group. Point at the baseline line:

> "This is the part that makes the number mean something. Observed: N qualifying
> pairs. Time-shuffle baseline: M pairs would qualify by chance with the same
> activity levels, just shuffled in time. If those two numbers are close, you have
> nothing."

**Be ready for the honest negative:**

> "On four live runs this detector returned zero groups. That is not a failure —
> it's a correct negative. Measured peak cross-account similarity was 0.313
> against a 0.45 threshold, because the two Telegram accounts I found were
> covering different earthquakes. I demonstrate the detector on a labelled
> synthetic fixture instead, and I label it synthetic everywhere it appears."

Then run `.venv/bin/socmint demo` and show the same group with the baseline gap.

---

## 27:00–30:00 — Indicators

> "Domains, IPs, URLs, file hashes, wallets, Telegram handles, emails — pulled by
> regex, defanged on the way out, and **never enriched**.
>
> That's a deliberate boundary. Enrichment is a separate action with its own
> terms and its own chance of being wrong. A module that silently enriches
> produces indicators whose provenance is unclear. Extraction is mechanical and
> auditable; the analyst pivots from there."

**This is the strongest section to walk slowly, because it is where most naive
implementations fail.** Show the role chips.

> "The first version of this listed every domain it found. I ran a search for a
> vulnerability name and got `php.net`, a vendor's blog, and a researcher's site
> on the IOC list. Technically it did what I asked. Operationally the file was
> worthless, because an analyst who gets a list like that stops reading it.
>
> So every value now carries a role. `indicator` means the post presents it as an
> artefact of compromise. `reference` means it is cited — documentation, a vendor
> advisory, a research outlet. `unknown` means the post gives no signal.
>
> The strongest signal is that the author defanged it themselves. In threat-intel
> posting, people write `hxxp` and `[.]` precisely because they think it's
> hostile — that's a human judgement already made, and it's free to read.
>
> Note the trap I fell into: the demo's own phishing post says *'verify your
> account at acme-bank-secure.com'*. I had `verify` in my benign-language list —
> meaning 'verify the checksum' — so the malicious domain classified as a
> reference. A credential lure now outranks citation language.
>
> Only indicators feed the risk score. References are reported separately and
> hidden by default."

Point at a full SHA256:

> "Hashes are never truncated. A shortened hash is operationally useless — you
> can't search it, you can't submit it. That's enforced by the regex and the
> exporter."

If asked "why not just use an allowlist": because an allowlist can't know about
every researcher's blog, and it can be abused — a lookalike of an allowlisted
brand still needs catching. The rules are structural (is it defanged, is it in an
article path, is it in a lure sentence), not a fixed list.

---

## 30:00–33:00 — Claims

> "Automation proposes claims that are checkable — they need a factual verb and a
> figure, and boilerplate is excluded. It can retrieve candidate evidence. It
> **never** assigns a verdict. The verdict field starts at `unreviewed` and a
> human has to write a rationale before it saves."

Demonstrate: try to save a verdict with no rationale → it refuses.

---

## 33:00–36:00 — Coverage honesty

Go to Coverage.

> "Requested versus observed, per platform. This is the honesty layer. Coverage
> describes what was collected within budget. It is not an estimate of
> platform-wide activity, and empty does not mean nothing was posted.
>
> If you asked me 'what fraction of Telegram discussion about this topic did you
> get', the honest answer is: I don't know, and any number I gave you would be
> invented."

---

## 36:00–38:00 — Export

```bash
ls exports/<run_id>/
```

> "JSON is authoritative for nested data. CSVs are flat tables for spreadsheets:
> posts with Admiralty columns, narratives with framing, coordination groups and
> episodes, propagation edges, earliest observed, claims, indicators,
> impersonation signals, and a standalone assessment.
>
> Two safety properties: exports never overwrite — a re-run gets a new timestamped
> directory, because an evidence directory is a record of what was known at a
> point in time. And all CSV cells are formula-injection safe, because post text
> is attacker-controlled and a cell starting with `=` is a spreadsheet exploit."

---

## 38:00–40:00 — Failure demonstration

This is the part most candidates skip and most interviewers remember.

```bash
.venv/bin/socmint run --topic "<same topic>" --start ... --end ... --offline-analysis
```

> "This is what happens when the embedding model isn't available. The pipeline
> falls back to TF-IDF lexical clustering and sets a degraded flag. Both the CLI
> and the dashboard surface it. Note what it says: cross-language grouping is not
> claimed. It doesn't quietly produce worse results and call them the same thing."

Then:

> "Same principle for rate limits. A 429 sets the platform to `rate_limited`, not
> `failed` and not `empty`. There is no retry storm, no proxy rotation, no
> captcha solving — if a platform says no, the run reports that it said no."

---

## Likely questions, with answers

**"Why only two platforms?"**
Access, not effort. X, TikTok, Instagram, Facebook and Snapchat all require paid
tiers, institutional research eligibility, or allowlisted access. I can show you
the specific barrier for each. Two platforms that genuinely work, with honest
coverage reporting, beats seven that fail differently. The adapter interface is
one file if you hand me credentials.

**"How do you know your coordination detector is right?"**
I don't, on live data — there's no ground truth. What I can show is that it fires
on a labelled synthetic positive, that it stays silent on four live samples where
measured similarity was below threshold, and that it reports a time-shuffle
baseline so the number is interpretable. Precision on live data is unmeasured and
I say so.

**"What's the false positive rate?"**
Unmeasured, and I won't invent one. The design choices that reduce it are: two
independent episodes required, popularity discounting, catch-all cluster
exclusion, and the baseline comparison.

**"How did you use AI?"**
Three places: multilingual embeddings for cross-language clustering (local model,
no API), extractive labelling (deliberately not generative, so labels are
traceable), and keyword-based framing classification. Everything the AI produces
is marked as a candidate. Verdicts are human-only. I did not use a generative LLM
anywhere in the analysis path, because I can't audit what it would say.

**"How did you check the AI's output?"**
Hand-read every cluster in the live samples and recorded disagreements; measured
the similarity gap between related and unrelated pairs to set the threshold
(0.87 vs 0.76 raw, which is why centring is required); and built the labelled
synthetic fixture specifically so a positive result exists to inspect. Details in
`docs/validation.md`.

**"What would you do with two more weeks?"**
X access via a paid tier and a proper ground-truth coordination corpus. Those are
the two things that would move accuracy from "defensible" to "measured".

**"Isn't scraping a terms violation?"**
Public preview surfaces, no authentication, no circumvention, budgeted and
rate-limited, and the restrictions are documented in
`docs/access-and-limitations.md` rather than ignored. Telegram and YouTube both
restrict automated collection and ML use in their terms; that is stated as a
material restriction on the deliverable, not waved away. In a production
deployment this runs under a licence agreement or an approved research
entitlement.

---

## Things not to say

- "This finds disinformation." It finds candidate narratives and coordination.
- "These accounts are bots." It reports matched behaviour; intent and automation
  are unknown.
- "This is the origin." It is the earliest *observed in this sample*.
- "Coverage is X%." No denominator exists.
- Any accuracy percentage on live data.
