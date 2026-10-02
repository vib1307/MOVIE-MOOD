"""Gradio UI, mounted at / inside the FastAPI app. Calls core directly (not over HTTP). See D-020.

Look: the "Nocturne" design from Claude Design (Movie Mood.dc.html): a Home screen
("How are you feeling?" + mood chips) and a Suggestions screen (numbered poster grid,
one line of "why" per film; a list on phones). Clicking a card opens its "Why this
pick" panel (D-024). Colors/fonts come from THEME + CSS below; main.py passes both to
mount_gradio_app.

Search runs in steps. Results are drawn once, after the LLM (D-034):
    1. loader: instant; clears the old results and shows a spinner
    2. final:  retrieval + LLM judges + explains (~2s with OpenAI), replaces the loader
    3. ingest: only if step 2 found nothing that fits: fetch the named actor's/title's
               movies from TMDB into the catalog, then recommend again (D-023)
Step 1 runs without a concurrency limit. Steps 2-3 share the "llm" queue: one at a time
with Ollama (it works through LLM requests one by one anyway), a few at once with OpenAI
(D-033). Gradio shows queued users their position.
"""

import html

import gradio as gr
import httpx
from ollama import ResponseError

from moviemood.config import get_settings
from moviemood.core.availability import DEFAULT_REGION, add_availability, region_name, regions
from moviemood.core.explain import explain
from moviemood.core.filters import describe, parse_filters
from moviemood.core.lazy_ingest import lazy_ingest
from moviemood.core.models import Recommendation, poster_url
from moviemood.core.recommender import recommend

LLM_CONCURRENCY = {"ollama": 1, "openai": 4}  # steps 2-3 running at once (D-033)
K = 10  # user: at least 10 films per search (D-033); the design's grid showed four
MIN_QUERY = 3

EXAMPLES = ["cozy and light", "dark and scary", "mind-bending", "a good cry", "pure adrenaline", "kuch comedy"]
# A mood (or a name) plus hard filters on rating / year / runtime (D-027); each one returns
# films on the current catalog (checked when these were picked, D-030).
FILTER_EXAMPLES = [
    "action movies with IMDb 8.5+",
    "romantic comedy from the 90s",
    "funny under 2 hours, IMDb 7+",
    "sci-fi after 2010",
    "Shah Rukh Khan, rated 7.5+",
]

OLLAMA_ERRORS = (ConnectionError, ResponseError, httpx.TimeoutException)
NO_MATCH = "🤔 Nothing in our catalog really fits that. Here are the nearest films we have."
SEARCHING_TMDB = NO_MATCH + " 🔎 Checking TMDB for more…"
UNAVAILABLE = "😴 The recommendation engine isn't available right now. Please try again shortly."

# How the pick was made, by Recommendation.source; shown in the "Why this pick" panel.
HOW_CHECKED = {
    "llm": "Found by meaning, then confirmed by our AI check.",
    "fallback": "Found by meaning. The AI check didn't weigh in on this one.",
    "demoted": "The closest we have, but our AI check thinks it may not fit.",
}
NUMBER_WORDS = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten"]

# Inline SVG: no static files to serve or deploy.
PLACEHOLDER_POSTER = (
    "data:image/svg+xml;utf8,"
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 200 300'>"
    "<rect width='200' height='300' fill='%23232532'/>"
    "<text x='100' y='165' font-size='56' text-anchor='middle'>🎬</text></svg>"
)
SEARCH_ICON = (
    "<svg width='20' height='20' viewBox='0 0 256 256' fill='currentColor' aria-hidden='true'>"
    "<path d='M229.66,218.34l-50.07-50.06a88.11,88.11,0,1,0-11.31,11.31l50.06,50.07a8,8,0,0,0,"
    "11.32-11.32ZM40,112a72,72,0,1,1,72,72A72.08,72.08,0,0,1,40,112Z'/></svg>"
)

# An empty href reloads the page: the simplest way back to the Home screen.
HEADER = "<header class='mm-header'><a class='mm-logo' href=''><span class='mm-dot'></span>movie mood</a></header>"
HERO = (
    "<div class='mm-hero'><p class='mm-eyebrow'>Tonight</p>"
    "<h1>How are you feeling?</h1>"
    "<p class='mm-lede'>Describe your mood in your own words. "
    "We'll suggest a few films that fit, and say why.</p></div>"
)

# Nocturne design tokens (Claude Design, styles.css). Dark only, like the design.
FONT = gr.themes.GoogleFont("Inter")
THEME = gr.themes.Base(font=[FONT, "system-ui", "sans-serif"]).set(
    body_background_fill="#161826",
    body_background_fill_dark="#161826",
    body_text_color="#e9e9ed",
    body_text_color_dark="#e9e9ed",
    background_fill_primary="#161826",
    background_fill_primary_dark="#161826",
    block_background_fill="transparent",
    block_background_fill_dark="transparent",
    block_border_width="0px",
    block_border_width_dark="0px",
    block_shadow="none",
    block_shadow_dark="none",
    input_background_fill="transparent",
    input_background_fill_dark="transparent",
    input_border_width="0px",
    input_border_width_dark="0px",
    input_shadow="none",
    input_shadow_dark="none",
    input_shadow_focus="none",
    input_shadow_focus_dark="none",
)

CSS = """
:root {
  --mm-bg: #161826; --mm-surface: #232532; --mm-text: #e9e9ed;
  --mm-accent: #9184d9; --mm-accent-300: #d2cefd; --mm-accent-600: #796cbf; --mm-accent-900: #2b2741;
  --mm-n300: #cfd3e5; --mm-n400: #b2b6ca; --mm-n500: #9397ab; --mm-n800: #3f424d;
  --mm-divider: color-mix(in srgb, #e9e9ed 16%, transparent);
  --mm-shadow-sm: 0 0 0 1px #3f424d;
}
.gradio-container .main, .gradio-container .wrap { gap: 0; }
/* gr.HTML adds its own padding; drop it so every block lines up on the same left edge */
.gradio-container .html-container, .gradio-container .prose.gradio-style { padding: 0 !important; }

.mm-header { padding: 24px 0 8px; }
.mm-logo { display: inline-flex; align-items: center; gap: 10px; color: var(--mm-text) !important;
           text-decoration: none; font-weight: 500; font-size: 17px; letter-spacing: -0.01em; }
.mm-dot { width: 14px; height: 14px; border-radius: 50%; border: 1.5px solid var(--mm-accent);
          box-shadow: 0 0 12px var(--mm-accent-600); }

.mm-hero { max-width: 760px; padding: 96px 0 40px; }
.mm-eyebrow { margin: 0 0 16px; font-size: 13px; letter-spacing: 0.08em; text-transform: uppercase;
              color: var(--mm-accent-300); }
.mm-hero h1 { margin: 0 0 16px; font-weight: 500; font-size: 60px; line-height: 1.05;
              letter-spacing: -0.03em; text-wrap: balance; }
.mm-lede { margin: 0; font-size: 17px; line-height: 1.5; color: var(--mm-n400); max-width: 520px; }

/* search box: icon + input + button in one surface */
#mm-search { max-width: 760px; align-items: center; gap: 12px; height: 60px; padding: 0 8px 0 20px;
             background: var(--mm-surface); border-radius: 14px; box-shadow: var(--mm-shadow-sm);
             flex-wrap: nowrap; margin-top: 8px; }
#mm-search > .mm-icon { color: var(--mm-n500); flex: 0 0 20px !important; width: 20px !important;
                        min-width: 0 !important; padding: 0 !important; }
#mm-search .mm-icon svg { display: block; }
#mm-query { padding: 0 !important; min-width: 0 !important; margin-left: 4px; }
#mm-query input, #mm-query textarea { background: transparent !important; border: none !important;
             box-shadow: none !important; color: var(--mm-text) !important; font-size: 17px !important;
             caret-color: var(--mm-accent); padding: 0 !important; resize: none; }
#mm-query input::placeholder, #mm-query textarea::placeholder { color: var(--mm-n500); }
#mm-go { flex: 0 0 auto !important; min-width: 0 !important; height: 44px; padding: 0 20px;
         background: transparent !important; color: var(--mm-accent) !important;
         border: 1px solid var(--mm-accent) !important; border-radius: 8px; font-size: 15px; font-weight: 500; }
#mm-go:hover { background: color-mix(in srgb, var(--mm-accent) 12%, transparent) !important; }

#mm-chips { max-width: 760px; flex-wrap: wrap; gap: 8px; margin-top: 20px; }
#mm-chips button { flex: 0 0 auto !important; min-width: 0 !important; border-radius: 999px;
                   font-size: 13px; padding: 6px 14px; background: var(--mm-surface) !important;
                   color: var(--mm-n300) !important; border: none !important; box-shadow: var(--mm-shadow-sm); }
#mm-chips button:hover { color: var(--mm-text) !important; }
.mm-chips-label { margin: 24px 0 8px; font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase;
                  color: var(--mm-n500); }
#mm-filter-chips { max-width: 760px; flex-wrap: wrap; gap: 8px; }
#mm-filter-chips button { flex: 0 0 auto !important; min-width: 0 !important; border-radius: 999px;
                          font-size: 13px; padding: 6px 14px; background: transparent !important;
                          color: var(--mm-accent-300) !important; border: 1px dashed var(--mm-accent-600) !important; }
#mm-filter-chips button:hover { background: color-mix(in srgb, var(--mm-accent) 12%, transparent) !important; }

/* region picker: a chip-shaped dropdown, visible on both screens */
#mm-region-row { max-width: 760px; }
#mm-region { flex: 0 0 auto !important; width: 230px !important; min-width: 0 !important; padding: 0 !important; }
#mm-region input { background: var(--mm-surface) !important; color: var(--mm-text) !important;
                   border: none !important; box-shadow: var(--mm-shadow-sm) !important;
                   border-radius: 999px !important; font-size: 13px !important; padding: 7px 14px !important; }
#mm-region ul, #mm-region .options { background: var(--mm-surface) !important; color: var(--mm-text) !important;
                   border: 1px solid var(--mm-n800) !important; border-radius: 10px !important; }
#mm-region li:hover, #mm-region .item:hover, #mm-region .active {
                   background: var(--mm-accent-900) !important; color: var(--mm-text) !important; }

/* results */
.mm-results-head { display: flex; align-items: baseline; gap: 16px; margin: 40px 0 8px; flex-wrap: wrap; }
.mm-results-head h2 { margin: 0; font-weight: 500; font-size: 32px; letter-spacing: -0.02em; }
.mm-again { font-size: 14px; color: var(--mm-accent-300) !important; text-decoration: none; }
.mm-again:hover { color: var(--mm-text) !important; }
.mm-rule { height: 1px; margin-bottom: 24px; background: linear-gradient(90deg, var(--mm-accent) 0,
           var(--mm-divider) 48px, var(--mm-divider) calc(100% - 48px), transparent); }
.mm-note { margin: 0 0 24px; font-size: 14px; color: var(--mm-n400); }
.mm-empty { padding: 48px 0; color: var(--mm-n400); }
/* step 1 loader (D-034) */
.mm-loading { display: flex; align-items: center; gap: 12px; padding: 48px 0; color: var(--mm-n400); font-size: 15px; }
.mm-spinner { width: 20px; height: 20px; border-radius: 50%; border: 2px solid var(--mm-accent-900);
              border-top-color: var(--mm-accent); animation: mm-spin 0.8s linear infinite; }
@keyframes mm-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .mm-spinner { animation: none; } }
/* 10 films = two rows of five (D-033) */
.mm-grid { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 24px; align-items: start; }

.mm-card > summary { list-style: none; cursor: pointer; display: flex; flex-direction: column; gap: 12px; }
.mm-card > summary::-webkit-details-marker { display: none; }
.mm-poster { aspect-ratio: 2 / 3; border-radius: 8px; overflow: hidden; background: var(--mm-surface);
             box-shadow: var(--mm-shadow-sm); transition: box-shadow 0.15s; }
.mm-poster img { width: 100%; height: 100%; object-fit: cover; display: block; }
.mm-card > summary:hover .mm-poster, .mm-card[open] .mm-poster { box-shadow: 0 0 0 1px var(--mm-accent); }
.mm-info { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.mm-num { font-size: 12px; color: var(--mm-accent-300); }
.mm-title { margin: 0; font-weight: 500; font-size: 17px; line-height: 1.25; }
.mm-meta { font-size: 12px; color: var(--mm-n500); }
.mm-why { margin: 0; font-size: 14px; line-height: 1.5; color: var(--mm-n300); text-wrap: pretty; }
.mm-watch { font-size: 12px; color: var(--mm-accent-300); }
.mm-watch-none { color: var(--mm-n500); }
.mm-credit { margin: 32px 0 0; font-size: 12px; color: var(--mm-n500); }
.mm-more { font-size: 13px; color: var(--mm-accent-300); }
.mm-more::after { content: "Why this pick  ↓"; }
.mm-card[open] .mm-more::after { content: "Hide  ↑"; }

.mm-panel { margin-top: 12px; padding: 14px 16px; border-radius: 8px; background: var(--mm-surface);
            box-shadow: var(--mm-shadow-sm); border-left: 2px solid var(--mm-accent);
            display: flex; flex-direction: column; gap: 12px; }
.mm-panel-title { font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--mm-accent-300); }
.mm-row { display: flex; flex-direction: column; gap: 4px; font-size: 14px; line-height: 1.45; }
.mm-label { font-size: 12px; color: var(--mm-n500); }
.mm-tags { display: flex; flex-wrap: wrap; gap: 6px; }
.mm-tag { font-size: 12px; padding: 2px 10px; border-radius: 999px; color: var(--mm-accent-300);
          background: color-mix(in srgb, var(--mm-accent) 14%, transparent); }
.mm-story { margin: 0; color: var(--mm-n300); display: -webkit-box; -webkit-line-clamp: 5;
            -webkit-box-orient: vertical; overflow: hidden; }

@media (max-width: 1100px) { .mm-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
/* phones: the design's list layout, poster on the left */
@media (max-width: 640px) {
  .mm-hero { padding: 48px 0 24px; }
  .mm-hero h1 { font-size: 38px; line-height: 1.08; }
  .mm-lede { font-size: 15px; }
  #mm-search { height: 56px; padding: 0 6px 0 16px; }
  #mm-go { padding: 0 14px; }
  .mm-results-head { margin-top: 24px; }
  .mm-results-head h2 { font-size: 24px; line-height: 1.2; }
  .mm-grid { grid-template-columns: 1fr; gap: 0; }
  .mm-card { padding: 16px 0; }
  .mm-card > summary { display: grid; grid-template-columns: 84px 1fr; column-gap: 16px; row-gap: 6px; }
  .mm-poster { grid-row: span 3; }
}
"""

# Page-level rules go in <head>, not CSS: Gradio prefixes every selector inside a custom
# CSS @media block with ".gradio-container ... .contain", so a phone rule for the
# container itself never matched (the page kept 64px + 32px side padding at 390px).
HEAD = """<style>
body {
  background: radial-gradient(900px 520px at 12% 0%, #2b2741, transparent 70%), #161826 !important;
  color: #e9e9ed;
}
gradio-app, .gradio-container { background: transparent !important; }
.gradio-container { width: 100% !important; max-width: 1280px !important; margin: 0 auto !important; padding: 0 64px 48px !important; }
.gradio-container .main.fillable { padding: 0 !important; }
footer { opacity: 0.4; }
@media (max-width: 640px) { .gradio-container { padding: 0 24px 32px !important; } }
/* Toasts (gr.Warning): Gradio paints them near-white while the theme's text is light,
   so the message was white on white. Put them on the dark surface instead. */
.toast-body { background: #232532 !important; border: 1px solid #3f424d !important; color: #e9e9ed !important; }
.toast-body .toast-message-text, .toast-body .toast-title, .toast-body .toast-close { color: #e9e9ed !important; }
.toast-body.warning .toast-title { color: #fbbf24 !important; }
.toast-body.error .toast-title { color: #f87171 !important; }
</style>"""


def _meta(r: Recommendation) -> str:
    parts = [str(r.year) if r.year else "", f"{r.runtime} min" if r.runtime else "",
             f"IMDb {r.imdb_rating:.1f}" if r.imdb_rating is not None else ""]
    return " · ".join(p for p in parts if p)


def _watch_line(r: Recommendation, region: str) -> str:
    """"▶ Netflix, JioHotstar (India)", or an honest empty state. See D-036.

    where_to_watch is None when availability was never looked up (API clients that skip
    it, tests): then the card shows no line at all rather than claiming anything.
    """
    if r.where_to_watch is None:
        return ""
    where = region_name(region)
    if not r.where_to_watch:
        return f"<span class='mm-watch mm-watch-none'>Not on subscription in {html.escape(where)}</span>"
    names = ", ".join(r.where_to_watch)
    return f"<span class='mm-watch'>▶ {html.escape(f'{names} ({where})')}</span>"


def _why_panel(r: Recommendation, filter_label: str = "") -> str:
    """The "Why this pick" panel. Everything is escaped: titles and tags come from TMDB
    and the "why" from an LLM, so none of it can be trusted to be HTML-safe."""
    rows = []
    if filter_label:  # e.g. "IMDb 8.5+ · this film: 2008 · 152 min · IMDb 9.1"
        rows.append(("Your filter", html.escape(f"{filter_label} · this film: {_meta(r)}")))
    if r.named:
        rows.append(("You asked for", html.escape(" · ".join(r.named))))
    if r.mood_tags:
        tags = "".join(f"<span class='mm-tag'>{html.escape(t)}</span>" for t in r.mood_tags)
        rows.append(("Closest to your mood", f"<span class='mm-tags'>{tags}</span>"))
    rows.append(("How we checked", html.escape(HOW_CHECKED[r.source])))
    if r.overview:
        rows.append(("The story", f"<p class='mm-story'>{html.escape(r.overview)}</p>"))
    body = "".join(f"<div class='mm-row'><span class='mm-label'>{label}</span>{value}</div>" for label, value in rows)
    return f"<div class='mm-panel'><div class='mm-panel-title'>Why this pick</div>{body}</div>"


def render_cards(
    results: list[Recommendation],
    query: str = "",
    note: str = "",
    empty: str = "No films found. Try describing the mood differently.",
    region: str = DEFAULT_REGION,
) -> str:
    """The Suggestions screen: heading, note, and a card per film. Each card is a
    <details>, so a click opens its "Why this pick" panel with no JavaScript."""
    parts = []
    # Hard filters in the query ("IMDb 8.5+"), shown so a short list makes sense (D-027)
    filter_label = describe(parse_filters(query)[1]) if query else ""
    if filter_label:
        note = " · ".join(x for x in (f"Filtered: {filter_label}", note) if x)
        empty = f"No films in our catalog match {filter_label}. Try loosening it."
    if query:
        count = NUMBER_WORDS[len(results)] if len(results) < len(NUMBER_WORDS) else str(len(results))
        noun = "film" if len(results) == 1 else "films"
        parts.append(
            "<div class='mm-results-head'>"
            f"<h2>{count} {noun} for “{html.escape(query)}”</h2>"
            "<a class='mm-again' href=''>Try another mood</a></div><div class='mm-rule'></div>"
        )
    if note:
        parts.append(f"<p class='mm-note'>{html.escape(note)}</p>")
    if not results:
        parts.append(f"<div class='mm-empty'>{html.escape(empty)}</div>")
        return "".join(parts)

    cards = []
    for n, r in enumerate(results, start=1):
        img = html.escape(poster_url(r.poster_path) or PLACEHOLDER_POSTER, quote=True)
        cards.append(
            "<details class='mm-card'><summary>"
            f"<div class='mm-poster'><img src=\"{img}\" alt=\"{html.escape(r.title, quote=True)} poster\" loading='lazy'></div>"
            "<div class='mm-info'>"
            f"<span class='mm-num'>{n:02d}</span>"
            f"<h3 class='mm-title'>{html.escape(r.title)}</h3>"
            f"<span class='mm-meta'>{html.escape(_meta(r))}</span>"
            f"{_watch_line(r, region)}</div>"
            f"<p class='mm-why'>{html.escape(r.why)}</p>"
            "<span class='mm-more'></span>"
            f"</summary>{_why_panel(r, filter_label)}</details>"
        )
    parts.append(f"<div class='mm-grid'>{''.join(cards)}</div>")
    if any(r.where_to_watch is not None for r in results):
        # TMDB asks for JustWatch to be credited wherever this data is shown (D-036).
        parts.append("<p class='mm-credit'>Streaming availability from JustWatch via TMDB.</p>")
    return "".join(parts)


def all_demoted(results: list[Recommendation]) -> bool:
    return bool(results) and all(r.source == "demoted" for r in results)


def render_loader(query: str) -> str:
    """Step 1's screen: the results heading + a spinner, no cards (D-034)."""
    return (
        "<div class='mm-results-head'>"
        f"<h2>Finding films for “{html.escape(query)}”</h2>"
        "<a class='mm-again' href=''>Try another mood</a></div><div class='mm-rule'></div>"
        "<div class='mm-loading' role='status'><span class='mm-spinner'></span>Reading your mood…</div>"
    )


def show_loader(query: str) -> str:
    """Step 1: instant. Clears the last search's cards, so nothing is swapped later (D-034)."""
    query = (query or "").strip()
    if len(query) < MIN_QUERY:
        gr.Warning("Describe the mood in a few words, e.g. “cozy and light”.")
        return render_cards([], empty="Type a mood above or pick one of the suggestions.")
    return render_loader(query)


def search_final(query: str, region: str = DEFAULT_REGION):
    """Step 2: retrieval + LLM judges + explains + where to watch, replacing the loader.

    Returns (cards, misses, shown). `misses` holds the results when nothing fits, which
    tells step 3 to run; otherwise None. `shown` is (results, note), kept so changing the
    region can redraw the same films without searching again.
    """
    query = (query or "").strip()
    if len(query) < MIN_QUERY:
        return gr.skip(), None, gr.skip()
    try:
        results = recommend(query, k=K)
    except OLLAMA_ERRORS:
        # Not gr.skip(): that would leave the loader spinning forever.
        gr.Warning(UNAVAILABLE)
        return render_cards([], empty=UNAVAILABLE), None, None
    explain(query, results)
    add_availability(results, region)
    # Every movie judged a misfit (e.g. an actor we have no films of): say so honestly.
    if all_demoted(results):
        return render_cards(results, query, note=SEARCHING_TMDB, region=region), results, (results, SEARCHING_TMDB)
    return render_cards(results, query, region=region), None, (results, "")


def search_ingest(query: str, misses: list[Recommendation] | None, region: str = DEFAULT_REGION):
    """Step 3: nothing fit, so try TMDB. New movies -> recommend again; none -> plain no-match.

    Returns (cards, shown), like step 2.
    """
    if not misses:
        return gr.skip(), gr.skip()
    query = (query or "").strip()
    try:
        added = lazy_ingest(query)
        if not added:
            return render_cards(misses, query, note=NO_MATCH, region=region), (misses, NO_MATCH)
        results = recommend(query, k=K)
    except OLLAMA_ERRORS:
        gr.Warning(UNAVAILABLE)
        return render_cards(misses, query, note=NO_MATCH, region=region), (misses, NO_MATCH)
    explain(query, results)
    add_availability(results, region)  # the new films aren't in the provider cache yet
    if all_demoted(results):
        return render_cards(results, query, note=NO_MATCH, region=region), (results, NO_MATCH)
    titles = ", ".join(m.title for m in added)
    note = f"🆕 Added from TMDB: {titles}"
    return render_cards(results, query, note=note, region=region), (results, note)


def switch_region(query: str, region: str, shown):
    """Redraw the films already on screen for another country. See D-036.

    No retrieval, no LLM and (with a warm cache) no network: one watch/providers call
    holds every country, so this is a dict lookup per film.
    """
    if not shown:
        return gr.skip()
    results, note = shown
    add_availability(results, region)
    return render_cards(results, (query or "").strip(), note=note, region=region)


def build_ui() -> gr.Blocks:
    with gr.Blocks(title="movie mood") as ui:
        gr.HTML(HEADER)
        hero = gr.HTML(HERO)
        with gr.Row(elem_id="mm-search", equal_height=True):
            gr.HTML(SEARCH_ICON, elem_classes="mm-icon", container=False, min_width=0)
            query = gr.Textbox(
                placeholder="e.g. tired, want something warm and funny",
                show_label=False,
                container=False,
                elem_id="mm-query",
                scale=1,
                max_lines=1,
                max_length=300,
                autofocus=True,
            )
            go = gr.Button("Find films", elem_id="mm-go", scale=0)
        # The label sits above the picker, like the filter-chips label: a gr.Row stretches
        # its children, so a label beside the picker pushes it to the far edge.
        gr.HTML("<p class='mm-chips-label'>Streaming in</p>")
        with gr.Row(elem_id="mm-region-row"):
            region = gr.Dropdown(
                choices=regions(),  # [("India", "IN"), ...]: label shown, code passed on
                value=DEFAULT_REGION,
                show_label=False,
                container=False,
                filterable=True,
                elem_id="mm-region",
                scale=0,
            )
        with gr.Row(elem_id="mm-chips") as chip_row:
            chips = [gr.Button(text, size="sm", scale=0) for text in EXAMPLES]
        with gr.Column(visible=True) as filter_col:
            gr.HTML("<p class='mm-chips-label'>Or mix a mood with filters: rating, year, length</p>")
            with gr.Row(elem_id="mm-filter-chips"):
                chips += [gr.Button(text, size="sm", scale=0) for text in FILTER_EXAMPLES]
        cards = gr.HTML("")
        misses = gr.State(None)  # step 2 -> step 3: the results when nothing fit
        shown = gr.State(None)  # (results, note) on screen, so the region can be switched

        # The public API is FastAPI's /api/v1, so UI events are hidden from Gradio's API page.
        # "undocumented", not "private": with "private" the browser's own clicks stopped
        # working too (verified in Chrome, Gradio 6.28).
        private = {"api_visibility": "undocumented"}

        def to_results(text: str):
            # Home -> Suggestions: hide the hero and chips (the design's results screen has neither).
            # Not for a too-short query: that only shows a warning, so stay on Home.
            if len((text or "").strip()) < MIN_QUERY:
                return text, gr.update(), gr.update(), gr.update()
            return text, gr.update(visible=False), gr.update(visible=False), gr.update(visible=False)

        # Ollama on one box answers one LLM request at a time; OpenAI can take several.
        llm_limit = LLM_CONCURRENCY[get_settings().llm_provider]

        def wire(event):
            # step 1 (no limit, instant) -> steps 2 and 3 (the shared "llm" queue)
            event.then(show_loader, query, cards, concurrency_limit=None,
                       show_progress="minimal", **private) \
                 .then(search_final, [query, region], [cards, misses, shown],
                       concurrency_limit=llm_limit, concurrency_id="llm",
                       show_progress="hidden", **private) \
                 .then(search_ingest, [query, misses, region], [cards, shown],
                       concurrency_limit=llm_limit, concurrency_id="llm",
                       show_progress="hidden", **private)

        screen = [query, hero, chip_row, filter_col]
        wire(query.submit(to_results, query, screen, queue=False, **private))
        wire(go.click(to_results, query, screen, queue=False, **private))
        for chip in chips:
            # A Button's value is its label, so the chip passes its own text as the input.
            wire(chip.click(to_results, chip, screen, queue=False, **private))
        # Changing the country only redraws the cards: no queue, no LLM, no new search.
        region.change(switch_region, [query, region, shown], cards, queue=False,
                      show_progress="hidden", **private)
    return ui
