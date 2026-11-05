"""Static case-file export.

The collector cannot run on Cloudflare or Vercel: it needs a long-lived process,
outbound scraping, PyTorch and SQLite, and neither platform offers that. What
*can* be hosted there is the finished product — one self-contained HTML file per
run, deployable as a static site with no server, no build step and no runtime.

That is also the better demo artifact. A link an assessor can open beats a
Streamlit process on a laptop, and it cannot fail mid-demo because a collector
timed out.

Everything is inlined: CSS, a small amount of JS for filtering, and the data as
JSON. No fonts, no CDNs, no network at view time. Post text is untrusted and is
inserted with ``textContent`` only, never as HTML.
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from .models import AnalysisBundle, IndicatorRole, Post, Run

# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------

_CSS = """
:root{
  --bg:#070B22; --surface:#0C1132; --surface-2:#111735; --line:#1E2749;
  --text:#E6E9F5; --muted:#8B94B8; --accent:#E8234A; --ok:#3FB950;
  --warn:#D29922; --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
  font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:32px 24px 96px}
header{border-bottom:1px solid var(--line);padding-bottom:20px;margin-bottom:28px}
.brand{font:600 22px/1 var(--mono);letter-spacing:-.5px}
.brand span{color:var(--accent)}
.tag{font:500 10px/1 var(--mono);letter-spacing:.18em;text-transform:uppercase;
  color:var(--muted);margin-top:10px}
.meta{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}
.chip{border:1px solid var(--line);background:var(--surface);border-radius:4px;
  padding:5px 9px;font:500 10px/1 var(--mono);letter-spacing:.12em;text-transform:uppercase}
.chip.tlp{border-color:var(--accent);color:var(--accent)}
.topic{font-size:26px;font-weight:600;margin:18px 0 4px;letter-spacing:-.4px}
.sub{color:var(--muted);font-size:13px}
h2{font:600 11px/1 var(--mono);letter-spacing:.2em;text-transform:uppercase;
  color:var(--muted);margin:40px 0 14px;padding-bottom:10px;border-bottom:1px solid var(--line)}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.stat{background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:14px}
.stat .n{font:600 26px/1 var(--mono);letter-spacing:-1px}
.stat .k{font:500 9px/1 var(--mono);letter-spacing:.16em;text-transform:uppercase;
  color:var(--muted);margin-top:8px}
.stat.alert .n{color:var(--accent)}
.card{background:var(--surface);border:1px solid var(--line);border-radius:6px;
  padding:16px;margin-bottom:10px}
.card .hd{display:flex;justify-content:space-between;gap:12px;align-items:baseline;
  margin-bottom:8px;flex-wrap:wrap}
.card .id{font:600 11px/1 var(--mono);letter-spacing:.1em;color:var(--accent)}
.conf{font:500 9px/1 var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--muted)}
.caveat{color:var(--muted);font-size:12px;margin-top:8px;padding-left:12px;
  border-left:2px solid var(--line)}
.mono{font-family:var(--mono);font-size:13px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;font:500 9px/1 var(--mono);letter-spacing:.16em;text-transform:uppercase;
  color:var(--muted);padding:8px 10px;border-bottom:1px solid var(--line)}
td{padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:none}
.badge{display:inline-block;padding:3px 7px;border-radius:3px;font:600 9px/1 var(--mono);
  letter-spacing:.1em;text-transform:uppercase;border:1px solid}
.b-ind{border-color:var(--accent);color:var(--accent)}
.b-ref{border-color:var(--line);color:var(--muted)}
.b-unk{border-color:var(--warn);color:var(--warn)}
.b-ok{border-color:var(--ok);color:var(--ok)}
.tools{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0}
input[type=search]{background:var(--surface);border:1px solid var(--line);color:var(--text);
  border-radius:4px;padding:8px 12px;font-size:13px;min-width:240px;font-family:inherit}
input[type=search]:focus{outline:none;border-color:var(--accent)}
.note{background:var(--surface-2);border-left:3px solid var(--warn);padding:12px 14px;
  border-radius:0 4px 4px 0;font-size:13px;color:var(--muted);margin-bottom:16px}
.empty{color:var(--muted);font-size:13px;padding:16px 0}
footer{margin-top:56px;padding-top:20px;border-top:1px solid var(--line);
  color:var(--muted);font-size:11px;font-family:var(--mono);line-height:1.8}
a{color:var(--text);text-decoration:none;border-bottom:1px solid var(--line)}
a:hover{border-color:var(--accent)}
"""

_JS = """
(function(){
  var q=document.getElementById('q');
  if(!q) return;
  q.addEventListener('input',function(){
    var v=q.value.toLowerCase();
    document.querySelectorAll('[data-search]').forEach(function(row){
      var hit=!v||row.getAttribute('data-search').indexOf(v)>-1;
      row.style.display=hit?'':'none';
    });
  });
})();
"""


def _e(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


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
    parts = [f"{n} {name.replace('_', ' ')}" for name, n in sorted(counts.items())]
    return " · ".join(parts)


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def _header(run: Run, posts: list[Post]) -> str:
    dated = sum(1 for p in posts if p.published_at)
    return f"""
<header>
  <div class="brand">&gt; socmint<span>.</span></div>
  <div class="tag">Social threat intelligence · case file</div>
  <div class="topic">{_e(run.topic)}</div>
  <div class="sub">Window {_e(run.window.start.date())} → {_e(run.window.end.date())} UTC ·
    {len(posts)} post(s) collected · {dated} dated</div>
  <div class="meta">
    <span class="chip tlp">{_e(run.tlp)}</span>
    <span class="chip">{_e(str(run.case_template).replace("_", " "))}</span>
    <span class="chip">{_e(run.run_id)}</span>
    <span class="chip">status {_e(run.status)}</span>
  </div>
</header>"""


def _assessment(bundle: AnalysisBundle) -> str:
    a = bundle.assessment
    if a is None:
        return ""
    judgements = "".join(
        f"""<div class="card">
          <div class="hd"><span class="id">{_e(k.judgement_id)}</span>
            <span class="conf">{_e(k.confidence)} confidence</span></div>
          <div>{_e(k.statement)}</div>
          {"".join(f'<div class="caveat">↳ {_e(c)}</div>' for c in k.caveats)}
        </div>"""
        for k in a.key_judgements
    )
    factors = "".join(f'<div class="caveat">· {_e(f)}</div>' for f in a.risk_factors)
    return f"""
<h2>Assessment</h2>
<div class="stats">
  <div class="stat alert"><div class="n">{a.risk_score}</div>
    <div class="k">Triage risk / 100</div></div>
  <div class="stat"><div class="n">{len(a.key_judgements)}</div>
    <div class="k">Key judgements</div></div>
  <div class="stat"><div class="n">{len(bundle.indicators)}</div>
    <div class="k">Technical values</div></div>
  <div class="stat"><div class="n">{len(bundle.coordination)}</div>
    <div class="k">Coordination groups</div></div>
</div>
<div class="note">Risk is an additive triage heuristic, not a probability. It answers
which run to read first, and can be recomputed by hand from the factors below.</div>
{judgements}
<div class="card"><div class="hd"><span class="id">risk factors</span></div>{factors}</div>"""


def _indicators(bundle: AnalysisBundle) -> str:
    if not bundle.indicators:
        return ""
    rows = []
    for ind in bundle.indicators:
        role = str(ind.role)
        cls = {"indicator": "b-ind", "reference": "b-ref"}.get(role, "b-unk")
        rows.append(
            f"""<tr data-search="{_e((ind.defanged + ' ' + ind.value).lower())}">
              <td class="mono">{_e(ind.defanged)}</td>
              <td><span class="badge {cls}">{_e(role)}</span></td>
              <td class="sub">{_e(ind.kind)}</td>
              <td class="sub">{_e(ind.role_reason)}</td>
              <td class="sub">{len(ind.post_ids)}</td>
            </tr>"""
        )
    actionable = sum(1 for i in bundle.indicators if i.role is IndicatorRole.INDICATOR)
    refs = sum(1 for i in bundle.indicators if i.role is IndicatorRole.REFERENCE)
    return f"""
<h2>Indicators</h2>
<div class="stats">
  <div class="stat alert"><div class="n">{actionable}</div><div class="k">Indicators</div></div>
  <div class="stat"><div class="n">{refs}</div><div class="k">Cited references</div></div>
  <div class="stat"><div class="n">{len(bundle.indicators)}</div><div class="k">Total values</div></div>
</div>
<div class="note">A mentioned value is not evidence of compromise. <b>indicator</b> means
the post presents it as an artefact of compromise; <b>reference</b> means cited
documentation or a vendor advisory; <b>unknown</b> means the post gives no signal.
Nothing here was checked against reputation or hosting data. All values are defanged.</div>
<div class="tools"><input type="search" id="q" placeholder="Filter values…"></div>
<table><thead><tr><th>Value</th><th>Role</th><th>Kind</th><th>Why</th><th>Posts</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>"""


def _narratives(bundle: AnalysisBundle) -> str:
    if not bundle.narratives:
        return ""
    cards = "".join(
        f"""<div class="card">
          <div class="hd"><span class="id">{_e(n.narrative_id)}</span>
            <span class="conf">{_e(_fmt_framing(n.framing))}</span></div>
          <div><b>{_e(n.label)}</b></div>
          <div class="sub">{len(n.member_ids)} post(s) · {_e(_fmt_counts(n.platform_counts))}</div>
          {"".join(f'<div class="caveat">↳ {_e(e)}</div>' for e in n.framing_evidence[:4])}
        </div>"""
        for n in bundle.narratives
    )
    return f"""
<h2>Narrative candidates</h2>
<div class="note">A cluster is a semantic grouping, not a confirmed narrative or a
common stance. Opposing posts often share vocabulary and cluster together.</div>
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
            <span class="conf">{_e(str(g.confidence).replace("_", " "))} ·
              {len(g.episodes)} episode(s) · baseline {_e(g.baseline_episodes)}</span></div>
          <div class="mono">{_e(", ".join(g.account_ids))}</div>
          {"".join(f'<div class="caveat">↳ {_e(b)}</div>' for b in g.benign_explanations[:3])}
        </div>"""
        for g in bundle.coordination
    )
    return f"""
<h2>Coordination candidates</h2>
<div class="note">A group is a set of accounts whose posts repeatedly matched. It is
not a probability, not a bot label, and not evidence of shared ownership or intent.</div>
{cards}"""


def _coverage(run: Run) -> str:
    rows = "".join(
        f"""<tr><td class="mono">{_e(r.platform)}</td><td class="sub">{_e(r.discovery)}</td>
          <td class="sub">{_e(r.retrieval)}</td><td>{r.sources_found}</td>
          <td>{r.posts_retrieved}</td><td>{r.posts_in_range}</td></tr>"""
        for r in run.platforms
    )
    return f"""
<h2>Coverage</h2>
<div class="note">Status is bounded by budget. "ok" means the collector completed
within its caps, not that the platform was exhausted. Empty does not mean nothing
was posted.</div>
<table><thead><tr><th>Platform</th><th>Discovery</th><th>Retrieval</th><th>Sources</th>
<th>Posts</th><th>In window</th></tr></thead><tbody>{rows}</tbody></table>"""


def _evidence(posts: list[Post], limit: int = 60) -> str:
    ordered = sorted(posts, key=lambda p: p.published_at or p.collected_at, reverse=True)[:limit]
    rows = "".join(
        f"""<tr data-search="{_e(((p.text or '') + ' ' + (p.author_name or '')).lower())}">
          <td class="sub mono">{_e(_fmt_dt(p.published_at))}</td>
          <td class="mono">{_e(p.platform)}</td>
          <td class="sub">{_e(p.author_name)}</td>
          <td>{_e((p.text or "")[:220])}</td>
          <td class="sub mono">{_e(p.admiralty.code if p.admiralty else "—")}</td>
        </tr>"""
        for p in ordered
    )
    return f"""
<h2>Evidence · {len(posts)} post(s)</h2>
<div class="note">Showing the {min(limit, len(posts))} most recent. Post text is
untrusted data, rendered as text. Admiralty grades are provisional proxies.</div>
<table><thead><tr><th>Published</th><th>Platform</th><th>Account</th><th>Text</th>
<th>Adm.</th></tr></thead><tbody>{rows}</tbody></table>"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def render(run: Run, posts: list[Post], bundle: AnalysisBundle) -> str:
    """Render one run as a self-contained HTML document."""
    body = "".join(
        [
            _header(run, posts),
            _assessment(bundle),
            _indicators(bundle),
            _narratives(bundle),
            _coordination(bundle),
            _coverage(run),
            _evidence(posts),
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
    run: Run, posts: list[Post], bundle: AnalysisBundle, out_dir: Path | str
) -> Path:
    """Write ``<out_dir>/index.html``. Refuses to overwrite an existing case."""
    target = Path(out_dir)
    index = target / "index.html"
    if index.exists():
        raise FileExistsError(f"{index} already exists; a case file is never overwritten")
    target.mkdir(parents=True, exist_ok=True)
    index.write_text(render(run, posts, bundle), encoding="utf-8")
    return index


__all__ = ["export_site", "render"]
