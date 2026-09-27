"""Fetch movies from TMDB + OMDb and write data/movies.json.

Usage:
    python scripts/fetch_movies.py              # 20 movies (Sprint A)
    python scripts/fetch_movies.py --limit 500
    python scripts/fetch_movies.py --refresh    # re-run discover for a new movie list

Safe to re-run: the chosen id list and raw API responses are cached in
data/cache/, so re-runs use the same movies and only new ones hit the network.
movies.json is rebuilt from the caches each run. Movies added at runtime by lazy
ingestion (data/added_ids.json) are kept too (D-024).
"""

import argparse
import logging
from datetime import date

from pydantic import ValidationError

from moviemood.config import get_settings
from moviemood.core.catalog import added_ids
from moviemood.core.models import Movie
from moviemood.ingest.cache import read_json, write_json
from moviemood.ingest.omdb import OMDbClient, OMDbError, OMDbLimitReached, parse_rating
from moviemood.ingest.tmdb import TMDBClient, TMDBError, to_movie

log = logging.getLogger("fetch_movies")


def load_or_discover(tmdb: TMDBClient, limit: int, refresh: bool) -> list[int]:
    """Reuse the saved id list so re-runs fetch the same movies.

    TMDB popularity changes daily and the release cutoff moves with today's date,
    so calling discover on every run would give a different list each time.
    """
    ids_file = get_settings().data_dir / "cache" / "discover_ids.json"
    saved = None if refresh else read_json(ids_file)
    # Compare against the limit that was *requested*, not len(ids): after dedup,
    # discover can return fewer ids than asked, and that must not trigger a rediscover.
    if saved and saved.get("limit", 0) >= limit:
        log.info("using saved id list from %s (--refresh for a new one)", saved["created"])
        return saved["ids"][:limit]

    ids = tmdb.discover_ids(limit=limit)
    write_json(ids_file, {"created": date.today().isoformat(), "limit": limit, "ids": ids})
    return ids


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=20, help="how many movies (default 20)")
    parser.add_argument("--refresh", action="store_true", help="ignore the saved id list, re-run discover")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    tmdb = TMDBClient()
    omdb = OMDbClient()

    ids = load_or_discover(tmdb, args.limit, args.refresh)
    log.info("discovered %d ids (asked for %d)", len(ids), args.limit)
    extra = [i for i in added_ids() if i not in set(ids)]
    if extra:
        log.info("keeping %d movies added by lazy ingestion", len(extra))
        ids += extra

    movies: list[Movie] = []
    failed: list[int] = []
    omdb_blocked = False  # set once the daily limit is hit

    for n, tmdb_id in enumerate(ids, start=1):
        try:
            movie = to_movie(tmdb.details(tmdb_id))
        except (TMDBError, KeyError, ValidationError) as e:
            # KeyError / ValidationError: TMDB returned a record missing required fields.
            log.warning("skip %d: %s: %s", tmdb_id, type(e).__name__, e)
            failed.append(tmdb_id)
            continue

        if movie.imdb_id and not omdb_blocked:
            try:
                movie.imdb_rating = parse_rating(omdb.fetch(movie.imdb_id))
            except OMDbLimitReached:
                # Keep going without ratings; a re-run tomorrow fills them in
                # (TMDB responses are cached, so that re-run is cheap).
                log.warning("OMDb daily limit reached, remaining movies get no rating")
                omdb_blocked = True
            except OMDbError as e:
                log.warning("no rating for %s: %s", movie.title, e)

        movies.append(movie)
        log.info("[%d/%d] %s (%s) imdb=%s", n, len(ids), movie.title, movie.year, movie.imdb_rating)

    out = get_settings().movies_json
    write_json(out, [m.model_dump() for m in movies], indent=2, ensure_ascii=False)

    rated = sum(m.imdb_rating is not None for m in movies)
    log.info("wrote %d movies to %s (%d with imdb_rating, %d failed)", len(movies), out, rated, len(failed))
    if omdb_blocked:
        log.info("re-run tomorrow to fill in the missing ratings")


if __name__ == "__main__":
    main()
