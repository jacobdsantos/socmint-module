"""Local analyst dashboard.

Design intent: a case file an analyst reads, not an influence-graph poster. Deep navy
with a crimson signal colour, monospace for anything machine-generated
(indicators, hashes, Admiralty codes), and a terminal prompt in the masthead.
Every number is click-through to the posts behind it, and every automated
judgement stays visibly provisional until a reviewer acts.

Run with:  streamlit run app.py
Binds to loopback only; this is not a hosted service.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from socmint import pipeline
from socmint.analysis.brief import Brief, build_brief
from socmint.analysis.narratives import volume_series
from socmint.cli import _parse_bound
from socmint.envfile import load_env_file
from socmint.exports import export
from socmint.models import TLP, CaseTemplate, DateRange
from socmint.presets import PRESETS, catalogue
from socmint.prose import humanise, kind_label, platform_label, plural
from socmint.settings import AppConfig
from socmint.store import Store

DATA_DIR = Path("data")
DB = DATA_DIR / "socmint.db"

# The dashboard is started by a human who expects the .env in the project root
# to be honoured. Without this, a key exported in another shell makes the CLI
# work and leaves X reporting "unsupported" here.
load_env_file(Path(__file__).parent / ".env")

st.set_page_config(
    page_title="SOCMINT · Case Workbench",
    page_icon="◆",
    layout="wide",
    # "auto" keeps the sidebar open on a laptop but collapses it on a narrow
    # screen, where an expanded sidebar would take half the viewport.
    initial_sidebar_state="auto",
)

CSS = """
<style>
  :root {
    --void: #050818; --navy: #070B22; --navy2: #0C1132; --navy3: #111845;
    --line: #1E2657; --line-hot: #2E3A7A;
    --ink: #E6E9F5; --muted: #8A93BF; --dim: #5A6394;
    --crimson: #E8234A; --crimson-soft: rgba(232,35,74,.12);
    --amber: #F2A33C; --mint: #35D6A4; --violet: #8B7CF6;
    --mono: ui-monospace, "Cascadia Code", "SF Mono", Menlo, Consolas, monospace;
    --sans: -apple-system, "Segoe UI", Inter, Roboto, Helvetica, Arial, sans-serif;
  }

  html, body, .stApp {
    background:
      radial-gradient(1200px 600px at 82% -10%, rgba(232,35,74,.10), transparent 60%),
      radial-gradient(900px 500px at 0% 0%, rgba(80,110,255,.12), transparent 55%),
      var(--navy) !important;
    font-family: var(--sans);
  }
  .stApp > header { background: transparent !important; }
  .block-container { padding-top: 2.2rem; max-width: 1500px; }

  /* ---------- masthead ---------- */
  .masthead {
    display: flex; align-items: flex-end; justify-content: space-between;
    gap: 24px; padding: 4px 0 18px 0; margin-bottom: 6px;
    border-bottom: 1px solid var(--line);
  }
  .prompt {
    font-family: var(--mono); font-size: 34px; font-weight: 600;
    letter-spacing: -0.03em; color: var(--ink); line-height: 1;
  }
  .prompt .sigil { color: var(--crimson); }
  .prompt .caret {
    display: inline-block; width: 11px; height: 26px; margin-left: 4px;
    background: var(--crimson); vertical-align: -3px;
    animation: blink 1.15s steps(1) infinite;
  }
  @keyframes blink { 0%,55% {opacity:1} 56%,100% {opacity:0} }
  .mh-sub {
    font-size: 10.5px; letter-spacing: .26em; text-transform: uppercase;
    color: var(--dim); margin-top: 9px; font-weight: 600;
  }
  .mh-right { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }

  /* ---------- badges ---------- */
  .tlp, .badge, .chip, .adm {
    font-family: var(--mono); font-size: 10.5px; font-weight: 600;
    letter-spacing: .12em; text-transform: uppercase;
    padding: 4px 9px; border-radius: 3px; white-space: nowrap;
  }
  .badge { color: var(--muted); border: 1px solid var(--line-hot); background: rgba(17,24,69,.6); }
  .tlp { color: #fff; background: var(--crimson); }
  .tlp-clear { background: #3B4A8C; }
  .tlp-green { background: #1E7A5A; }
  .tlp-amber { background: #B8761C; }
  .tlp-amber-strict { background: #C2540F; }
  .tlp-red { background: var(--crimson); }

  .chip { border: 1px solid var(--line-hot); color: var(--muted); background: transparent; }
  .chip-high { color: #FF6B87; border-color: #7A1B2E; background: var(--crimson-soft); }
  .chip-moderate { color: var(--amber); border-color: #6B4A12; background: rgba(242,163,60,.10); }
  .chip-low { color: var(--muted); border-color: var(--line-hot); }
  .chip-mint { color: var(--mint); border-color: #14543F; background: rgba(53,214,164,.10); }

  .adm {
    color: var(--ink); border: 1px solid var(--line-hot);
    background: var(--navy3); letter-spacing: .06em;
  }
  .adm-a, .adm-b, .adm-c { border-color: #14543F; color: var(--mint); }
  .adm-d { border-color: #6B4A12; color: var(--amber); }
  .adm-e, .adm-f { border-color: #7A1B2E; color: #FF6B87; }

  /* ---------- stat tiles ---------- */
  .stat {
    background: linear-gradient(180deg, rgba(17,24,69,.85), rgba(12,17,50,.85));
    border: 1px solid var(--line); border-radius: 4px;
    padding: 14px 15px 13px; height: 100%; position: relative; overflow: hidden;
  }
  .stat::before {
    content: ""; position: absolute; left: 0; top: 0; width: 2px; height: 100%;
    background: var(--crimson); opacity: .85;
  }
  .stat .k {
    font-size: 9.5px; letter-spacing: .2em; text-transform: uppercase;
    color: var(--dim); font-weight: 700;
  }
  .stat .v {
    font-family: var(--mono); font-size: 27px; font-weight: 600;
    color: var(--ink); line-height: 1.15; margin-top: 5px;
  }
  .stat .s { font-size: 10.5px; color: var(--muted); margin-top: 3px; }

  /* ---------- risk ---------- */
  .risk {
    background: linear-gradient(135deg, rgba(232,35,74,.14), rgba(12,17,50,.9) 55%);
    border: 1px solid #3A1530; border-radius: 4px; padding: 18px 20px; height: 100%;
  }
  .risk .k {
    font-size: 9.5px; letter-spacing: .22em; text-transform: uppercase;
    color: #FF8FA5; font-weight: 700;
  }
  .risk .v { font-family: var(--mono); font-size: 44px; font-weight: 600; color: var(--ink); line-height: 1; margin: 8px 0 2px; }
  .risk .v span { font-size: 15px; color: var(--dim); }
  .bar { height: 3px; background: var(--navy3); border-radius: 2px; overflow: hidden; margin-top: 12px; }
  .bar > i { display: block; height: 100%; background: linear-gradient(90deg, var(--crimson), #FF7A93); }
  .risk .n { font-size: 10.5px; color: var(--muted); margin-top: 9px; line-height: 1.5; }

  /* ---------- key judgements ---------- */
  .kj {
    border-left: 2px solid var(--line-hot); padding: 10px 0 10px 14px; margin: 0 0 14px 0;
  }
  .kj.hi { border-left-color: var(--crimson); }
  .kj .head { display: flex; gap: 10px; align-items: center; margin-bottom: 5px; }
  .kj .kid { font-family: var(--mono); font-size: 10.5px; letter-spacing: .12em; color: var(--dim); }
  .kj .body { color: var(--ink); font-size: 14px; line-height: 1.55; }
  .kj .cav { color: var(--dim); font-size: 11.5px; line-height: 1.5; margin-top: 6px; }

  /* ---------- evidence ---------- */
  .ev {
    border-left: 2px solid var(--line); padding: 2px 0 2px 12px; margin: 10px 0;
  }
  .ev .q { color: var(--ink); font-size: 13px; line-height: 1.55; }
  .ev .m { color: var(--dim); font-size: 10.5px; font-family: var(--mono); margin-top: 4px; letter-spacing: .04em; }
  .ev .m a { color: #7E93FF; text-decoration: none; border-bottom: 1px dotted #3A4A9E; }

  /* ---------- indicators ---------- */
  .ioc {
    font-family: var(--mono); font-size: 12px; color: var(--ink);
    background: var(--void); border: 1px solid var(--line); border-radius: 3px;
    padding: 9px 11px; margin: 5px 0; display: flex;
    gap: 16px; align-items: baseline;
  }
  .ioc .val { flex: 1 1 auto; min-width: 0; word-break: break-all; line-height: 1.45; }
  .ioc .meta {
    flex: 0 0 auto; color: var(--dim); font-size: 9.5px; white-space: nowrap;
    letter-spacing: .1em; text-transform: uppercase;
  }

  /* ---------- written brief ---------- */
  .standfirst {
    color: var(--muted); font-size: 14px; line-height: 1.65;
    max-width: 74ch; margin: 2px 0 26px 0;
  }
  .brief-h {
    font-size: 13px !important; font-weight: 650; letter-spacing: -0.005em;
    color: var(--ink) !important; margin: 30px 0 10px 0;
    padding-bottom: 8px; border-bottom: 1px solid var(--line);
  }
  .brief-p {
    color: #C9CFE6; font-size: 13.5px; line-height: 1.72;
    max-width: 74ch; margin: 0 0 12px 0;
  }
  .brief-ul { max-width: 74ch; margin: 2px 0 16px 0; padding-left: 17px; }
  .brief-ul li {
    color: var(--muted); font-size: 12.5px; line-height: 1.68;
    margin-bottom: 6px;
  }
  .brief-ul li::marker { color: var(--crimson); }

  /* ---------- section headers ---------- */
  .sec {
    font-size: 10px; letter-spacing: .24em; text-transform: uppercase;
    color: var(--dim); font-weight: 700; margin: 26px 0 10px 0;
    padding-bottom: 7px; border-bottom: 1px solid var(--line);
  }
  .note { color: var(--muted); font-size: 12px; line-height: 1.6; }
  .caveat { color: var(--amber); font-size: 12px; line-height: 1.55; }

  /* ---------- streamlit chrome ---------- */
  h1, h2, h3, h4 { color: var(--ink) !important; letter-spacing: -0.01em; }
  [data-testid="stSidebar"] {
    background: var(--void) !important; border-right: 1px solid var(--line);
  }
  [data-testid="stSidebar"] h3 {
    font-family: var(--mono) !important; font-size: 13px !important;
    letter-spacing: .06em; color: var(--ink) !important;
  }
  /* Streamlit 1.66 renders tabs and chips with react-aria, not BaseWeb: the
     hooks are [role="tablist"] / [data-testid="stTab"] and
     [data-testid="stMultiSelectTagsContainer"]. BaseWeb selectors match nothing. */
  .stTabs [role="tablist"] {
    gap: 2px; border-bottom: 1px solid var(--line); background: transparent;
  }
  .stTabs [data-testid="stTab"] {
    font-family: var(--mono); font-size: 11px; letter-spacing: .14em;
    text-transform: uppercase; color: var(--dim);
    background: transparent; border-radius: 0; padding: 10px 14px;
  }
  .stTabs [data-testid="stTab"] p {
    font-family: var(--mono); font-size: 11px; letter-spacing: .14em;
    text-transform: uppercase;
  }
  .stTabs [aria-selected="true"] {
    color: var(--ink) !important; background: rgba(232,35,74,.08) !important;
    box-shadow: inset 0 -2px 0 var(--crimson);
  }
  .stTabs [aria-selected="true"] p { color: var(--ink) !important; }
  div[data-testid="stExpander"] details {
    border: 1px solid var(--line) !important; border-radius: 4px !important;
    background: rgba(12,17,50,.55) !important;
  }
  div[data-testid="stExpander"] summary { font-size: 13px; }
  .stDataFrame { border: 1px solid var(--line); border-radius: 4px; }
  .stButton > button {
    font-family: var(--mono); font-size: 11px; letter-spacing: .1em;
    text-transform: uppercase; border-radius: 3px;
    border: 1px solid var(--line-hot); background: var(--navy3); color: var(--ink);
  }
  .stButton > button:hover { border-color: var(--crimson); color: #fff; }
  .stButton > button[kind="primary"] { background: var(--crimson); border-color: var(--crimson); color: #fff; }
  hr { border-color: var(--line) !important; }

  /* Streamlit alerts default to a saturated mustard that fights the palette.
     Recolour to navy panels with a semantic left rule. */
  [data-testid="stAlert"] {
    background: var(--navy2) !important; border: 1px solid var(--line) !important;
    border-left: 3px solid var(--amber) !important; border-radius: 3px !important;
    color: var(--ink) !important;
  }
  [data-testid="stAlert"] p { color: var(--ink) !important; font-size: 13px; line-height: 1.6; }
  [data-testid="stAlert"] svg { display: none; }
  [data-testid="stAlertContainer"] { background: transparent !important; }
  [data-testid="stSidebar"] [data-testid="stAlert"] { border-left-color: var(--crimson) !important; }
  [data-testid="stSidebar"] input, [data-testid="stSidebar"] textarea {
    font-family: var(--mono) !important; font-size: 12px !important;
  }
  [data-testid="stSidebar"] [data-testid="stSelectbox"] { font-size: 12px; }
  [data-testid="stSidebar"] .stCaption, [data-testid="stSidebar"] small { color: var(--dim) !important; }

  /* Multiselect tokens default to a solid primary fill, which reads as an alert.
     Make them quiet chips: navy fill, crimson rule. */
  [data-testid="stMultiSelectTagsContainer"] > span[role="group"] > span {
    background: var(--navy3) !important; border: 1px solid var(--crimson) !important;
    color: var(--ink) !important; border-radius: 3px !important;
  }
  [data-testid="stMultiSelectTagsContainer"] > span[role="group"] > span span {
    color: var(--ink) !important; font-family: var(--mono);
    font-size: 10.5px; letter-spacing: .08em; text-transform: uppercase;
  }
  [data-testid="stMultiSelectTagsContainer"] > span[role="group"] > span svg {
    fill: var(--crimson) !important; color: var(--crimson) !important;
  }

  .stDownloadButton > button {
    font-family: var(--mono); font-size: 11px; letter-spacing: .1em;
    text-transform: uppercase; border-radius: 3px;
    border: 1px solid var(--line-hot); background: var(--navy3); color: var(--ink);
  }
  .stDownloadButton > button:hover { border-color: var(--crimson); color: #fff; }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Small render helpers
# ---------------------------------------------------------------------------


def _esc(text: object) -> str:
    """Escape untrusted post text before it reaches HTML."""
    return (
        str(text if text is not None else "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _tlp_class(marking: str) -> str:
    return {
        "TLP:CLEAR": "tlp-clear",
        "TLP:GREEN": "tlp-green",
        "TLP:AMBER": "tlp-amber",
        "TLP:AMBER+STRICT": "tlp-amber-strict",
        "TLP:RED": "tlp-red",
    }.get(marking, "tlp-amber")


def _stat(label: str, value, sub: str = "") -> str:
    return (
        f'<div class="stat"><div class="k">{_esc(label)}</div>'
        f'<div class="v">{_esc(value)}</div>'
        f'<div class="s">{_esc(sub)}</div></div>'
    )


def _conf_chip(confidence: str) -> str:
    cls = {"high": "chip-high", "moderate": "chip-moderate", "medium": "chip-moderate"}.get(
        confidence, "chip-low"
    )
    return f'<span class="chip {cls}">{_esc(confidence)} confidence</span>'


def _adm_badge(code: str | None) -> str:
    if not code:
        return '<span class="adm adm-f">—</span>'
    return f'<span class="adm adm-{code[0].lower()}">{_esc(code)}</span>'


def _evidence_card(post, quote: str | None = None) -> str:
    rating = _adm_badge(post.admiralty.code if post.admiralty else None)
    body = _esc((quote if quote is not None else (post.title or post.text or ""))[:320])
    stamp = post.published_at.strftime("%Y-%m-%d %H:%M UTC") if post.published_at else "undated"
    return (
        f'<div class="ev"><div class="q">{body}</div>'
        f'<div class="m">{_esc(platform_label(post.platform))} · {_esc(post.author_name)} · {stamp} · '
        f'{rating} · <a href="{_esc(post.url)}">source</a></div></div>'
    )


# ---------------------------------------------------------------------------
# Overview — the one page someone reads when they only read one page
# ---------------------------------------------------------------------------

_PLATFORM_COLOURS = {
    "mastodon": "#8B7CF6",
    "telegram": "#35D6A4",
    "youtube": "#E8234A",
    "google_news": "#F2A33C",
    "x": "#7E93FF",
    "gdelt": "#5A6394",
}


def _platform_colour(name: str) -> str:
    return _PLATFORM_COLOURS.get(name, "#5A6394")


def _blank_figure(height: int) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        height=height,
        margin=dict(l=0, r=0, t=4, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#8A93BF", size=10, family="ui-monospace, Menlo, monospace"),
        showlegend=False,
        bargap=0.18,
    )
    fig.update_xaxes(showgrid=False, zeroline=False, showline=False, tickfont=dict(size=9))
    fig.update_yaxes(
        showgrid=True, gridcolor="#1E2657", zeroline=False, showline=False, tickfont=dict(size=9)
    )
    return fig


def _volume_chart(posts, *, height: int = 220, legend: bool = False) -> go.Figure | None:
    """Posts per day, stacked by platform. Undated posts are excluded by design."""
    series = volume_series(posts)
    if not series:
        return None
    buckets = sorted({row["bucket"] for row in series})
    platforms = sorted({row["platform"] for row in series})
    lookup = {(row["bucket"], row["platform"]): row["count"] for row in series}

    fig = _blank_figure(height)
    for platform in platforms:
        fig.add_trace(
            go.Bar(
                x=buckets,
                y=[lookup.get((bucket, platform), 0) for bucket in buckets],
                name=platform_label(platform),
                marker=dict(color=_platform_colour(platform), line=dict(width=0)),
                hovertemplate="%{x}<br>%{y} posts<extra>"
                + platform_label(platform) + "</extra>",
            )
        )
    fig.update_layout(
        barmode="stack",
        showlegend=legend,
        legend=dict(orientation="h", y=1.14, x=0, bgcolor="rgba(0,0,0,0)"),
    )
    # The window is in the header; repeating the year on every tick is noise.
    fig.update_xaxes(tickformat="%-d %b")
    return fig


def _sparkline(posts) -> go.Figure | None:
    """A trend, not a bar chart: at 48px tall, bars read as a barcode."""
    series = volume_series(posts)
    if not series:
        return None
    buckets = sorted({row["bucket"] for row in series})
    totals: Counter[str] = Counter()
    for row in series:
        totals[row["bucket"]] += row["count"]

    fig = _blank_figure(48)
    fig.add_trace(
        go.Scatter(
            x=buckets,
            y=[totals[bucket] for bucket in buckets],
            mode="lines",
            line=dict(color="#E8234A", width=1.6),
            fill="tozeroy",
            fillcolor="rgba(232,35,74,0.16)",
            hovertemplate="%{x}: %{y} posts<extra></extra>",
        )
    )
    fig.update_layout(margin=dict(l=0, r=0, t=0, b=0))
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return fig


def _platform_chips(counts: dict[str, int]) -> str:
    if not counts:
        return '<span class="chip">no platform data</span>'
    return " ".join(
        f'<span class="chip" style="border-color:{_platform_colour(name)}55">'
        f"{_esc(name.replace('_', ' '))} {count}</span>"
        for name, count in sorted(counts.items(), key=lambda kv: -kv[1])
    )


def _members(narrative, posts_by_id: dict) -> list:
    return [posts_by_id[i] for i in narrative.member_ids if i in posts_by_id]


def _render_brief(brief: Brief) -> None:
    """The written assessment: prose first, evidence behind it."""
    if brief.standfirst:
        st.markdown(
            f'<p class="standfirst">{_esc(brief.standfirst)}</p>', unsafe_allow_html=True
        )
    for section in brief.sections:
        st.markdown(
            f'<h3 class="brief-h">{_esc(section.heading)}</h3>', unsafe_allow_html=True
        )
        for paragraph in section.paragraphs:
            st.markdown(
                f'<p class="brief-p">{_esc(paragraph)}</p>', unsafe_allow_html=True
            )
        if section.bullets:
            items = "".join(f"<li>{_esc(b)}</li>" for b in section.bullets)
            st.markdown(f'<ul class="brief-ul">{items}</ul>', unsafe_allow_html=True)


def _render_overview(run, posts, in_scope, bundle) -> None:
    assessment = bundle.assessment
    actionable = sum(1 for i in bundle.indicators if i.role.value == "indicator")

    tiles = st.columns(5)
    tiles[0].markdown(
        _stat("Triage risk", f"{assessment.risk_score}" if assessment else "—", "of 100"),
        unsafe_allow_html=True,
    )
    tiles[1].markdown(
        _stat("Narratives", len(bundle.narratives), "semantic groups"),
        unsafe_allow_html=True,
    )
    tiles[2].markdown(
        _stat("Account groups", len(bundle.coordination), "candidates"),
        unsafe_allow_html=True,
    )
    tiles[3].markdown(
        _stat("Indicators", actionable, "actionable values"),
        unsafe_allow_html=True,
    )
    tiles[4].markdown(
        _stat("Claims", len(bundle.claims),
              f"{sum(1 for c in bundle.claims if c.verdict == 'unreviewed')} pending"),
        unsafe_allow_html=True,
    )

    chart = _volume_chart(in_scope, legend=True)
    if chart is not None:
        st.plotly_chart(chart, use_container_width=True, config={"displayModeBar": False})

    _render_brief(build_brief(run, posts, bundle))


@st.cache_resource
def get_store() -> Store:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return Store(DB)


def _run_collection(
    topic: str,
    start: date,
    end: date,
    tz_name: str,
    aliases: list[str],
    max_posts: int,
    offline: bool,
    retrieve_evidence: bool,
    case_template: CaseTemplate,
    tlp: TLP | None,
    include_x: bool = False,
    allow_spend: bool = False,
    x_max_results: int = 10,
    x_cooldown: float = 30.0,
):
    tz = ZoneInfo(tz_name)
    window = DateRange(
        start=_parse_bound(start.isoformat(), tz, is_end=False),
        end=_parse_bound(end.isoformat(), tz, is_end=True),
    )
    from dataclasses import replace

    config = AppConfig(data_dir=DATA_DIR)
    config = replace(
        config,
        budgets=replace(config.budgets, max_posts_per_run=max_posts, enable_x=include_x),
    )
    if include_x:
        # Spending stays off unless the operator ticked it. X without permission
        # is not an error: the collector reports blocked_policy and the coverage
        # table shows why, which is the behaviour worth demonstrating.
        config = replace(
            config,
            credits=replace(
                config.credits,
                allow_spend=allow_spend,
                max_results_per_query=max(1, int(x_max_results)),
                min_seconds_between_calls=max(0.0, float(x_cooldown)),
            ),
        )
    if offline:
        config = replace(config, analysis=replace(config.analysis, force_lexical=True))

    store = get_store()
    return pipeline.execute(
        topic=topic,
        window=window,
        config=config,
        aliases=aliases,
        case_template=case_template,
        tlp=tlp,
        store=store,
        include_claims=True,
        retrieve_evidence=retrieve_evidence,
    )


# ---------------------------------------------------------------------------
# Sidebar: run configuration
# ---------------------------------------------------------------------------

st.sidebar.markdown("### <span style='color:#E8234A'>&gt;</span> new case", unsafe_allow_html=True)

with st.sidebar.form("run_form"):
    # Three inputs are all a demo needs. Everything else has a defensible
    # default, so it lives behind one expander rather than competing for
    # attention with the two fields that actually decide the case.
    topic = st.text_input("Topic", placeholder="Acme Bank collapse")
    start = st.date_input("Start (inclusive)", value=date.today() - timedelta(days=14))
    end = st.date_input("End (inclusive)", value=date.today() - timedelta(days=1))

    with st.expander("Advanced"):
        alias_text = st.text_input("Aliases (comma separated)", placeholder="lindol, PH quake")
        case_label = st.selectbox(
            "Case template",
            [entry["label"] for entry in catalogue()],
            index=0,
            help="Frames the assessment and the threat patterns in focus.",
        )
        tlp_label = st.selectbox(
            "TLP marking",
            ["auto (from template)"] + [marking.value for marking in TLP],
            index=0,
        )
        tz_name = st.selectbox(
            "Timezone", ["UTC", "Asia/Manila", "Asia/Singapore", "America/New_York"]
        )
        max_posts = st.slider("Post limit", 50, 800, 250, step=50)
        offline = st.checkbox("Fast mode", value=False, help="Groups by word overlap instead of semantics.")
        retrieve_evidence = st.checkbox("Retrieve candidate evidence for claims", value=False)

    submitted = st.form_submit_button("Run collection", type="primary")

# Outside the form on purpose: a form batches its inputs, so a checkbox that
# reveals more fields would only update on submit. Out here it reruns and the
# controls appear immediately.
with st.sidebar.expander("X / Twitter"):
    include_x = st.checkbox(
        "Include X in this run",
        value=False,
        help="Adds X posts to this run.",
    )
    if include_x:
        _config = AppConfig(data_dir=DATA_DIR)
        if not _config.xpoz.configured:
            st.warning("X is not configured. Add an API key to enable it.")
        allow_spend = st.checkbox("Allow paid queries", value=False)
        x_max_results = st.number_input("Posts per query", 1, 50, 10, step=1)
        x_cooldown = st.number_input("Seconds between queries", 0, 600, 30, step=10)
    else:
        allow_spend, x_max_results, x_cooldown = False, 10, 30.0

if submitted:
    if not topic.strip():
        st.sidebar.error("Enter a topic.")
    elif end < start:
        st.sidebar.error("End date must be on or after start date.")
    else:
        aliases = [a.strip() for a in alias_text.split(",") if a.strip()]
        template = next(
            key for key, preset in PRESETS.items() if preset.label == case_label
        )
        marking = None if tlp_label.startswith("auto") else TLP(tlp_label)
        with st.spinner("Collecting and analysing… this can take a few minutes"):
            run, posts, bundle = _run_collection(
                topic.strip(), start, end, tz_name, aliases, max_posts,
                offline, retrieve_evidence, template, marking,
                include_x=include_x, allow_spend=allow_spend,
                x_max_results=x_max_results, x_cooldown=x_cooldown,
            )
        st.session_state["run_id"] = run.run_id
        st.session_state["flash"] = (
            f"Run {run.run_id} finished with status **{run.status}** "
            f"({len(posts)} posts collected)."
        )

store = get_store()
runs = store.list_runs(limit=30)
if not runs:
    st.markdown(
        '<div class="masthead"><div><div class="prompt">'
        '<span class="sigil">&gt;</span> socmint<span class="caret"></span></div>'
        '<div class="mh-sub">Social threat intelligence</div></div></div>',
        unsafe_allow_html=True,
    )
    st.info("No runs yet. Configure a topic and date range in the sidebar, then run a collection.")
    st.stop()

run_ids = [r.run_id for r in runs]
default_id = st.session_state.get("run_id") if st.session_state.get("run_id") in run_ids else run_ids[0]
selected = st.sidebar.selectbox(
    "Stored run",
    run_ids,
    index=run_ids.index(default_id),
    # Topic first: the run id is a timestamp nobody reads, and the sidebar is
    # narrow enough that leading with it truncates the only useful part.
    format_func=lambda rid: f"{next(r.topic for r in runs if r.run_id == rid)} · {rid}",
)

if st.session_state.get("flash"):
    st.success(st.session_state.pop("flash"))

run = store.load_run(selected)
posts = store.load_posts(selected)
bundle = store.load_analysis(selected)
if run is None or bundle is None:
    st.error("Selected run is incomplete.")
    st.stop()
bundle = store.apply_reviews(selected, bundle)

posts_by_id = {p.identity: p for p in posts}
in_scope = [p for p in posts if p.topic_match]
assessment = bundle.assessment

# ---------------------------------------------------------------------------
# Masthead
# ---------------------------------------------------------------------------

marking = assessment.tlp.value if assessment else run.tlp.value
template = assessment.case_template.value if assessment else run.case_template.value
st.markdown(
    f'<div class="masthead"><div>'
    f'<div class="prompt"><span class="sigil">&gt;</span> socmint<span class="caret"></span></div>'
    f'<div class="mh-sub">Social threat intelligence</div></div>'
    f'<div class="mh-right">'
    f'<span class="tlp {_tlp_class(marking)}">{_esc(marking)}</span>'
    f'<span class="badge">case · {_esc(template)}</span>'
    f'<span class="badge">{_esc(run.run_id)}</span>'
    f'</div></div>',
    unsafe_allow_html=True,
)

window_label = (
    f"{run.window.start.date()} → {(run.window.end - timedelta(microseconds=1)).date()} UTC"
)
st.markdown(
    f'<div class="note" style="margin:12px 0 4px 0">'
    f'<strong style="color:#E6E9F5">{_esc(run.topic)}</strong> · window {window_label} · '
    f'{len(posts)} posts collected · {len(in_scope)} in scope</div>',
    unsafe_allow_html=True,
)

if bundle.degraded:
    st.warning(
        "Analysis degraded: the multilingual embedding model was unavailable. "
        "Groups are lexical only — cross-language grouping is not claimed."
    )

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

# Overview first: someone who reads one screen should get the whole case. The
# tabs behind it are the detail a reviewer opens when a line on the overview
# raises a question, so they follow the overview's own order.
(
    tab_over, tab_case, tab_indicators, tab_narr, tab_coord,
    tab_posts, tab_cover, tab_claims, tab_prop,
) = st.tabs(
    [
        "Overview",
        "Assessment",
        "Indicators",
        "Narratives",
        "Coordination",
        "Evidence",
        "Coverage",
        "Claims",
        "Spread",
    ]
)

# --- Overview ---------------------------------------------------------------

with tab_over:
    _render_overview(run, posts, in_scope, bundle)

# --- Assessment -------------------------------------------------------------

with tab_case:
    if assessment is None:
        st.info("No assessment stored for this run.")
    else:
        left, right = st.columns([2, 1])
        with left:
            st.markdown('<div class="sec">Key judgements</div>', unsafe_allow_html=True)
            for judgement in assessment.key_judgements:
                cls = "kj hi" if judgement.confidence == "high" else "kj"
                caveats = "".join(
                    f'<div class="cav">↳ {_esc(c)}</div>' for c in judgement.caveats[:3]
                )
                st.markdown(
                    f'<div class="{cls}">'
                    f'<div class="head"><span class="kid">{_esc(judgement.judgement_id)}</span>'
                    f'{_conf_chip(judgement.confidence)}</div>'
                    f'<div class="body">{_esc(judgement.statement)}</div>'
                    f'{caveats}</div>',
                    unsafe_allow_html=True,
                )
        with right:
            st.markdown(
                f'<div class="risk"><div class="k">Triage risk · additive heuristic</div>'
                f'<div class="v">{assessment.risk_score}<span>/100</span></div>'
                f'<div class="bar"><i style="width:{min(assessment.risk_score, 100)}%"></i></div>'
                f'<div class="n">Derived from the factors opposite.</div></div>',
                unsafe_allow_html=True,
            )
            st.markdown('<div class="sec">Risk factors</div>', unsafe_allow_html=True)
            for factor in assessment.risk_factors:
                st.markdown(f'<div class="note">· {_esc(factor)}</div>', unsafe_allow_html=True)

        st.markdown('<div class="sec">Impersonation &amp; brand abuse</div>', unsafe_allow_html=True)
        if not bundle.impersonation:
            st.markdown(
                '<div class="note">No impersonation signals were observed in this sample.</div>',
                unsafe_allow_html=True,
            )
        for finding in bundle.impersonation:
            sev_cls = {"high": "chip-high", "medium": "chip-moderate", "low": "chip-low"}.get(
                finding.severity, "chip-low"
            )
            st.markdown(
                f'<div class="note" style="margin-bottom:10px">'
                f'<span class="chip {sev_cls}">{_esc(finding.severity)} severity</span> '
                f'{_conf_chip(finding.confidence)} '
                f'<span class="chip">{len(finding.post_ids)} posts</span> '
                f'subject <strong style="color:#E6E9F5">{_esc(finding.subject)}</strong></div>',
                unsafe_allow_html=True,
            )
            for signal in finding.signals:
                evidence = "".join(
                    f'<div class="cav">“{_esc(item)}”</div>' for item in signal.evidence[:2]
                )
                st.markdown(
                    f'<div class="kj"><div class="head">'
                    f'<span class="kid">{_esc(humanise(signal.kind))}</span>'
                    f'<span class="chip">weight {signal.weight:g}</span></div>'
                    f'<div class="body">{_esc(signal.detail)}</div>{evidence}</div>',
                    unsafe_allow_html=True,
                )
            for caveat in finding.caveats:
                st.markdown(f'<div class="caveat">↳ {_esc(caveat)}</div>', unsafe_allow_html=True)

        st.markdown('<div class="sec">Case caveats</div>', unsafe_allow_html=True)
        for caveat in assessment.caveats:
            st.markdown(f'<div class="caveat">↳ {_esc(caveat)}</div>', unsafe_allow_html=True)

# --- Indicators -------------------------------------------------------------

with tab_indicators:
    st.markdown('<div class="sec">Technical values</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="note">Values the post presents as artefacts of compromise are '
        '<strong style="color:#E6E9F5">indicators</strong>; cited documentation and vendor '
        'advisories are <strong style="color:#E6E9F5">references</strong>. All are defanged.</div>',
        unsafe_allow_html=True,
    )
    if not bundle.indicators:
        st.info("No technical values were extracted from this sample.")
    else:
        counts = {"indicator": 0, "unknown": 0, "reference": 0}
        for item in bundle.indicators:
            counts[item.role.value] = counts.get(item.role.value, 0) + 1
        st.markdown(
            f'<span class="chip chip-high">{counts["indicator"]} indicators</span> '
            f'<span class="chip">{counts["unknown"]} unknown</span> '
            f'<span class="chip">{counts["reference"]} references</span>',
            unsafe_allow_html=True,
        )

        roles = st.multiselect(
            "Show roles",
            ["indicator", "unknown", "reference"],
            default=["indicator", "unknown"],
            help="References are cited sources, not indicators. They are hidden by default.",
        )
        kinds = sorted({i.kind.value for i in bundle.indicators})
        chosen = st.multiselect("Filter by kind", kinds, default=kinds)

        shown = [
            i for i in bundle.indicators
            if i.role.value in roles and i.kind.value in chosen
        ]
        if not shown:
            st.info("No values match the current filters.")
        for indicator in shown:
            platforms = " ".join(
                f"{name}:{count}" for name, count in sorted(indicator.platform_counts.items())
            )
            role_class = {
                "indicator": "chip-high",
                "reference": "chip-low",
            }.get(indicator.role.value, "")
            st.markdown(
                f'<div class="ioc"><span class="val">{_esc(indicator.defanged)}</span>'
                f'<span class="meta">{_esc(kind_label(indicator.kind.value))} · '
                f'<span class="{role_class}" style="padding:0;border:0">'
                f'{_esc(indicator.role.value)}</span> · '
                f'{plural(len(indicator.post_ids), "post")} · {_esc(platforms)}</span></div>'
                f'<div class="caveat" style="margin:-2px 0 8px 0">↳ '
                f'{_esc(indicator.role_reason)}</div>',
                unsafe_allow_html=True,
            )
        st.download_button(
            "Download indicators CSV",
            pd.DataFrame(
                [
                    {
                        "kind": i.kind.value,
                        "role": i.role.value,
                        "value": i.value,
                        "defanged": i.defanged,
                        "role_reason": i.role_reason,
                        "posts": len(i.post_ids),
                        "first_seen": i.first_seen,
                    }
                    for i in bundle.indicators
                ]
            ).to_csv(index=False).encode("utf-8"),
            file_name=f"{selected}-indicators.csv",
            mime="text/csv",
        )

# --- Narratives -------------------------------------------------------------

with tab_narr:
    st.markdown('<div class="sec">Narrative candidates</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="note">Grouped by shared vocabulary. Confirm, split or reject each '
        'candidate.</div>',
        unsafe_allow_html=True,
    )
    if not bundle.narratives:
        st.info("No narrative candidates. The corpus may be too small or too homogeneous.")
    for narrative in bundle.narratives:
        members = [posts_by_id[i] for i in narrative.member_ids if i in posts_by_id]
        framing_chip = (
            f'<span class="chip chip-high">{_esc(narrative.framing.value)}</span>'
            if narrative.framing.value != "none"
            else '<span class="chip">no framing</span>'
        )
        with st.expander(
            f"{narrative.narrative_id} · {narrative.label} · {len(members)} posts"
            + (f" · reviewer: {narrative.reviewer_label}" if narrative.reviewer_label else "")
        ):
            st.markdown(
                f'{framing_chip} <span class="chip">{_esc(narrative.method)}</span>',
                unsafe_allow_html=True,
            )
            if narrative.framing_evidence:
                st.markdown(
                    '<div class="caveat">Framing matched on: '
                    + ", ".join(_esc(term) for term in narrative.framing_evidence)
                    + "</div>",
                    unsafe_allow_html=True,
                )
            c1, c2, c3 = st.columns(3)
            c1.markdown(f"**Platforms** {json.dumps(narrative.platform_counts)}")
            c2.markdown(
                f"**First observed** {narrative.first_seen.date() if narrative.first_seen else '—'}"
            )
            c3.markdown(f"**Members** {len(members)}")

            st.markdown("**Representative posts**")
            for pid in narrative.representative_ids:
                post = posts_by_id.get(pid)
                if post:
                    st.markdown(_evidence_card(post), unsafe_allow_html=True)

            options = ["unreviewed", "confirmed", "split", "rejected"]
            review = st.radio(
                "Reviewer decision",
                options,
                index=options.index(
                    narrative.reviewer_label if narrative.reviewer_label in options else "unreviewed"
                ),
                key=f"narr_{narrative.narrative_id}",
                horizontal=True,
            )
            note = st.text_input("Note", key=f"narr_note_{narrative.narrative_id}")
            if st.button("Save review", key=f"narr_save_{narrative.narrative_id}"):
                store.save_review(selected, "narrative", narrative.narrative_id, review, note, "analyst")
                st.success("Saved. Re-running analysis will not erase this decision.")

# --- Coordination -----------------------------------------------------------

with tab_coord:
    st.markdown('<div class="sec">Coordination candidates</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="note">Accounts linked by repeated, time-aligned posting of related '
        'content.</div>',
        unsafe_allow_html=True,
    )
    if not bundle.coordination:
        st.info("No group of accounts showed repeated, time-aligned posting in this sample.")
    for group in bundle.coordination:
        conf_cls = "chip-high" if group.confidence == "strong_candidate" else "chip-moderate"
        with st.expander(
            f"{group.group_id} · {len(group.account_ids)} accounts · "
            f"{len(group.episodes)} episodes · {group.confidence}"
        ):
            st.markdown(
                f'<span class="chip {conf_cls}">{_esc(group.confidence)}</span> '
                f'<span class="chip">{len(group.account_ids)} accounts</span>',
                unsafe_allow_html=True,
            )
            st.markdown(f"**Accounts** {', '.join(group.account_ids)}")
            st.markdown("**Signals** " + ", ".join(f"{humanise(s.kind)} ({s.detail})" for s in group.signals))
            if group.baseline_episodes is not None:
                st.markdown(
                    f'<div class="caveat">Time-shuffle baseline: '
                    f'{plural(int(round(group.baseline_episodes)), "qualifying pair")} arose '
                    'by chance. Compare with the observed evidence.</div>',
                    unsafe_allow_html=True,
                )

            st.markdown("**Supporting episodes**")
            ep_rows = []
            for ep in group.episodes:
                a, b = posts_by_id.get(ep.post_a), posts_by_id.get(ep.post_b)
                ep_rows.append(
                    {
                        "account A": ep.account_a,
                        "account B": ep.account_b,
                        "seconds apart": round(ep.seconds_apart or 0, 1),
                        "signals": ", ".join(ep.signal_kinds),
                        "post A": (a.text or "")[:80] if a else ep.post_a,
                        "post B": (b.text or "")[:80] if b else ep.post_b,
                    }
                )
            st.dataframe(pd.DataFrame(ep_rows), hide_index=True, width="stretch")

            st.markdown("**Benign explanations to rule out**")
            for explanation in group.benign_explanations:
                st.markdown(f"- {explanation}")
            st.markdown("**Limitations**")
            for limitation in group.limitations:
                st.markdown(f"- {limitation}")

            decision = st.radio(
                "Reviewer decision",
                ["unreviewed", "likely_coordinated", "organic", "insufficient"],
                key=f"coord_{group.group_id}",
                horizontal=True,
            )
            if st.button("Save decision", key=f"coord_save_{group.group_id}"):
                store.save_review(selected, "coordination", group.group_id, decision, None, "analyst")
                st.success("Saved.")

# --- Spread -----------------------------------------------------------------

with tab_prop:
    st.markdown('<div class="sec">Earliest observed and spread</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="note">First observation within the collected sample.</div>',
        unsafe_allow_html=True,
    )
    if bundle.earliest:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "narrative": e.narrative_id,
                        "first observed": e.observed_at,
                        "accounts": ", ".join(e.accounts),
                        "platforms": ", ".join(e.platforms),
                        "posts": len(e.post_ids),
                    }
                    for e in bundle.earliest
                ]
            ),
            hide_index=True,
            width="stretch",
        )
        st.caption(bundle.earliest[0].caveat)

    if bundle.propagation:
        st.markdown('<div class="sec">Propagation edges</div>', unsafe_allow_html=True)
        kinds = st.multiselect("Edge kind", ["explicit", "inferred"], default=["explicit", "inferred"])
        edges = [e for e in bundle.propagation if e.kind in kinds]
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "edge": e.edge_id,
                        "narrative": e.narrative_id,
                        "from": e.from_post,
                        "to": e.to_post,
                        "kind": e.kind,
                        "signal": e.signal,
                        "seconds": e.seconds_apart,
                    }
                    for e in edges
                ]
            ),
            hide_index=True,
            width="stretch",
        )
        inferred = sum(1 for e in bundle.propagation if e.kind == "inferred")
        if inferred:
            st.caption(f"{plural(inferred, 'inferred edge')} of {plural(len(edges), 'edge')}.")
    else:
        st.info("No propagation edges were established in this sample.")

# --- Claims -----------------------------------------------------------------

with tab_claims:
    st.markdown('<div class="sec">Claim review queue</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="note">Proposed claims awaiting review. A verdict requires a reviewer who '
        'has read the source.</div>',
        unsafe_allow_html=True,
    )
    if not bundle.claims:
        st.info("No claim candidates met the extraction criteria.")
    for claim in bundle.claims[:40]:
        post = posts_by_id.get(claim.post_id)
        with st.expander(f"{claim.claim_id} · {claim.verdict} · {claim.claim_text[:90]}"):
            st.markdown(f'<div class="ev"><div class="q">{_esc(claim.claim_text)}</div></div>',
                        unsafe_allow_html=True)
            if post:
                st.markdown(
                    f'<div class="m">{_esc(platform_label(post.platform))} · {_esc(post.author_name)} · '
                    f'{post.published_at} · <a href="{_esc(post.url)}">source</a></div>',
                    unsafe_allow_html=True,
                )
            if claim.evidence:
                st.markdown("**Candidate evidence (unverified)**")
                for item in claim.evidence:
                    st.markdown(f"- [{item.title or item.url}]({item.url})")

            options = [
                "unreviewed", "supported", "contradicted", "mixed", "unverified", "not_checkable",
            ]
            verdict = st.selectbox(
                "Verdict", options, index=options.index(claim.verdict),
                key=f"claim_{claim.claim_id}",
            )
            rationale = st.text_area(
                "Rationale (cite what you read)", value=claim.rationale or "",
                key=f"claim_note_{claim.claim_id}", height=80,
            )
            if st.button("Save verdict", key=f"claim_save_{claim.claim_id}"):
                if verdict != "unreviewed" and not rationale.strip():
                    st.error("A verdict needs a rationale.")
                else:
                    store.save_review(selected, "claim", claim.claim_id, verdict, rationale, "analyst")
                    st.success("Saved.")

# --- Coverage ---------------------------------------------------------------

with tab_cover:
    dated = sum(1 for p in posts if p.published_at)
    tiles = st.columns(5)
    tiles[0].markdown(
        _stat("Posts", len(posts), f"{dated} dated · {len(posts) - dated} undated"),
        unsafe_allow_html=True,
    )
    tiles[1].markdown(
        _stat("Narratives", len(bundle.narratives), "candidates · review required"),
        unsafe_allow_html=True,
    )
    tiles[2].markdown(
        _stat("Coordination", len(bundle.coordination), "candidates · not verdicts"),
        unsafe_allow_html=True,
    )
    actionable = sum(1 for i in bundle.indicators if i.role.value == "indicator")
    references = sum(1 for i in bundle.indicators if i.role.value == "reference")
    tiles[3].markdown(
        _stat("Indicators", actionable, f"{references} references excluded"),
        unsafe_allow_html=True,
    )
    tiles[4].markdown(
        _stat("Claims", len(bundle.claims),
              f"{sum(1 for c in bundle.claims if c.verdict == 'unreviewed')} unreviewed"),
        unsafe_allow_html=True,
    )

    st.markdown('<div class="sec">Per-platform collection status</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="note">Per-platform retrieval status for this run.</div>',
        unsafe_allow_html=True,
    )
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "platform": str(r.platform),
                    "discovery": str(r.discovery),
                    "retrieval": str(r.retrieval),
                    "sources": r.sources_found,
                    "posts": r.posts_retrieved,
                    "in window": r.posts_in_range,
                    # A bool column renders as an empty checkbox, which reads as
                    # "not yet filled in". Use the same tick/dash as Evidence.
                    "truncated": "✓" if r.truncated else "—",
                }
                for r in run.platforms
            ]
        ),
        hide_index=True,
        width="stretch",
    )
    for report in run.platforms:
        with st.expander(f"{platform_label(report.platform)} notes ({len(report.notes)})"):
            if report.notes:
                for note in report.notes:
                    st.markdown(f"- {note}")
            else:
                st.markdown("_No notes._")

    st.markdown('<div class="sec">Requested vs observed</div>', unsafe_allow_html=True)
    coverage = pipeline.coverage_summary(run, posts)
    for platform, values in coverage["platforms"].items():
        st.markdown(
            f"**{platform}** — {values['posts']} posts, {values['dated']} dated, "
            f"{values['undated']} undated. Observed range: "
            f"{values['first'] or '—'} → {values['last'] or '—'}"
        )
    st.caption(coverage["note"])

    st.markdown('<div class="sec">Method notes</div>', unsafe_allow_html=True)
    for note in bundle.method_notes:
        st.markdown(f"- {note}")

# --- Evidence ---------------------------------------------------------------

with tab_posts:
    st.markdown('<div class="sec">Evidence table</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="note">Every row links to the original post. Engagement is a snapshot at '
        'collection time.</div>',
        unsafe_allow_html=True,
    )
    if posts:
        frame = pd.DataFrame(
            [
                {
                    "platform": str(p.platform),
                    "published": p.published_at,
                    "author": p.author_name,
                    "lang": p.language,
                    "topic": "✓" if p.topic_match else "—",
                    "admiralty": p.admiralty.code if p.admiralty else "—",
                    "text": (p.title or p.text or "")[:140],
                    # Media is collected as references, not downloaded: the cell
                    # says what the post carries, the source link renders it.
                    "media": (
                        " · ".join(
                            f"{count} {kind}"
                            for kind, count in Counter(str(m.kind) for m in p.media).most_common()
                        )
                        if p.media
                        else "—"
                    ),
                    "views": p.engagement.views if p.engagement else None,
                    "url": p.url,
                }
                for p in posts
            ]
        )
        only_scope = st.checkbox("Only topic-matched posts", value=True)
        view = frame[frame["topic"] == "✓"] if only_scope else frame
        st.dataframe(
            view,
            hide_index=True,
            width="stretch",
            column_config={"url": st.column_config.LinkColumn("source")},
        )
        st.download_button(
            "Download evidence CSV",
            view.to_csv(index=False).encode("utf-8"),
            file_name=f"{selected}-posts.csv",
            mime="text/csv",
        )
    else:
        st.info("No posts collected.")

st.markdown("---")
with st.expander("Export this run"):
    st.caption(
        "Writes JSON + CSV to a new timestamped directory. Existing exports are never overwritten."
    )
    if st.button("Export run artefacts"):
        directory = export(
            run, posts, bundle,
            output_root=Path("exports"),
            coverage=pipeline.coverage_summary(run, posts),
        )
        st.success(f"Exported to {directory}")
