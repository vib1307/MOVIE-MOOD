from typing import Literal

from pydantic import BaseModel, Field


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
    source: Literal["llm", "fallback"]  # who picked it: the LLM re-rank or plain retrieval order
    distance: float  # cosine distance from the query (lower = closer)
