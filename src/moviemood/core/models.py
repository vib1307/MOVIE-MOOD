from typing import Literal

from pydantic import BaseModel, Field

POSTER_BASE_URL = "https://image.tmdb.org/t/p/w500"


def poster_url(poster_path: str | None) -> str | None:
    """'/abc.jpg' -> 'https://image.tmdb.org/t/p/w500/abc.jpg'. Shared by the API and UI."""
    return f"{POSTER_BASE_URL}{poster_path}" if poster_path else None


class Movie(BaseModel):
    """One normalized movie record: TMDB details + OMDb rating."""

    tmdb_id: int
    title: str
    overview: str = ""
    year: int | None = None  # some movies have an empty release_date
    runtime: int | None = None  # minutes; TMDB sometimes returns 0 or null
    genres: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    top_cast: list[str] = Field(default_factory=list)
    directors: list[str] = Field(default_factory=list)  # usually one
    poster_path: str | None = None  # e.g. "/abc.jpg"; prefix with the image base URL to display
    imdb_id: str | None = None
    imdb_rating: float | None = None  # None when OMDb has no rating ("N/A")
    original_language: str | None = None  # ISO 639-1, e.g. "en", "hi", "ta"


class Recommendation(BaseModel):
    """One recommended movie, ready for the API and UI to display."""

    tmdb_id: int
    title: str
    year: int | None = None
    runtime: int | None = None
    poster_path: str | None = None
    imdb_rating: float | None = None
    why: str
    # llm: LLM said it fits (and wrote the why); fallback: retrieval order, template why;
    # demoted: LLM said it doesn't fit, so it was moved below the ones that do
    source: Literal["llm", "fallback", "demoted"]
    distance: float  # cosine distance from the query (lower = closer)
    overview: str = ""
    # "Why this pick" panel (core/explain.py, D-024); empty until explain() runs
    named: list[str] = Field(default_factory=list)  # e.g. ["Starring Brad Pitt"]
    mood_tags: list[str] = Field(default_factory=list)  # the movie's tags closest to the query
    # Subscription providers in the region asked for (core/availability.py, D-036).
    # None = not looked up (so the UI shows nothing); [] = looked up, not streaming there.
    where_to_watch: list[str] | None = None
