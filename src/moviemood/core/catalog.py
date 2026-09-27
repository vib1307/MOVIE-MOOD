"""Full movie details by id, loaded from data/movies.json. See D-016.

Chroma finds movies; the catalog provides everything else about them.
"""

import json
from functools import lru_cache

from moviemood.config import get_settings
from moviemood.core.models import Movie
from moviemood.ingest.cache import read_json, write_json


@lru_cache
def get_catalog() -> dict[int, Movie]:
    """{tmdb_id: Movie}, loaded once per process. Call get_catalog.cache_clear() after a refetch."""
    raw = json.loads(get_settings().movies_json.read_text())
    return {m.tmdb_id: m for m in (Movie.model_validate(r) for r in raw)}


def added_ids() -> list[int]:
    """Ids added at runtime (lazy ingestion). fetch_movies.py keeps these on a refetch."""
    return read_json(get_settings().added_ids_json) or []


def add_to_catalog(movies: list[Movie]) -> None:
    """Add movies to the in-memory catalog, rewrite movies.json (atomic write), and
    record their ids in added_ids.json so a refetch doesn't drop them (D-024).

    Not thread-safe on its own: lazy_ingest holds a lock around it.
    """
    catalog = get_catalog()  # the cached dict itself, so every caller sees the new movies
    for movie in movies:
        catalog[movie.tmdb_id] = movie
    write_json(
        get_settings().movies_json,
        [m.model_dump() for m in catalog.values()],
        indent=2,
        ensure_ascii=False,
    )
    known = added_ids()
    write_json(get_settings().added_ids_json, known + [m.tmdb_id for m in movies if m.tmdb_id not in known])
