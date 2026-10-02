"""API contract (request/response shapes). Kept separate from core models so core can
change internally without breaking API clients. See D-019."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from moviemood.core.availability import DEFAULT_REGION
from moviemood.core.models import Movie, Recommendation, poster_url


class RecommendRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)  # "  cozy  " -> "cozy" before the length check

    query: str = Field(min_length=3, max_length=300, examples=["feel-good, no sad ending"])
    k: int = Field(default=10, ge=1, le=20, description="how many movies to return")
    rerank: bool = Field(default=True, description="false = fast mode: retrieval only, no LLM (<1s)")
    region: str = Field(
        default=DEFAULT_REGION,
        pattern="^[A-Za-z]{2}$",
        description="ISO 3166-1 country for streaming availability, e.g. IN, US",
        examples=["IN"],
    )


class MovieResult(BaseModel):
    tmdb_id: int
    title: str
    year: int | None
    runtime: int | None
    poster_url: str | None
    imdb_rating: float | None
    why: str
    # Subscription providers in the requested region; [] = not streaming there (D-036)
    where_to_watch: list[str] | None = None

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
