"""Fetch movies from TMDB + OMDb and write data/movies.json.

Usage:
    python scripts/fetch_movies.py              # 20 movies (Sprint A)
    python scripts/fetch_movies.py --limit 500

Safe to re-run: raw API responses are cached in data/cache/, so only new
movies hit the network. movies.json is rebuilt from scratch each run.
"""

import argparse
import json
import logging

from moviemood.config import get_settings
from moviemood.core.models import Movie
from moviemood.ingest.omdb import OMDbClient, OMDbError, OMDbLimitReached, parse_rating
from moviemood.ingest.tmdb import TMDBClient, TMDBError, to_movie

log = logging.getLogger("fetch_movies")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=20, help="how many movies (default 20)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    tmdb = TMDBClient()
    omdb = OMDbClient()

    ids = tmdb.discover_ids(limit=args.limit)
    log.info("discovered %d ids (asked for %d)", len(ids), args.limit)

    movies: list[Movie] = []
    failed: list[int] = []
    omdb_blocked = False  # set once the daily limit is hit

    for n, tmdb_id in enumerate(ids, start=1):
        try:
            movie = to_movie(tmdb.details(tmdb_id))
        except TMDBError as e:
            log.warning("skip %d: %s", tmdb_id, e)
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
    out.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temp file, then rename: a crash mid-write never leaves a half-written movies.json.
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps([m.model_dump() for m in movies], indent=2, ensure_ascii=False))
    tmp.replace(out)

    rated = sum(m.imdb_rating is not None for m in movies)
    log.info("wrote %d movies to %s (%d with imdb_rating, %d failed)", len(movies), out, rated, len(failed))
    if omdb_blocked:
        log.info("re-run tomorrow to fill in the missing ratings")


if __name__ == "__main__":
    main()
