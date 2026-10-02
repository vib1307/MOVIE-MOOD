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

    def _discover_mix(self, queries: dict[str, dict], want: int, exclude: set[int] = frozenset()) -> list[int]:
        """Round-robin across several discover queries until `want` unique ids.

        Genres overlap (a romcom is in both Comedy and Romance), so asking each query for
        want/n ids came up short after dedup. Ask for 1.5x more each round until there are
        enough, or every query has run out of results (D-025).
        """
        per = -(-want // len(queries))  # ceiling division
        while True:
            lists = [self._discover(per, **filters) for filters in queries.values()]
            mixed = _round_robin(lists, exclude=exclude)
            if len(mixed) >= want or all(len(ids) < per for ids in lists):
                break
            log.info("discover: %d unique of %d wanted, asking each query for more", len(mixed), want)
            per = -(-per * 3 // 2)
        for name, ids in zip(queries, lists):
            log.info("discover %s: %d ids", name, len(ids))
        if len(mixed) < want:
            log.warning("discover: only %d unique ids available (wanted %d)", len(mixed), want)
        return mixed[:want]

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

        common = {"primary_release_date.lte": released_before}
        main = self._discover_mix(
            {name: {"with_genres": gid, "vote_count.gte": min_votes, **common} for name, gid in genres.items()},
            n_main,
        )
        indian: list[int] = []
        if n_indian:
            indian = self._discover_mix(
                {f"language={lang}": {"with_original_language": lang, "vote_count.gte": indian_min_votes, **common}
                 for lang in indian_languages},
                n_indian,
                exclude=set(main),
            )

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

    # Live lookups for lazy ingestion (D-023). Not cached: results change over time,
    # and they only run on a catalog miss. Each returns TMDB's short movie/person dicts.

    def search_person(self, name: str) -> list[dict]:
        return self._get("/search/person", query=name, include_adult="false")["results"]

    def movie_credits(self, person_id: int) -> dict:
        """{"cast": [...], "crew": [...]}; crew items have a "job", e.g. "Director"."""
        return self._get(f"/person/{person_id}/movie_credits", language="en-US")

    def search_movie(self, title: str) -> list[dict]:
        return self._get("/search/movie", query=title, include_adult="false", language="en-US")["results"]

    def search_keyword(self, name: str) -> list[dict]:
        """[{"id": 10683, "name": "tearjerker"}, ...]: TMDB keyword ids for discover."""
        return self._get("/search/keyword", query=name)["results"]

    def discover(self, **filters) -> list[dict]:
        """One page (20) of /discover/movie, most-voted first; filters as TMDB names them."""
        return self._get(
            "/discover/movie", sort_by="vote_count.desc", include_adult="false", language="en-US", **filters
        )["results"]

    def recommendations(self, tmdb_id: int) -> list[dict]:
        """TMDB's "if you liked this" list for a movie."""
        return self._get(f"/movie/{tmdb_id}/recommendations", language="en-US")["results"]

    def watch_providers(self, tmdb_id: int) -> dict:
        """Where the movie streams, per country (JustWatch data, D-036).

        {"IN": {"link": ..., "flatrate": [{"provider_name": "Netflix", ...}], "buy": [...]},
         "US": {...}, ...} - every country in one response, ~130 of them.
        Not cached here: core/availability.py keeps its own trimmed cache.
        """
        return self._get(f"/movie/{tmdb_id}/watch/providers")["results"]

    def watch_regions(self) -> list[dict]:
        """[{"iso_3166_1": "IN", "english_name": "India", ...}, ...] for the region picker."""
        return self._get("/watch/providers/regions", language="en-US")["results"]


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
        directors=[c["name"] for c in credits.get("crew") or [] if c.get("job") == "Director"],
        poster_path=details.get("poster_path"),
        imdb_id=imdb_id or None,
        original_language=details.get("original_language") or None,
    )
