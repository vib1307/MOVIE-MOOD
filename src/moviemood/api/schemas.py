"""API contract (request/response shapes). Kept separate from core models so core can
change internally without breaking API clients. See D-019."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from moviemood.core.models import Movie, Recommendation

POSTER_BASE_URL = "https://image.tmdb.org/t/p/w500"


def poster_url(poster_path: str | None) -> str | None:
    """'/abc.jpg' -> 'https://image.tmdb.org/t/p/w500/abc.jpg'"""
    return f"{POSTER_BASE_URL}{poster_path}" if poster_path else None


class RecommendRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)  # "  cozy  " -> "cozy" before the length check

    query: str = Field(min_length=3, max_length=300, examples=["feel-good, no sad ending"])
    k: int = Field(default=5, ge=1, le=10, description="how many movies to return")
    rerank: bool = Field(default=True, description="false = fast mode: retrieval only, no LLM (<1s)")


class MovieResult(BaseModel):
    tmdb_id: int
    title: str
    year: int | None
    runtime: int | None
    poster_url: str | None
    imdb_rating: float | None
    why: str

    @classmethod
    def from_recommendation(cls, rec: Recommendation) -> "MovieResult":
        # distance and source are internal: not part of the API contract
        return cls(**rec.model_dump(include=set(cls.model_fields) - {"poster_url"}),
                   poster_url=poster_url(rec.poster_path))


class RecommendResponse(BaseModel):
    query: str
    results: list[MovieResult]


class MovieDetail(Movie):
    poster_url: str | None

    @classmethod
    def from_movie(cls, movie: Movie) -> "MovieDetail":
        return cls(**movie.model_dump(), poster_url=poster_url(movie.poster_path))


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    ollama: bool
    movies_indexed: int
