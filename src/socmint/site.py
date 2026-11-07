"""Static case-file export.

The collector cannot run on Cloudflare or Vercel: it needs a long-lived process,
outbound scraping, PyTorch and SQLite, and neither platform offers that. What
*can* be hosted there is the finished product — one self-contained HTML file per
run, deployable as a static site with no server, no build step and no runtime.

That is also the better demo artifact. A link an assessor can open beats a
Streamlit process on a laptop, and it cannot fail mid-demo because a collector
timed out.

The file uses the same design tokens as the dashboard in ``app.py`` so the two
read as one product: deep navy, crimson signal colour, monospace for anything
machine-generated, and a terminal prompt in the masthead.

Everything is inlined: CSS, a small amount of JS for filtering, and the data as
markup. No fonts, no CDNs, no network at view time. Post text is untrusted and
is escaped on the way in.

Section order is the order a document is read in, which is not quite the
dashboard's: the dashboard is a workbench where you jump between tabs, whereas a
case file is read top to bottom and ends with the raw evidence as an appendix.
"""

from __future__ import annotations

import html
from datetime import datetime, timedelta
from pathlib import Path

from .analysis.brief import Brief, BriefSection, build_brief
from .analysis.narratives import volume_series
from .models import AnalysisBundle, IndicatorRole, Post, Run
from .prose import humanise, kind_label, platform_label, plural
from .summary import ModelSummary

# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------

_CSS = """
:root{
  --void:#050818; --navy:#070B22; --navy2:#0C1132; --navy3:#111845;
  --line:#1E2657; --line-hot:#2E3A7A;
  --ink:#E6E9F5; --muted:#8A93BF; --dim:#727CB0;
  --crimson:#EC2C53; --crimson-soft:rgba(232,35,74,.12);
  --amber:#F2A33C; --mint:#35D6A4;
  --mono:ui-monospace,"Cascadia Code","SF Mono",Menlo,Consolas,monospace;
  --sans:-apple-system,"Segoe UI",Inter,Roboto,Helvetica,Arial,sans-serif;
}
*{box-sizing:border-box}
html,body{margin:0}
body{
  background:
    radial-gradient(1200px 600px at 82% -10%, rgba(232,35,74,.10), transparent 60%),
    radial-gradient(900px 500px at 0% 0%, rgba(80,110,255,.12), transparent 55%),
    var(--navy);
  background-attachment:fixed;
  color:var(--ink); font:15px/1.6 var(--sans);
}
.wrap{max-width:1180px;margin:0 auto;padding:36px 24px 96px}

/* ---------- masthead ---------- */
.masthead{display:flex;align-items:flex-end;justify-content:space-between;gap:24px;
  padding-bottom:18px;border-bottom:1px solid var(--line)}
.prompt{font-family:var(--mono);font-size:34px;font-weight:600;letter-spacing:-.03em;line-height:1}
.prompt .sigil{color:var(--crimson)}
.prompt .caret{display:inline-block;width:11px;height:26px;margin-left:4px;
  background:var(--crimson);vertical-align:-3px;animation:blink 1.15s steps(1) infinite}
@keyframes blink{0%,55%{opacity:1}56%,100%{opacity:0}}
@media (prefers-reduced-motion:reduce){.prompt .caret{animation:none}}
.mh-sub{font-size:10.5px;letter-spacing:.26em;text-transform:uppercase;color:var(--dim);
  margin-top:9px;font-weight:600}
.mh-right{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.topic{font-size:28px;font-weight:600;margin:22px 0 6px;letter-spacing:-.4px}
.sub{color:var(--muted);font-size:13px}

/* ---------- badges ---------- */
.tlp,.badge,.chip,.adm{font-family:var(--mono);font-size:10.5px;font-weight:600;
  letter-spacing:.12em;text-transform:uppercase;padding:4px 9px;border-radius:3px;white-space:nowrap}
.badge{color:var(--muted);border:1px solid var(--line-hot);background:rgba(17,24,69,.6)}
.chip{border:1px solid var(--line-hot);color:var(--muted);background:transparent}
.chip-high{color:#FF6B87;border-color:#7A1B2E;background:var(--crimson-soft)}
.chip-moderate{color:var(--amber);border-color:#6B4A12;background:rgba(242,163,60,.10)}
.chip-low{color:var(--muted);border-color:var(--line-hot)}
.chip-mint{color:var(--mint);border-color:#14543F;background:rgba(53,214,164,.10)}
.tlp{color:#fff;background:var(--crimson)}
.tlp-clear{background:#3B4A8C}
.tlp-green{background:#1E7A5A}
.tlp-amber{background:#B8761C}
.tlp-amber-strict{background:#C2540F}
.tlp-red{background:var(--crimson)}
.adm{color:var(--ink);border:1px solid var(--line-hot);background:var(--navy3);letter-spacing:.06em}
.adm-a,.adm-b,.adm-c{border-color:#14543F;color:var(--mint)}
.adm-d{border-color:#6B4A12;color:var(--amber)}
.adm-e,.adm-f{border-color:#7A1B2E;color:#FF6B87}

/* ---------- sections ---------- */
h2{font:600 10px/1 var(--mono);letter-spacing:.24em;text-transform:uppercase;color:var(--dim);
  margin:44px 0 14px;padding-bottom:8px;border-bottom:1px solid var(--line)}
.note{color:var(--muted);font-size:12.5px;line-height:1.6;margin:0 0 16px}
.caveat{color:var(--amber);font-size:12px;line-height:1.55}
.empty{color:var(--muted);font-size:13px;padding:14px 0}
.mono{font-family:var(--mono);font-size:12.5px}

/* ---------- stat tiles ---------- */
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:16px}
.stat{position:relative;overflow:hidden;border:1px solid var(--line);border-radius:4px;
  padding:14px 15px 13px;
  background:linear-gradient(180deg,rgba(17,24,69,.85),rgba(12,17,50,.85))}
.stat::before{content:"";position:absolute;left:0;top:0;width:2px;height:100%;
  background:var(--crimson);opacity:.85}
.stat .k{font-size:9.5px;letter-spacing:.2em;text-transform:uppercase;color:var(--dim);font-weight:700}
.stat .v{font-family:var(--mono);font-size:27px;font-weight:600;line-height:1.15;margin-top:5px}
.stat .s{font-size:10.5px;color:var(--muted);margin-top:3px}

/* ---------- risk ---------- */
.risk{border:1px solid #3A1530;border-radius:4px;padding:18px 20px;margin-bottom:18px;
  background:linear-gradient(135deg,rgba(232,35,74,.14),rgba(12,17,50,.9) 55%)}
.risk .k{font-size:9.5px;letter-spacing:.22em;text-transform:uppercase;color:#FF8FA5;font-weight:700}
.risk .v{font-family:var(--mono);font-size:44px;font-weight:600;line-height:1;margin:8px 0 2px}
.risk .v span{font-size:15px;color:var(--dim)}
.bar{height:3px;background:var(--navy3);border-radius:2px;overflow:hidden;margin-top:12px}
.bar>i{display:block;height:100%;background:linear-gradient(90deg,var(--crimson),#FF7A93)}
.risk .n{font-size:10.5px;color:var(--muted);margin-top:9px;line-height:1.5}

/* ---------- key judgements ---------- */
.kj{border-left:2px solid var(--line-hot);padding:10px 0 10px 14px;margin:0 0 14px}
.kj.hi{border-left-color:var(--crimson)}
.kj .head{display:flex;gap:10px;align-items:center;margin-bottom:5px;flex-wrap:wrap}
.kj .kid{font-family:var(--mono);font-size:10.5px;letter-spacing:.12em;color:var(--dim)}
.kj .body{font-size:14px;line-height:1.55}
.kj .cav{color:var(--dim);font-size:11.5px;line-height:1.5;margin-top:6px}

/* ---------- cards ---------- */
.card{background:var(--navy2);border:1px solid var(--line);border-radius:4px;
  padding:15px 16px;margin-bottom:10px}
.card .hd{display:flex;justify-content:space-between;gap:12px;align-items:baseline;
  margin-bottom:7px;flex-wrap:wrap}
.card .id{font-family:var(--mono);font-size:11px;letter-spacing:.1em;color:var(--crimson)}

/* ---------- indicators ---------- */
.ioc{display:flex;gap:16px;align-items:baseline;font-family:var(--mono);font-size:12px;
  background:var(--void);border:1px solid var(--line);border-radius:3px;padding:9px 11px;margin:5px 0}
.ioc .val{flex:1 1 auto;min-width:0;word-break:break-all;line-height:1.45}
.ioc .meta{flex:0 0 auto;color:var(--dim);font-size:9.5px;white-space:nowrap;
  letter-spacing:.1em;text-transform:uppercase}

/* ---------- written brief ---------- */
.standfirst{color:var(--muted);font-size:14.5px;line-height:1.7;max-width:74ch;margin:0 0 8px}
.brief h3{font-size:13px;font-weight:650;color:var(--ink);margin:26px 0 9px;
  padding-bottom:7px;border-bottom:1px solid var(--line)}
.brief p{color:#C9CFE6;font-size:13.5px;line-height:1.75;max-width:74ch;margin:0 0 12px}
.brief ul{max-width:74ch;margin:2px 0 14px;padding-left:17px}
.brief li{color:var(--muted);font-size:12.5px;line-height:1.7;margin-bottom:6px}
.brief li::marker{color:var(--crimson)}

/* ---------- model summary beside the brief ---------- */
.two-up{display:grid;grid-template-columns:1fr;gap:16px;margin:6px 0 26px;
  align-items:start;max-width:850px}
/* Two columns only where each one still holds a readable measure. Below this
   the panels stack: side by side at 768 squeezes prose to about 45 characters. */
@media (min-width:1000px){.two-up{grid-template-columns:1fr 1fr;gap:22px}}
.panel{background:var(--navy2);border:1px solid var(--line);border-radius:6px;
  padding:18px 20px 20px}
.panel h3{margin-top:0}
.panel .provenance{color:var(--dim);font-size:10.5px;font-family:var(--mono);
  letter-spacing:.05em;margin:-2px 0 12px}
.panel.model .generated{color:var(--ink);font-size:13.5px;line-height:1.72;
  max-width:68ch;margin:0}
.brief.continuation{margin-top:0}

/* ---------- inline volume chart ---------- */
.chart{margin:14px 0 26px;max-width:820px}
.chart svg{display:block;width:100%;height:auto}
.chart figcaption{color:var(--dim);font-size:11px;font-family:var(--mono);
  letter-spacing:.04em;margin-top:8px}
.chart .axis{fill:var(--dim);font:10px var(--mono);letter-spacing:.06em}

/* ---------- tables ---------- */
table{width:100%;border-collapse:collapse;font-size:13px;margin-bottom:8px}
th{text-align:left;font:600 9px/1 var(--mono);letter-spacing:.16em;text-transform:uppercase;
  color:var(--dim);padding:8px 10px;border-bottom:1px solid var(--line)}
td{padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:none}

/* ---------- search ---------- */
.tools{display:flex;gap:10px;flex-wrap:wrap;margin:20px 0 4px}
.tools-label{font:600 9.5px/1 var(--mono);letter-spacing:.2em;text-transform:uppercase;
  color:var(--dim);align-self:center}
input[type=search]{background:var(--navy2);border:1px solid var(--line);color:var(--ink);
  border-radius:4px;padding:9px 12px;font-size:13px;min-width:300px;font-family:inherit}
input[type=search]:focus{outline:none;border-color:var(--crimson)}
input[type=search]::placeholder{color:var(--dim)}

a{color:var(--ink);text-decoration:none;border-bottom:1px solid var(--line)}
a:hover{border-color:var(--crimson)}

footer{margin-top:56px;padding-top:20px;border-top:1px solid var(--line);
  color:var(--dim);font-size:11px;font-family:var(--mono);line-height:1.9}

/* ---------- print ---------- */
@media print{
  body{background:#fff;color:#111}
  .wrap{max-width:none;padding:0}
  .prompt,.topic,.stat .v,.card .id,.kj .body{color:#111}
  .prompt .sigil,.prompt .caret,.tools{display:none}
  .stat,.card,.risk,.ioc{background:#fff;border-color:#bbb}
  .stat::before{display:none}
  .note,.sub,.caveat,.mh-sub,td,th,.stat .k,.stat .s{color:#333}
  a{color:#111;border:0}
  h2{page-break-after:avoid;color:#111;border-color:#bbb}
  tr,.card,.kj{page-break-inside:avoid}
}
"""

_JS = """
(function(){
  var q=document.getElementById('q');
  if(!q) return;
  q.addEventListener('input',function(){
    var v=q.value.toLowerCase().trim();
    document.querySelectorAll('[data-search]').forEach(function(row){
      row.style.display=(!v||row.getAttribute('data-search').indexOf(v)>-1)?'':'none';
    });
  });
})();
"""

_TLP_CLASS = {
    "TLP:CLEAR": "tlp-clear",
    "TLP:GREEN": "tlp-green",
    "TLP:AMBER": "tlp-amber",
    "TLP:AMBER+STRICT": "tlp-amber-strict",
    "TLP:RED": "tlp-red",
}

_VERDICT_CLASS = {
    "supported": "chip-mint",
    "contradicted": "chip-high",
    "mixed": "chip-moderate",
    "unverified": "chip-low",
    "not_checkable": "chip-low",
    "unreviewed": "chip-low",
}


def _e(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _inclusive_end(run: Run) -> str:
    """The stored window is half-open; a reader thinks in inclusive days."""
    return (run.window.end - timedelta(microseconds=1)).date()


def _fmt_dt(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else "—"


def _fmt_framing(framing: object) -> str:
    """A framing of "none" means nothing matched, which is a finding, not a blank."""
    text = "" if framing is None else str(framing).replace("_", " ")
    return "no framing matched" if text.strip().lower() in {"", "none", "threatframing.none"} else text


def _fmt_counts(counts: dict[str, int]) -> str:
    """Render per-platform counts as prose, not as a Python dict."""
    if not counts:
        return "no platform data"
    parts = [f"{n} {platform_label(name)}" for name, n in sorted(counts.items())]
    return " · ".join(parts)


def _chip(text: object, cls: str = "") -> str:
    return f'<span class="chip {cls}">{_e(text)}</span>'


def _adm(code: str | None) -> str:
    # An ungraded post gets a bare dash, not a bordered badge: a small outlined
    # box in a table column reads as an empty checkbox.
    if not code:
        return '<span style="color:var(--dim)">—</span>'
    return f'<span class="adm adm-{_e(code[0].lower())}">{_e(code)}</span>'


def _search_blob(*values: object) -> str:
    """Lowercased haystack for the client-side filter. Escaped like any attribute."""
    return _e(" ".join(str(v) for v in values if v).lower())


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def _header(run: Run, posts: list[Post], bundle: AnalysisBundle) -> str:
    dated = sum(1 for p in posts if p.published_at)
    tlp_cls = _TLP_CLASS.get(str(run.tlp), "tlp-amber")
    degraded = (
        '<div class="note" style="border-left:3px solid var(--amber);padding-left:12px">'
        "<b>Analysis degraded.</b> The multilingual embedding model was unavailable; "
        "groups are lexical only and cross-language grouping is not claimed.</div>"
        if bundle.degraded
        else ""
    )
    return f"""
<header>
  <div class="masthead">
    <div>
      <div class="prompt"><span class="sigil">&gt;</span> socmint<span class="caret"></span></div>
      <div class="mh-sub">Social threat intelligence · case file</div>
    </div>
    <div class="mh-right">
      <span class="tlp {tlp_cls}">{_e(run.tlp)}</span>
      <span class="badge">case · {_e(str(run.case_template).replace("_", " "))}</span>
      <span class="badge">{_e(run.run_id)}</span>
    </div>
  </div>
  <div class="topic">{_e(run.topic)}</div>
  <div class="sub">Window {_e(run.window.start.date())} → {_e(_inclusive_end(run))} UTC ·
    {len(posts)} {"post" if len(posts) == 1 else "posts"} collected · {dated} dated ·
    status {_e(run.status)}</div>
  {degraded}
</header>"""


def _filter_bar() -> str:
    return """<div class="tools"><span class="tools-label">filter</span>
    <input type="search" id="q" placeholder="values, claims, evidence"></div>"""


_MONTHS_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                 "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _day_label(bucket: str) -> str:
    """'2026-10-29' -> '29 Oct' — the axis form, not the storage form."""
    try:
        year, month, day = (int(part) for part in bucket.split("-")[:3])
    except (ValueError, TypeError):
        return bucket
    return f"{day} {_MONTHS_SHORT[month - 1]}"


def _volume_chart(posts: list[Post], width: int = 820, height: int = 148) -> str:
    """Daily totals as inline SVG: no script, no external asset, prints cleanly."""
    series = volume_series(posts)
    if not series:
        return ""
    totals: dict[str, int] = {}
    for row in series:
        totals[row["bucket"]] = totals.get(row["bucket"], 0) + row["count"]

    buckets = sorted(totals)
    peak = max(totals.values()) or 1
    peak_day = max(buckets, key=lambda b: totals[b])
    baseline = height - 34  # leaves a gutter for the date labels
    slot = width / len(buckets)
    bar_w = slot * 0.62
    bars = []
    for index, bucket in enumerate(buckets):
        value = totals[bucket]
        bar_h = max(2.0, (value / peak) * (baseline - 4))
        x = index * slot + (slot - bar_w) / 2
        opacity = "1" if value == peak else ".45"
        bars.append(
            f'<rect x="{x:.1f}" y="{baseline - bar_h:.1f}" width="{bar_w:.1f}" '
            f'height="{bar_h:.1f}" fill="var(--crimson)" opacity="{opacity}" rx="1"/>'
        )

    # Label three points only: first, peak, last. A label under every bar turns
    # a sparkline into a table the reader has to decode.
    labels = []
    for bucket, anchor in ((buckets[0], "start"), (peak_day, "middle"), (buckets[-1], "end")):
        index = buckets.index(bucket)
        cx = index * slot + slot / 2
        if anchor == "start":
            cx = max(2.0, cx - slot / 2)
        elif anchor == "end":
            cx = min(width - 2, cx + slot / 2)
        labels.append(
            f'<text x="{cx:.1f}" y="{height - 12}" text-anchor="{anchor}" '
            f'class="axis">{_day_label(bucket)}</text>'
        )

    first, last = buckets[0], buckets[-1]
    return f"""<figure class="chart">
  <svg viewBox="0 0 {width} {height}" role="img"
       aria-label="Posts per day from {_e(first)} to {_e(last)}, peak {peak} on {_e(peak_day)}">
    {"".join(bars)}
    <line x1="0" y1="{baseline - 0.5}" x2="{width}" y2="{baseline - 0.5}"
          stroke="var(--line)" stroke-width="1"/>
    {"".join(labels)}
  </svg>
  <figcaption>Posts per day · peak {peak} on {_day_label(peak_day)}</figcaption>
</figure>"""


def _section(section: BriefSection) -> str:
    body = "".join(f"<p>{_e(p)}</p>" for p in section.paragraphs)
    if section.bullets:
        body += "<ul>" + "".join(f"<li>{_e(b)}</li>" for b in section.bullets) + "</ul>"
    return f"<h3>{_e(section.heading)}</h3>{body}"


def _brief(brief: Brief) -> str:
    """The written assessment, rendered as prose rather than as cards."""
    if brief.is_empty:
        return ""
    return f"""
<div class="brief">
  <p class="standfirst">{_e(brief.standfirst)}</p>
  {"".join(_section(s) for s in brief.sections)}
</div>"""


def _two_up(summary: ModelSummary | None, brief: Brief) -> str:
    """The model summary beside the brief's opening, as peers.

    Not a headline over the brief: a reader should see two readings of the same
    findings, each labelled, neither presented as the conclusion. The remaining
    sections continue full width beneath, so nothing is squeezed into a column.
    """
    if summary is None or not brief.sections:
        return ""
    opening, rest = brief.sections[0], brief.sections[1:]
    remainder = (
        f'<div class="brief continuation">{"".join(_section(s) for s in rest)}</div>'
        if rest
        else ""
    )
    return f"""
<p class="standfirst">{_e(brief.standfirst)}</p>
<div class="two-up">
  <section class="panel model">
    <h3>Model summary</h3>
    <p class="provenance">{_e(summary.provenance)} · condenses the findings below</p>
    <p class="generated">{_e(summary.text)}</p>
  </section>
  <section class="panel">
    <h3>{_e(opening.heading)}</h3>
    <p class="provenance">Derived directly from the collected records</p>
    {_section_body(opening)}
  </section>
</div>
{remainder}"""


def _section_body(section: BriefSection) -> str:
    """The body of a section, without repeating its heading."""
    body = "".join(f"<p>{_e(p)}</p>" for p in section.paragraphs)
    if section.bullets:
        body += "<ul>" + "".join(f"<li>{_e(b)}</li>" for b in section.bullets) + "</ul>"
    return body


def _assessment(bundle: AnalysisBundle) -> str:
    a = bundle.assessment
    if a is None:
        return ""

    actionable = sum(1 for i in bundle.indicators if i.role is IndicatorRole.INDICATOR)
    unreviewed = sum(1 for c in bundle.claims if c.verdict == "unreviewed")
    stats = f"""
<div class="stats">
  <div class="stat"><div class="k">Key judgements</div><div class="v">{len(a.key_judgements)}</div>
    <div class="s">confidence stated on each</div></div>
  <div class="stat"><div class="k">Indicators</div><div class="v">{actionable}</div>
    <div class="s">references excluded</div></div>
  <div class="stat"><div class="k">Coordination</div><div class="v">{len(bundle.coordination)}</div>
    <div class="s">candidates · not verdicts</div></div>
  <div class="stat"><div class="k">Claims</div><div class="v">{len(bundle.claims)}</div>
    <div class="s">{unreviewed} unreviewed</div></div>
</div>"""

    judgements = "".join(
        f"""<div class="kj{' hi' if k.confidence == 'high' else ''}">
          <div class="head"><span class="kid">{_e(k.judgement_id)}</span>
            {_chip(f"{k.confidence} confidence", _conf_class(k.confidence))}</div>
          <div class="body">{_e(k.statement)}</div>
          {"".join(f'<div class="cav">↳ {_e(c)}</div>' for c in k.caveats)}
        </div>"""
        for k in a.key_judgements
    )
    factors = "".join(f'<div class="caveat">· {_e(f)}</div>' for f in a.risk_factors)
    caveats = "".join(f'<div class="caveat">↳ {_e(c)}</div>' for c in a.caveats)

    return f"""
<h2>Assessment</h2>
{stats}
<div class="risk">
  <div class="k">Triage risk · additive heuristic</div>
  <div class="v">{a.risk_score}<span>/100</span></div>
  <div class="bar"><i style="width:{min(max(a.risk_score, 0), 100)}%"></i></div>
  <div class="n">Not a probability. It answers which run to read first, and can be
  recomputed by hand from the factors below — disagree with any line.</div>
</div>
{judgements}
<div class="card"><div class="hd"><span class="id">risk factors</span></div>{factors or
  '<div class="note">No risk factors recorded.</div>'}</div>
{_impersonation(bundle)}
<div class="card"><div class="hd"><span class="id">case caveats</span></div>
  {caveats or '<div class="note">No case-level caveats.</div>'}</div>"""


def _conf_class(confidence: str) -> str:
    return {"high": "chip-high", "moderate": "chip-moderate", "medium": "chip-moderate"}.get(
        confidence, "chip-low"
    )


def _impersonation(bundle: AnalysisBundle) -> str:
    if not bundle.impersonation:
        return ""
    cards = []
    for finding in bundle.impersonation:
        sev = {"high": "chip-high", "medium": "chip-moderate", "low": "chip-low"}.get(
            finding.severity, "chip-low"
        )
        signals = "".join(
            f"""<div class="kj"><div class="head"><span class="kid">{_e(humanise(s.kind))}</span>
              {_chip(f"weight {s.weight:g}")}</div>
              <div class="body">{_e(s.detail)}</div>
              {"".join(f'<div class="cav">“{_e(item)}”</div>' for item in s.evidence[:2])}
            </div>"""
            for s in finding.signals
        )
        caveats = "".join(f'<div class="caveat">↳ {_e(c)}</div>' for c in finding.caveats)
        cards.append(
            f"""<div class="card">
              <div class="hd"><span class="id">{_e(finding.finding_id)}</span>
                <span>{_chip(finding.severity + " severity", sev)}
                  {_chip(finding.confidence + " confidence", _conf_class(finding.confidence))}
                  {_chip(f"{len(finding.post_ids)} posts")}</span></div>
              <div><b>{_e(finding.subject)}</b></div>
              {signals}{caveats}
            </div>"""
        )
    return f"""
<h2>Impersonation &amp; brand abuse</h2>
<div class="note">Signals co-occurring in post text.</div>
{"".join(cards)}"""


def _indicators(bundle: AnalysisBundle) -> str:
    if not bundle.indicators:
        return """
<h2>Indicators</h2>
<div class="empty">No technical values were extracted from this sample.</div>"""
    rows = []
    for ind in bundle.indicators:
        role = str(ind.role)
        cls = {"indicator": "chip-high", "reference": "chip-low"}.get(role, "chip-moderate")
        # The wrapper carries data-search so the filter hides the reason line
        # with its value, rather than orphaning it. An "unknown" role carries a
        # boilerplate reason identical on every row, so it is dropped: the badge
        # already says it, and twenty copies of the same sentence is noise.
        reason = (
            f'<div style="color:var(--dim);font-size:11.5px;margin:-2px 0 10px 2px">'
            f"↳ {_e(ind.role_reason)}</div>"
            if role != "unknown"
            else ""
        )
        rows.append(
            f"""<div data-search="{_search_blob(ind.defanged, ind.value, ind.kind, role)}">
              <div class="ioc">
                <span class="val">{_e(ind.defanged)}</span>
                <span class="meta">{_chip(role, cls)} {_e(kind_label(ind.kind, 1))} ·
                  {plural(len(ind.post_ids), "post")}</span>
              </div>{reason}
            </div>"""
        )
    actionable = sum(1 for i in bundle.indicators if i.role is IndicatorRole.INDICATOR)
    refs = sum(1 for i in bundle.indicators if i.role is IndicatorRole.REFERENCE)
    return f"""
<h2>Indicators</h2>
<div class="note">Values the post presents as artefacts of compromise are
<b>indicators</b>; cited documentation and vendor advisories are <b>references</b>.
All values are defanged.</div>
<div class="stats">
  <div class="stat"><div class="k">Indicators</div><div class="v">{actionable}</div>
    <div class="s">actionable</div></div>
  <div class="stat"><div class="k">Cited references</div><div class="v">{refs}</div>
    <div class="s">excluded from the set</div></div>
  <div class="stat"><div class="k">Total values</div><div class="v">{len(bundle.indicators)}</div>
    <div class="s">extracted</div></div>
</div>
{''.join(rows)}"""


def _narratives(bundle: AnalysisBundle) -> str:
    if not bundle.narratives:
        return ""
    cards = "".join(
        f"""<div class="card">
          <div class="hd"><span class="id">{_e(n.narrative_id)}</span>
            {_chip(_fmt_framing(n.framing),
                   "chip-high" if str(n.framing) != "none" else "chip-low")}</div>
          <div><b>{_e(n.label)}</b></div>
          <div class="sub">{plural(len(n.member_ids), "post")} · {_e(_fmt_counts(n.platform_counts))}</div>
          {"".join(f'<div class="cav" style="color:var(--dim);font-size:11.5px">↳ {_e(e)}</div>'
                   for e in n.framing_evidence[:4])}
        </div>"""
        for n in bundle.narratives
    )
    return f"""
<h2>Narrative candidates</h2>
<div class="note">Grouped by shared vocabulary. Each candidate requires review.</div>
{cards}"""


def _coordination(bundle: AnalysisBundle) -> str:
    if not bundle.coordination:
        return """
<h2>Coordination</h2>
<div class="empty">No account group met the threshold in this sample. That is a
negative result for this sample only; it does not show coordination was absent.</div>"""
    cards = "".join(
        f"""<div class="card">
          <div class="hd"><span class="id">{_e(g.group_id)}</span>
            <span>{_chip(str(g.confidence).replace("_", " "))}
              {_chip(plural(len(g.episodes), "episode"))}
              {_chip(f"baseline {g.baseline_episodes}")
               if g.baseline_episodes is not None else ""}</span></div>
          <div class="mono">{_e(", ".join(g.account_ids))}</div>
          {"".join(f'<div class="cav" style="color:var(--dim);font-size:11.5px">↳ {_e(b)}</div>'
                   for b in g.benign_explanations[:3])}
        </div>"""
        for g in bundle.coordination
    )
    return f"""
<h2>Coordination candidates</h2>
<div class="note">Accounts linked by repeated, time-aligned posting of related content.</div>
{cards}"""


def _claims(bundle: AnalysisBundle, by_id: dict[str, Post]) -> str:
    if not bundle.claims:
        return """
<h2>Claim review</h2>
<div class="empty">No claim candidates met the extraction criteria in this sample.</div>"""
    cards = []
    for claim in bundle.claims:
        post = by_id.get(claim.post_id)
        cls = _VERDICT_CLASS.get(claim.verdict, "chip-low")
        source = ""
        if post is not None:
            source = (
                f'<div class="sub" style="margin-top:6px">{_e(platform_label(post.platform))} · '
                f'{_e(post.author_name)} · {_e(_fmt_dt(post.published_at))} · '
                f'<a href="{_e(post.url)}">source</a></div>'
            )
        evidence = ""
        if claim.evidence:
            evidence = (
                '<div class="cav" style="color:var(--dim);font-size:11.5px;margin-top:8px">'
                "candidate evidence (unverified):</div>"
                + "".join(
                    f'<div class="cav" style="color:var(--dim);font-size:11.5px">· '
                    f'<a href="{_e(item.url)}">{_e(item.title or item.url)}</a></div>'
                    for item in claim.evidence[:4]
                )
            )
        # An unreviewed claim's "rationale" is the auto-proposal boilerplate,
        # repeated identically on every card. The section note already says
        # automation assigns no verdict, so it is suppressed until a human writes
        # a real one.
        rationale = (
            f'<div class="cav" style="color:var(--amber);font-size:11.5px;margin-top:8px">'
            f"rationale: {_e(claim.rationale)}</div>"
            if claim.rationale and claim.verdict != "unreviewed"
            else ""
        )
        cards.append(
            f"""<div class="card" data-search="{_search_blob(claim.claim_text, claim.verdict)}">
              <div class="hd"><span class="id">{_e(claim.claim_id)}</span>
                {_chip(claim.verdict.replace("_", " "), cls)}</div>
              <div>{_e(claim.claim_text)}</div>
              {source}{evidence}{rationale}
            </div>"""
        )
    unreviewed = sum(1 for c in bundle.claims if c.verdict == "unreviewed")
    return f"""
<h2>Claim review</h2>
<div class="note">Proposed claims awaiting review. {unreviewed} of
{len(bundle.claims)} remain unassessed.</div>
{''.join(cards)}"""


def _spread(bundle: AnalysisBundle) -> str:
    if not bundle.earliest and not bundle.propagation:
        return ""
    parts = []
    if bundle.earliest:
        rows = "".join(
            f"""<tr><td class="mono">{_e(e.narrative_id)}</td>
              <td class="sub">{_e(_fmt_dt(e.observed_at))}</td>
              <td class="sub">{_e(", ".join(e.accounts))}</td>
              <td class="sub">{_e(", ".join(platform_label(p) for p in e.platforms))}</td>
              <td>{len(e.post_ids)}</td></tr>"""
            for e in bundle.earliest
        )
        parts.append(
            f"""<div class="card"><div class="hd"><span class="id">earliest observed</span></div>
            <table><thead><tr><th>Narrative</th><th>First observed</th><th>Accounts</th>
            <th>Platforms</th><th>Posts</th></tr></thead><tbody>{rows}</tbody></table>
            <div class="caveat">↳ {_e(bundle.earliest[0].caveat)}</div></div>"""
        )
    if bundle.propagation:
        rows = "".join(
            f"""<tr><td class="mono">{_e(e.edge_id)}</td><td class="sub">{_e(e.narrative_id)}</td>
              <td class="sub">{_e(e.from_post)} → {_e(e.to_post)}</td>
              <td>{_chip(e.kind)}</td><td class="sub">{_e(humanise(e.signal))}</td>
              <td class="sub">{_e(round(e.seconds_apart, 1) if e.seconds_apart is not None else "—")}</td>
            </tr>"""
            for e in bundle.propagation
        )
        inferred = sum(1 for e in bundle.propagation if e.kind == "inferred")
        note = (
            f'<div class="caveat">↳ {plural(inferred, "inferred edge")} are associations, not '
            "transmission. Both posts may copy a third source outside the collected sample.</div>"
            if inferred
            else ""
        )
        parts.append(
            f"""<div class="card"><div class="hd"><span class="id">propagation edges</span></div>
            <table><thead><tr><th>Edge</th><th>Narrative</th><th>From → to</th><th>Kind</th>
            <th>Signal</th><th>Seconds</th></tr></thead><tbody>{rows}</tbody></table>{note}</div>"""
        )
    return f"""
<h2>Spread</h2>
<div class="note">First observation within the collected sample.</div>
{"".join(parts)}"""


def _coverage(run: Run) -> str:
    rows = "".join(
        f"""<tr><td class="mono">{_e(platform_label(r.platform))}</td><td class="sub">{_e(r.discovery)}</td>
          <td class="sub">{_e(r.retrieval)}</td><td>{r.sources_found}</td>
          <td>{r.posts_retrieved}</td><td>{r.posts_in_range}</td>
          <td>{"✓" if r.truncated else "—"}</td></tr>"""
        for r in run.platforms
    )
    return f"""
<h2>Coverage</h2>
<div class="note">Per-platform retrieval status for this run.</div>
<table><thead><tr><th>Platform</th><th>Discovery</th><th>Retrieval</th><th>Sources</th>
<th>Posts</th><th>In window</th><th>Truncated</th></tr></thead><tbody>{rows}</tbody></table>"""


def _evidence(posts: list[Post], limit: int = 60) -> str:
    ordered = sorted(posts, key=lambda p: p.published_at or p.collected_at, reverse=True)[:limit]
    rows = "".join(
        f"""<tr data-search="{_search_blob(p.text, p.title, p.author_name, p.platform)}">
          <td class="sub mono">{_e(_fmt_dt(p.published_at))}</td>
          <td class="mono">{_e(platform_label(p.platform))}</td>
          <td class="sub">{_e(p.author_name)}</td>
          <td>{_e((p.text or "")[:220])}</td>
          <td>{_adm(p.admiralty.code if p.admiralty else None)}</td>
        </tr>"""
        for p in ordered
    )
    return f"""
<h2>Evidence · {plural(len(posts), "post")}</h2>
<div class="note">Showing the {min(limit, len(posts))} most recent.</div>
<table><thead><tr><th>Published</th><th>Platform</th><th>Account</th><th>Text</th>
<th>Adm.</th></tr></thead><tbody>{rows}</tbody></table>"""


def _methods(bundle: AnalysisBundle) -> str:
    if not bundle.method_notes:
        return ""
    items = "".join(f'<div class="note">· {_e(note)}</div>' for note in bundle.method_notes)
    return f"""
<h2>Method notes</h2>
{items}"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def render(
    run: Run,
    posts: list[Post],
    bundle: AnalysisBundle,
    summary: ModelSummary | None = None,
) -> str:
    """Render one run as a self-contained HTML document.

    ``summary`` is optional. Without it the page is the deterministic brief
    exactly as before; with it, the model's paragraph sits beside the brief's
    opening rather than above it.
    """
    by_id = {p.identity: p for p in posts}
    in_scope = [p for p in posts if p.topic_match]
    brief = build_brief(run, posts, bundle)
    written = _two_up(summary, brief) or _brief(brief)
    body = "".join(
        [
            _header(run, posts, bundle),
            _volume_chart(in_scope),
            written,
            _filter_bar(),
            _assessment(bundle),
            _indicators(bundle),
            _narratives(bundle),
            _coordination(bundle),
            _claims(bundle, by_id),
            _spread(bundle),
            _coverage(run),
            _evidence(posts),
            _methods(bundle),
        ]
    )
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(run.topic)} · SOCMINT case {_e(run.run_id)}</title>
<style>{_CSS}</style>
</head><body><div class="wrap">
{body}
<footer>
  Generated {_e(generated)} · {_e(run.run_id)} · {_e(run.tlp)}<br>
  Extraction is not validation. Coordination is not intent. Coverage is a sample.<br>
  Collected from public sources under each platform's terms.
</footer>
</div><script>{_JS}</script></body></html>"""


def export_site(
    run: Run,
    posts: list[Post],
    bundle: AnalysisBundle,
    out_dir: Path | str,
    summary: ModelSummary | None = None,
) -> Path:
    """Write ``<out_dir>/index.html``. Refuses to overwrite an existing case."""
    target = Path(out_dir)
    index = target / "index.html"
    if index.exists():
        raise FileExistsError(f"{index} already exists; a case file is never overwritten")
    target.mkdir(parents=True, exist_ok=True)
    index.write_text(render(run, posts, bundle, summary=summary), encoding="utf-8")
    return index


__all__ = ["export_site", "render"]
