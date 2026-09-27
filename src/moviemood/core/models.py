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
