"""Lazy ingestion: grow the catalog when nothing in it fits. See D-023, D-025.

Called by the UI only after the recommender demoted every result (e.g. "Brad Pitt"
with no Pitt films indexed). Flow:
    query -> the LLM (settings.llm_model) reads the intent:
               {"person": "Brad Pitt", "title": null, "genres": [], "keywords": []}
          -> TMDB: the person's movies, and/or the title + TMDB's recommendations for it;
             with no names, a mood: TMDB genres + keywords -> /discover, most-voted first
             ("a good cry" -> Drama/Romance + tearjerker)
          -> filters (votes, age, overview, keywords), max MAX_NEW new movies
          -> OMDb rating -> Chroma + movies.json
The caller then runs recommend() again, and the next identical search is instant.
"""

import logging
import threading
from datetime import date, timedelta
from functools import lru_cache
from typing import Literal

from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field, ValidationError

from moviemood.core.catalog import add_to_catalog, get_catalog
from moviemood.core.llm import structured_llm
from moviemood.core.models import Movie
from moviemood.core.semantic_text import BLOB_CAST, name_in_query
from moviemood.core.vectorstore import add_to_index
from moviemood.ingest.omdb import OMDbClient, OMDbError, OMDbLimitReached, parse_rating
from moviemood.ingest.tmdb import TMDBClient, TMDBError, to_movie

log = logging.getLogger(__name__)

MAX_NEW = 5  # new movies per miss, so random queries can't flood the catalog
MAX_DETAILS = 15  # detail lookups per miss; some candidates fail the keyword filter
TITLE_RECS = 4  # a title brings its top TMDB recommendations with it
MIN_VOTES = 100  # same floor as the Indian slice in discover_ids
MIN_AGE_DAYS = 90  # same as discover_ids: votes and ratings need time to settle
MIN_KEYWORDS = 3  # thin keywords make weak blobs (Colony, Phase 2)
LLM_TIMEOUT = 60

# TMDB's movie genre ids (stable; GET /genre/movie/list)
GENRE_IDS = {
    "Action": 28, "Adventure": 12, "Animation": 16, "Comedy": 35, "Crime": 80,
    "Documentary": 99, "Drama": 18, "Family": 10751, "Fantasy": 14, "History": 36,
    "Horror": 27, "Music": 10402, "Mystery": 9648, "Romance": 10749,
    "Science Fiction": 878, "Thriller": 53, "War": 10752, "Western": 37,
}
Genre = Literal[tuple(GENRE_IDS)]  # the JSON schema lists them, so the LLM can only pick these
MAX_MOOD_KEYWORDS = 3

SYSTEM_PROMPT = """You read a movie search request and say what it asks for.
- person: an actor's or director's name if the request names one, else null.
- title: a specific movie title if the request names one, else null.
- genres: 1-2 genres that fit the mood. Empty if the request is only a name or title.
- keywords: 1-3 short tags that describe the mood, the way a movie database tags films
  (tearjerker, feel-good, dystopia, heist, coming of age). Empty if the request is only
  a name or title. Respect negatives: "no sad ending" means NOT tragedy.
Moods, genres, and descriptions are NOT names. Fix obvious spelling and capitalization.
The request may be in Hinglish (Hindi in Latin letters).

Examples:
"Brad Pitt" -> {"person": "Brad Pitt", "title": null, "genres": [], "keywords": []}
"christopher nolan movies" -> {"person": "Christopher Nolan", "title": null, "genres": [], "keywords": []}
"Inception jaisi movie" -> {"person": null, "title": "Inception", "genres": [], "keywords": []}
"a good cry" -> {"person": null, "title": null, "genres": ["Drama", "Romance"], "keywords": ["tearjerker", "grief"]}
"kuch halka sa, rona nahi chahiye" -> {"person": null, "title": null, "genres": ["Comedy", "Family"], "keywords": ["feel-good", "lighthearted"]}"""

_lock = threading.Lock()  # one writer at a time for Chroma + movies.json
_tried: set[str] = set()  # queries already handled in this process (Q12)


class QueryIntent(BaseModel):
    person: str | None = Field(default=None, description="actor or director name, or null")
    title: str | None = Field(default=None, description="movie title, or null")
    genres: list[Genre] = Field(default_factory=list, max_length=2, description="genres that fit the mood")
    keywords: list[str] = Field(default_factory=list, max_length=MAX_MOOD_KEYWORDS, description="mood tags")


@lru_cache
def _get_llm() -> Runnable:
    """Messages -> QueryIntent, from Ollama or OpenAI (settings.llm_provider, D-033)."""
    return structured_llm(QueryIntent, LLM_TIMEOUT)


MOOD_ONLY_HINT = "\n\n(This request names no person and no movie title. Fill only genres and keywords.)"


def extract_intent(query: str, mood_only: bool = False) -> QueryIntent | None:
    """What the query asks for: names, or a mood as genres + keywords.
    None if it's neither (or the LLM failed). mood_only: re-read it as a pure mood."""
    try:
        request = query + (MOOD_ONLY_HINT if mood_only else "")
        intent = _get_llm().invoke([("system", SYSTEM_PROMPT), ("human", request)])
    except Exception as e:  # timeout, Ollama/OpenAI error, bad JSON: just skip lazy ingest
        log.warning("name extraction failed: %s: %s", type(e).__name__, e)
        return None
    # llama3.2 sometimes invents a name from mood words ("kuch halka sa, rona nahi
    # chahiye" -> title "A Light Hearted, No Crying Required"), so keep only names
    # that are really in the query.
    intent.person = None if mood_only else _in_query(intent.person, query)
    intent.title = None if mood_only else _in_query(intent.title, query)
    intent.keywords = [k.strip().lower() for k in intent.keywords if k.strip()][:MAX_MOOD_KEYWORDS]
    if intent.person or intent.title:
        intent.genres, intent.keywords = [], []  # a named query: its mood guess isn't needed
    return intent if intent.person or intent.title or intent.genres or intent.keywords else None


def _in_query(name: str | None, query: str) -> str | None:
    return name.strip() if name and name_in_query(name, query) else None


def lazy_ingest(query: str) -> list[Movie]:
    """Find, fetch and store new movies for what `query` asks for. Returns the new movies.

    [] when the query has no names or mood, nothing new passes the filters, or this query
    was already handled. TMDB errors also give []. Ollama errors while embedding are
    raised, like everywhere else in core.
    """
    key = query.strip().lower()
    if key in _tried:
        return []
    intent = extract_intent(query)
    if intent is None:
        _tried.add(key)
        return []
    log.info("lazy ingest for %r: %s", query, intent)

    try:
        movies = find_new_movies(intent)
        if not movies and intent.title and not intent.person:
            # llama3.2 read "a good cry" as a title. TMDB had nothing usable for it,
            # so read the query again as a mood.
            retry = extract_intent(query, mood_only=True)
            log.info("title %r gave nothing, as a mood: %s", intent.title, retry)
            if retry:
                movies = find_new_movies(retry)
    except TMDBError as e:
        log.warning("lazy ingest skipped, TMDB failed: %s", e)
        return []  # not marked as tried: TMDB may be back next time
    if movies:
        with _lock:
            # Chroma first: if embedding fails, movies.json stays unchanged and the
            # movies can be tried again. (A catalog entry without an index entry would
            # count as "already have it" forever and never show up in search.)
            add_to_index(movies)
            add_to_catalog(movies)
        log.info("lazy ingest added %d: %s", len(movies), ", ".join(m.title for m in movies))
    _tried.add(key)
    return movies


def find_new_movies(intent: QueryIntent, tmdb: TMDBClient | None = None) -> list[Movie]:
    """TMDB movies for `intent` that aren't in the catalog yet and pass the filters.
    Writes nothing except the TMDB/OMDb disk caches."""
    tmdb = tmdb or TMDBClient()
    candidates: list[dict] = []
    if intent.title:
        candidates += _title_candidates(tmdb, intent.title)
    if intent.person:
        candidates += _person_candidates(tmdb, intent.person)
    if not candidates and (intent.genres or intent.keywords):
        candidates += _mood_candidates(tmdb, intent)

    have = set(get_catalog())
    released_before = (date.today() - timedelta(days=MIN_AGE_DAYS)).isoformat()
    ids: list[int] = []
    for c in candidates:
        if c["id"] not in have and c["id"] not in ids and _eligible(c, released_before):
            ids.append(c["id"])

    movies: list[Movie] = []
    for tmdb_id in ids[:MAX_DETAILS]:
        try:
            movie = to_movie(tmdb.details(tmdb_id))
        except (KeyError, ValidationError) as e:
            log.info("lazy skip %d: %s", tmdb_id, e)
            continue
        if not movie.overview.strip() or len(movie.keywords) < MIN_KEYWORDS:
            log.info("lazy skip %s: thin blob (%d keywords)", movie.title, len(movie.keywords))
            continue
        movies.append(movie)
        if len(movies) == MAX_NEW:
            break
    _add_ratings(movies)
    return movies


def _title_candidates(tmdb: TMDBClient, title: str) -> list[dict]:
    """The best match for the title, then TMDB's recommendations for it."""
    # Keep only real matches: every word of the asked-for title must be in the result's
    # title. TMDB search is fuzzy, and "A Good Cry" returned Bad Grandpa and Tommy Boy.
    results = [m for m in tmdb.search_movie(title)[:5] if name_in_query(title, m.get("title") or "", 1)]
    if not results:
        return []
    # Search order is relevance; among the top 5 the most-voted is usually the one
    # people mean ("Titanic" -> 1997, not a documentary).
    best = max(results, key=lambda m: m.get("vote_count") or 0)
    # TMDB's own order is noisy (Inception -> The Dark Tower, Paycheck, Push);
    # most-voted first gives The Matrix, Oblivion.
    recs = sorted(tmdb.recommendations(best["id"]), key=lambda m: m.get("vote_count") or 0, reverse=True)
    released_before = (date.today() - timedelta(days=MIN_AGE_DAYS)).isoformat()
    return [best] + [r for r in recs if _eligible(r, released_before)][:TITLE_RECS]


def _person_candidates(tmdb: TMDBClient, name: str) -> list[dict]:
    """The person's movies, most-voted first: the ones they're known for (Q8).

    - Directors ("Directing"): movies they directed. The blob has a "Directed by" line (D-024).
    - Everyone else: acting roles billed in the top BLOB_CAST only. The blob lists just
      that many names, so a cameo (Brad Pitt in Deadpool 2, billed 20th) wouldn't match
      "Brad Pitt" anyway.
    """
    people = tmdb.search_person(name)
    if not people:
        return []
    person = people[0]
    credits = tmdb.movie_credits(person["id"])
    if person.get("known_for_department") == "Directing":
        movies = [c for c in credits.get("crew") or [] if c.get("job") == "Director"]
    else:
        movies = [
            c for c in credits.get("cast") or []
            if (c.get("order") if c.get("order") is not None else 99) < BLOB_CAST
        ]
    return sorted(movies, key=lambda m: m.get("vote_count") or 0, reverse=True)


def _mood_candidates(tmdb: TMDBClient, intent: QueryIntent) -> list[dict]:
    """Most-voted films for a mood: any of its keywords, within any of its genres.
    Falls back to the genres alone when no keyword is known to TMDB (D-025)."""
    keyword_ids = []
    for word in intent.keywords:
        results = tmdb.search_keyword(word)
        # exact name first ("tearjerker"), else TMDB's best guess
        match = next((r for r in results if r["name"].lower() == word), results[0] if results else None)
        if match:
            keyword_ids.append(str(match["id"]))
    genre_ids = [str(GENRE_IDS[g]) for g in intent.genres]
    floor = {"vote_count.gte": MIN_VOTES}
    candidates = []
    if keyword_ids:
        # "|" = OR in TMDB filters: any keyword, any genre
        genres = {"with_genres": "|".join(genre_ids)} if genre_ids else {}
        candidates += tmdb.discover(with_keywords="|".join(keyword_ids), **genres, **floor)
    if len(candidates) < MAX_NEW and genre_ids:
        candidates += tmdb.discover(with_genres=",".join(genre_ids), **floor)  # "," = AND
    log.info("mood %s + %s -> %d candidates", intent.genres, intent.keywords, len(candidates))
    return candidates


def _eligible(item: dict, released_before: str) -> bool:
    """Same bar as the bulk fetch: well-voted, settled, not adult."""
    release = item.get("release_date") or ""
    return (
        not item.get("adult")
        and (item.get("vote_count") or 0) >= MIN_VOTES
        and bool(release)
        and release <= released_before  # ISO dates compare correctly as strings
    )


def _add_ratings(movies: list[Movie]) -> None:
    """IMDb ratings from OMDb. On the daily limit, the rest simply get None."""
    if not movies:
        return
    omdb = OMDbClient()
    for movie in movies:
        if not movie.imdb_id:
            continue
        try:
            movie.imdb_rating = parse_rating(omdb.fetch(movie.imdb_id))
        except OMDbLimitReached:
            log.warning("OMDb daily limit reached, lazy-ingested movies get no rating")
            return
        except OMDbError as e:
            log.warning("no rating for %s: %s", movie.title, e)
