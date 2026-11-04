# What SOCMINT is for, and what this module solves

Written to be said out loud, not read. If you can answer "why does this exist"
without reaching for the feature list, the rest of the demo takes care of itself.

---

## The one-line answer

**SOCMINT turns public social platforms into a timely, attributable,
local-language sensor for threats — and gives the analyst evidence they can
defend instead of a screenshot they can't.**

---

## The problem, stated honestly

Threat intelligence does not usually fail because there is no information. It
fails on five specific things.

### 1. Latency

Technical intelligence arrives through vendor feeds, blogs, and advisories. Those
lag the event by days to weeks — the write-up has to be researched, drafted,
reviewed, and published.

The people actually affected are on social media within hours. If a bank in
Manila is rumoured breached at 09:00, customers are posting about failed
withdrawals by 11:00. The vendor blog lands nine days later.

For a P0, the difference between "we knew in hour two" and "we knew in week two"
is the difference between proactive defence and writing an incident report.

### 2. Language and locality

Most commercial TI is English-centric and priced for Western enterprise. The
claims that matter for PH, SEA, and GCC customers are in Tagalog, Bahasa,
Arabic, Spanish, and local slang, on local channels, discussing local
institutions.

No global vendor covers this well, because the market is too small to justify it.
That is precisely where a regional team adds value that cannot be bought.

### 3. Evidence quality

An analyst writes: *"There is chatter that actor X is targeting bank Y."*

Ask for the source. What comes back is usually a screenshot in a chat channel —
no timestamp, no link, no context, and no way to tell whether it was one person
or fifty repeating each other.

That is a rumour with a work email attached. SOCMINT done properly produces
timestamped, linked, graded evidence with stated coverage. That is the
difference between a hunch and an assessment.

### 4. Volume

A two-week window on one topic can be hundreds of posts across several
languages. Nobody reads all of them. Without grouping, deduplication, and
structural analysis, the analyst reads the first thirty and guesses about the
rest.

### 5. The corroboration question

*"Is this five sources, or one source repeated?"*

Traditional collection cannot answer it. You see five posts and you count five.
Coordination analysis answers it structurally: five accounts posting identical
text within minutes is one source amplified, and it looks completely different
from five independent outlets arriving at the same conclusion.

Getting this wrong is how intelligence products end up confidently wrong.

---

## Where social beats technical collection

Technical telemetry tells you what already happened on your network. Social
signals often tell you what is about to happen.

| Precursor | Where it appears first |
|---|---|
| Ransomware group teasing a victim before listing | Their public Telegram channel |
| Initial access being sold | Breach and access-broker forums, often advertised on Telegram |
| A new critical CVE being weaponised | Researcher posts and PoC repos, hours after disclosure |
| A phishing wave riding a news event | Lookalike domains promoted on social, before email volume spikes |
| An executive impersonation attempt | Fake accounts and lookalike domains, not email filters |
| Disinformation before it reaches mainstream media | New accounts posting in lockstep on the origin platform |

Ransomware affiliates and initial access brokers *market* on social platforms.
That is not a side channel any more — for some actors it is the primary
channel. If your collection does not cover it, you are reading their press
releases instead of their plans.

---

## What this module actually does about it

It turns `topic + date range` into a case file:

1. **Collects** from public sources with no account, no API key, no cookies —
   OPSEC matters because a collection identity is an attribution risk.
2. **Groups** multilingual posts into narrative candidates and labels the threat
   framing each resembles.
3. **Detects** accounts posting in lockstep, and compares the result against a
   chance baseline so the number means something.
4. **Separates** real indicators from cited references — `php.net` in a
   vulnerability write-up is not an IOC.
5. **Grades** every post with a provisional Admiralty rating and shows the rule
   that produced it.
6. **Produces** key judgements with confidence and caveats, TLP-marked, with an
   itemised risk score you can recompute by hand.
7. **Exports** evidence a second analyst can audit without asking you anything.

### The practical gain

The process exists either way — an analyst monitoring a topic does it today by
hand: search, screenshot, paste into a document, lose the link, forget which
channel it came from.

This makes it **repeatable, multilingual, and auditable**, and it compresses
"topic appears" to "structured brief on my screen" from a few hours of scrolling
to a few minutes. The analyst still does the judgement. That is the point.

---

## Where it sits in the workflow

```
Collection  →  Triage  →  Analysis  →  Assessment  →  Action
            ↑
     this module
```

It is a **collection and triage** tool. It does not replace the analyst, the
CERT relationship, or technical telemetry. It feeds them.

The honest framing for an interview: *this compresses the time from "something
is happening" to "I have a structured, sourced, caveated brief" — and it leaves
an audit trail that a hand-rolled process never does.*

---

## What it does not solve — say this before you are asked

| It is not | Because |
|---|---|
| A truth machine | It surfaces claims and coordination; humans assign verdicts |
| Attribution | Coordination is not ownership, intent, or state sponsorship |
| Real-time | Batch runs over a window, not a streaming feed |
| Complete coverage | A sample, with coverage stated and no recall estimate |
| A replacement for HUMINT or telemetry | It is the public-facing layer those miss |
| A deepfake detector | It tracks *claims* about synthetic media, not the media |

Volunteering the limits is not weakness. In intelligence work, an analyst who
states what they cannot see is trusted further than one who does not.

---

## Why the assessment brief looked like this

The task is a realistic job simulation, and the graded skill is not "can you
scrape a website". It is:

- Can you build collection that is **legal and ethical** under platform terms,
  and say so when it is not?
- Can you separate **what you observed** from **what you concluded**?
- Do you know when to **abstain** rather than guess?
- Can you explain a **method** to someone who will challenge it?

That is why the module reports coverage instead of implying completeness, why
Admiralty grades are marked provisional, why the coordination detector returns
zero on live data and says that is correct, and why the risk score is itemised
rather than learned.

A tool that guesses is worse than one that abstains, because in the output the
guess is indistinguishable from the finding.
