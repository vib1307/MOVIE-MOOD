"""TMDB client: discover movie ids by genre, fetch details, normalize to Movie."""

import logging
from datetime import date, timedelta
from itertools import chain, zip_longest
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from moviemood.config import get_settings
from moviemood.core.models import Movie
from moviemood.ingest.cache import read_json, write_json
from moviemood.ingest.http import install_log_redaction

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

# Hindi, Tamil, Telugu (ISO 639-1 codes, as TMDB's original_language uses)
INDIAN_LANGUAGES = ("hi", "ta", "te")


class TMDBError(RuntimeError):
    pass


class TMDBClient:
    def __init__(self, cache_dir: Path | None = None):
        settings = get_settings()
        self._key = settings.tmdb_api_key.get_secret_value()
        self.cache_dir = cache_dir or settings.data_dir / "cache" / "tmdb"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        install_log_redaction()
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
        try:
            return resp.json()
        except ValueError:  # e.g. an HTML error page from a proxy with status 200
            raise TMDBError(f"GET {path} returned invalid JSON") from None

    def _discover(self, want: int, **filters) -> list[int]:
        """Page through /discover/movie (popularity order) until `want` ids are collected."""
        ids: list[int] = []
        page = 1
        while len(ids) < want:
            data = self._get(
                "/discover/movie",
                sort_by="popularity.desc",
                include_adult="false",
                language="en-US",
                page=page,
                **filters,
            )
            ids += [m["id"] for m in data["results"]]
            if page >= data["total_pages"]:
                break
            page += 1
        return ids

    def discover_ids(
        self,
        limit: int,
        genres: dict[str, int] = DEFAULT_GENRES,
        min_votes: int = 300,
        min_age_days: int = 90,
        indian_share: float = 0.25,
        indian_languages: tuple[str, ...] = INDIAN_LANGUAGES,
        indian_min_votes: int = 100,
    ) -> list[int]:
        """Popular, well-voted movie ids: a genre mix plus a slice of Indian films.

        - Main slice: round-robin across `genres` so no single genre dominates.
        - Indian slice (`indian_share` of `limit`): round-robin across `indian_languages`,
          with a lower vote floor because Indian films get fewer TMDB votes.
        - Movies released in the last `min_age_days` are skipped: their votes and
          IMDb ratings haven't settled yet.
        """
        released_before = (date.today() - timedelta(days=min_age_days)).isoformat()
        n_indian = round(limit * indian_share) if indian_languages else 0
        n_main = limit - n_indian

        per_genre = -(-n_main // len(genres))  # ceiling division
        genre_lists = []
        for name, genre_id in genres.items():
            ids = self._discover(
                per_genre,
                with_genres=genre_id,
                **{"vote_count.gte": min_votes, "primary_release_date.lte": released_before},
            )
            log.info("discover %s: %d ids", name, len(ids))
            genre_lists.append(ids)
        main = _round_robin(genre_lists)[:n_main]

        indian: list[int] = []
        if n_indian:
            per_lang = -(-n_indian // len(indian_languages))
            lang_lists = []
            for lang in indian_languages:
                ids = self._discover(
                    per_lang,
                    with_original_language=lang,
                    **{"vote_count.gte": indian_min_votes, "primary_release_date.lte": released_before},
                )
                log.info("discover language=%s: %d ids", lang, len(ids))
                lang_lists.append(ids)
            indian = _round_robin(lang_lists, exclude=set(main))[:n_indian]

        return _spread(main, indian)


    def details(self, tmdb_id: int) -> dict:
        """Raw details + keywords + credits + external_ids, cached on disk."""
        cache_file = self.cache_dir / f"{tmdb_id}.json"
        if (cached := read_json(cache_file)) is not None:
            return cached
        data = self._get(
            f"/movie/{tmdb_id}",
            append_to_response="keywords,credits,external_ids",
            language="en-US",
        )
        write_json(cache_file, data)
        return data


def _round_robin(lists: list[list[int]], exclude: set[int] = frozenset()) -> list[int]:
    """Take one id from each list in turn, skipping duplicates.

    [[c1, c2], [d1, d2]] -> [c1, d1, c2, d2]
    """
    seen = set(exclude)
    result: list[int] = []
    for tmdb_id in chain.from_iterable(zip_longest(*lists)):
        if tmdb_id is not None and tmdb_id not in seen:
            seen.add(tmdb_id)
            result.append(tmdb_id)
    return result


def _spread(main: list[int], extra: list[int]) -> list[int]:
    """Mix `extra` evenly through `main`, so any prefix of the result keeps the ratio.

    main=[m1..m6], extra=[e1, e2] -> [m1, e1, m2, m3, m4, e2, m5, m6] (roughly)
    """
    keyed = [((i + 0.5) / len(main), x) for i, x in enumerate(main)]
    keyed += [((j + 0.5) / len(extra), x) for j, x in enumerate(extra)]
    return [x for _, x in sorted(keyed, key=lambda pair: pair[0])]


def to_movie(details: dict) -> Movie:
    """Normalize a raw TMDB details response. imdb_rating is filled in later from OMDb."""
    release = details.get("release_date") or ""
    # `or {}` / `or []` (not .get(key, {})) also covers keys that are present but null
    credits = details.get("credits") or {}
    cast = sorted(credits.get("cast") or [], key=lambda c: c.get("order", 999))
    external = details.get("external_ids") or {}
    imdb_id = details.get("imdb_id") or external.get("imdb_id")
    keywords = (details.get("keywords") or {}).get("keywords") or []
    return Movie(
        tmdb_id=details["id"],
        title=details["title"],
        overview=details.get("overview") or "",
        year=int(release[:4]) if release[:4].isdigit() else None,
        runtime=details.get("runtime") or None,  # 0 means unknown
        genres=[g["name"] for g in details.get("genres") or []],
        keywords=[k["name"] for k in keywords],
        top_cast=[c["name"] for c in cast[:TOP_CAST]],
        poster_path=details.get("poster_path"),
        imdb_id=imdb_id or None,
        original_language=details.get("original_language") or None,
    )
