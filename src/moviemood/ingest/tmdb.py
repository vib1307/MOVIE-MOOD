"""TMDB client: discover movie ids by genre, fetch details, normalize to Movie."""

import json
import logging
from datetime import date, timedelta
from itertools import chain, zip_longest
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from moviemood.config import get_settings
from moviemood.core.models import Movie

log = logging.getLogger(__name__)

BASE_URL = "https://api.themoviedb.org/3"
TOP_CAST = 5
TIMEOUT = 10  # seconds per request

# TMDB genre ids (stable; full list at GET /genre/movie/list)
DEFAULT_GENRES = {
    "Comedy": 35,
    "Drama": 18,
    "Romance": 10749,
    "Thriller": 53,
    "Science Fiction": 878,
    "Animation": 16,
    "Horror": 27,
    "Adventure": 12,
}


class TMDBError(RuntimeError):
    pass


class TMDBClient:
    def __init__(self, cache_dir: Path | None = None):
        settings = get_settings()
        self._key = settings.tmdb_api_key.get_secret_value()
        self.cache_dir = cache_dir or settings.data_dir / "cache" / "tmdb"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = self._make_session()

    def _make_session(self) -> requests.Session:
        retry = Retry(
            total=5,
            backoff_factor=1,  # waits 1s, 2s, 4s... between retries
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
            respect_retry_after_header=True,
        )
        session = requests.Session()
        session.mount("https://", HTTPAdapter(max_retries=retry))
        # v4 "Read Access Token" is a JWT (starts with eyJ) and goes in a header;
        # a v3 "API key" goes in the query string.
        if self._key.startswith("eyJ"):
            session.headers["Authorization"] = f"Bearer {self._key}"
        else:
            session.params = {"api_key": self._key}
        return session

    def _get(self, path: str, **params) -> dict:
        try:
            resp = self.session.get(f"{BASE_URL}{path}", params=params, timeout=TIMEOUT)
            resp.raise_for_status()
        except requests.RequestException as e:
            # Don't re-raise the original: its message contains the full URL,
            # which includes the api_key.
            status = getattr(e.response, "status_code", None)
            raise TMDBError(f"GET {path} failed (status={status}, {type(e).__name__})") from None
        return resp.json()

    def discover_ids(
        self,
        limit: int,
        genres: dict[str, int] = DEFAULT_GENRES,
        min_votes: int = 300,
        min_age_days: int = 90,
    ) -> list[int]:
        """Popular, well-voted movie ids, interleaved across genres for variety.

        Movies released in the last `min_age_days` are skipped: their votes and
        IMDb ratings haven't settled yet.
        """
        released_before = (date.today() - timedelta(days=min_age_days)).isoformat()
        per_genre = -(-limit // len(genres))  # ceiling division
        lists: list[list[int]] = []
        for name, genre_id in genres.items():
            ids: list[int] = []
            page = 1
            while len(ids) < per_genre:
                data = self._get(
                    "/discover/movie",
                    with_genres=genre_id,
                    sort_by="popularity.desc",
                    **{
                        "vote_count.gte": min_votes,
                        "primary_release_date.lte": released_before,
                    },
                    include_adult="false",
                    language="en-US",
                    page=page,
                )
                ids += [m["id"] for m in data["results"]]
                if page >= data["total_pages"]:
                    break
                page += 1
            log.info("discover %s: %d ids", name, len(ids))
            lists.append(ids)

        # Round-robin: comedy[0], drama[0], ..., comedy[1], drama[1], ...
        seen: set[int] = set()
        result: list[int] = []
        for tmdb_id in chain.from_iterable(zip_longest(*lists)):
            if tmdb_id is not None and tmdb_id not in seen:
                seen.add(tmdb_id)
                result.append(tmdb_id)
        return result[:limit]

    def details(self, tmdb_id: int) -> dict:
        """Raw details + keywords + credits + external_ids, cached on disk."""
        cache_file = self.cache_dir / f"{tmdb_id}.json"
        if cache_file.exists():
            return json.loads(cache_file.read_text())
        data = self._get(
            f"/movie/{tmdb_id}",
            append_to_response="keywords,credits,external_ids",
            language="en-US",
        )
        cache_file.write_text(json.dumps(data))
        return data


def to_movie(details: dict) -> Movie:
    """Normalize a raw TMDB details response. imdb_rating is filled in later from OMDb."""
    release = details.get("release_date") or ""
    cast = sorted(details.get("credits", {}).get("cast", []), key=lambda c: c["order"])
    imdb_id = details.get("imdb_id") or details.get("external_ids", {}).get("imdb_id")
    return Movie(
        tmdb_id=details["id"],
        title=details["title"],
        overview=details.get("overview") or "",
        year=int(release[:4]) if release[:4].isdigit() else None,
        runtime=details.get("runtime") or None,  # 0 means unknown
        genres=[g["name"] for g in details.get("genres", [])],
        keywords=[k["name"] for k in details.get("keywords", {}).get("keywords", [])],
        top_cast=[c["name"] for c in cast[:TOP_CAST]],
        poster_path=details.get("poster_path"),
        imdb_id=imdb_id or None,
    )
