"""render_cards() is plain Python -> HTML, so it can be tested without a browser or Ollama."""

import html

from moviemood.core.models import Recommendation
from moviemood.ui import gradio_app
from moviemood.ui.gradio_app import PLACEHOLDER_POSTER, render_cards


def rec(**overrides) -> Recommendation:
    fields = dict(
        tmdb_id=19404, title="Dilwale Dulhania Le Jayenge", year=1995, runtime=190,
        poster_path="/ddlj.jpg", imdb_rating=8.0, why="Romantic comedy with SRK",
        source="llm", distance=0.3,
    )
    return Recommendation(**{**fields, **overrides})


def test_card_shows_poster_title_rating_and_why():
    out = render_cards([rec()])
    assert "https://image.tmdb.org/t/p/w500/ddlj.jpg" in out
    assert "Dilwale Dulhania Le Jayenge" in out
    assert "1995 · 190 min · IMDb 8.0" in out
    assert "Romantic comedy with SRK" in out
    assert "<span class='mm-num'>01</span>" in out


def test_text_is_html_escaped():
    out = render_cards([rec(title="<b>Bold</b>", why="<script>alert(1)</script>")])
    assert "<script>" not in out and "<b>Bold</b>" not in out
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out


def test_missing_poster_rating_and_runtime():
    out = render_cards([rec(poster_path=None, imdb_rating=None, runtime=None)])
    assert html.escape(PLACEHOLDER_POSTER, quote=True) in out  # the browser decodes it back
    assert "<span class='mm-meta'>1995</span>" in out and "IMDb" not in out


def test_one_card_per_result_and_empty_state():
    assert render_cards([rec(tmdb_id=i) for i in range(4)]).count("class='mm-card'") == 4
    assert "Type a mood" in render_cards([], empty="Type a mood above")


def test_heading_counts_films_and_escapes_query():
    assert "Four films for “cozy &amp; light”" in render_cards([rec()] * 4, "cozy & light")
    assert "One film for" in render_cards([rec()], "x y z")
    assert "mm-results-head" not in render_cards([rec()])  # no query: no heading


def test_why_panel_shows_evidence_escaped():
    out = render_cards([rec(named=["Starring <Brad>"], mood_tags=["grimdark"], overview="A <b>story</b>.")])
    assert "Why this pick" in out
    assert "Starring &lt;Brad&gt;" in out and "<span class='mm-tag'>grimdark</span>" in out
    assert "A &lt;b&gt;story&lt;/b&gt;." in out
    assert "confirmed by our AI check" in out  # source="llm"


def test_why_panel_skips_empty_rows():
    out = render_cards([rec(source="demoted")])
    assert "You asked for" not in out and "Closest to your mood" not in out and "The story" not in out
    assert "may not fit" in out


def test_loader_shows_spinner_and_no_cards(monkeypatch):
    # Results are drawn once, after the LLM, so step 1 must not show any films (D-034).
    called = []
    monkeypatch.setattr(gradio_app, "recommend", lambda *a, **kw: called.append(a) or [])
    out = gradio_app.show_loader("cozy <feel-good>")
    assert "mm-spinner" in out and "mm-card" not in out
    assert "cozy &lt;feel-good&gt;" in out
    assert called == []


def test_short_query_shows_hint_not_loader(monkeypatch):
    monkeypatch.setattr(gradio_app.gr, "Warning", lambda msg: None)  # no UI context in tests
    out = gradio_app.show_loader("hi")
    assert "mm-spinner" not in out and "Type a mood" in out


def test_ollama_down_replaces_loader_with_friendly_message(monkeypatch):
    def broken(*a, **kw):
        raise ConnectionError("Ollama down")

    monkeypatch.setattr(gradio_app, "recommend", broken)
    monkeypatch.setattr(gradio_app.gr, "Warning", lambda msg: None)
    cards, misses = gradio_app.search_final("cozy feel-good")
    assert "isn&#x27;t available" in cards  # not gr.skip(): the loader would spin forever
    assert misses is None


def test_all_demoted_shows_no_match_note_and_hands_misses_to_step3(monkeypatch):
    demoted = [rec(source="demoted")] * 2
    monkeypatch.setattr(gradio_app, "recommend", lambda *a, **kw: demoted)
    monkeypatch.setattr(gradio_app, "explain", lambda q, recs: None)
    cards, misses = gradio_app.search_final("Brad Pitt")
    assert "Nothing in our catalog" in cards and "Checking TMDB" in cards
    assert misses == demoted


def test_some_fits_shows_no_note(monkeypatch):
    monkeypatch.setattr(gradio_app, "recommend", lambda *a, **kw: [rec(), rec(source="demoted")])
    monkeypatch.setattr(gradio_app, "explain", lambda q, recs: None)
    cards, misses = gradio_app.search_final("romantic SRK")
    assert "Nothing in our catalog" not in cards
    assert misses is None  # step 3 won't run


def test_step3_skips_when_something_fit(monkeypatch):
    called = []
    monkeypatch.setattr(gradio_app, "lazy_ingest", lambda q: called.append(q) or [])
    gradio_app.search_ingest("romantic SRK", None)
    assert called == []


def test_step3_nothing_new_shows_plain_no_match(monkeypatch):
    monkeypatch.setattr(gradio_app, "lazy_ingest", lambda q: [])
    out = gradio_app.search_ingest("cozy", [rec(source="demoted")])
    assert "Nothing in our catalog" in out and "Checking TMDB" not in out


def test_step3_new_movies_recommend_again(monkeypatch):
    added = [type("M", (), {"title": "Fight Club"})()]
    monkeypatch.setattr(gradio_app, "lazy_ingest", lambda q: added)
    monkeypatch.setattr(gradio_app, "recommend", lambda *a, **kw: [rec(title="Fight Club")])
    monkeypatch.setattr(gradio_app, "explain", lambda q, recs: None)
    out = gradio_app.search_ingest("Brad Pitt", [rec(source="demoted")])
    assert "Added from TMDB: Fight Club" in out and "mm-card" in out


def test_filter_is_shown_in_note_panel_and_empty_state():
    out = render_cards([rec()], "action movies imdb 8.5+")
    assert "Filtered: IMDb 8.5+" in out
    assert "Your filter" in out and "this film: 1995 · 190 min · IMDb 8.0" in out
    empty = render_cards([], "action imdb 9.9+")
    assert "No films in our catalog match IMDb 9.9+" in empty


def test_no_filter_no_filter_note():
    out = render_cards([rec()], "cozy and light")
    assert "Filtered" not in out and "Your filter" not in out
