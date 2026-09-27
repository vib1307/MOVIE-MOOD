"""Full movie details by id, loaded from data/movies.json. See D-016.

Chroma finds movies; the catalog provides everything else about them.
"""

import json
from functools import lru_cache

from moviemood.config import get_settings
from moviemood.core.models import Movie


@lru_cache
def get_catalog() -> dict[int, Movie]:
    """{tmdb_id: Movie}, loaded once per process. Call get_catalog.cache_clear() after a refetch."""
    raw = json.loads(get_settings().movies_json.read_text())
    return {m.tmdb_id: m for m in (Movie.model_validate(r) for r in raw)}
