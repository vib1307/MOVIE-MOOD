"""Fill data/providers.json with streaming availability for the whole catalog. See D-036.

Usage:
    python scripts/fetch_providers.py            # new movies + entries older than 7 days
    python scripts/fetch_providers.py --all      # refetch everything
    python scripts/fetch_providers.py --limit 50 # just the first 50 (a quick try)

Safe to re-run and safe to interrupt: progress is written every CHUNK movies. Two
requests at a time on purpose - TMDB resets the connection on a bigger pool, and the
retry backoff then makes it slower than going almost serially (D-036).

Re-run it weekly: licences expire, so availability drifts.
Provider names are cleaned when they are fetched, not when they are read, so after a
change to clean_names() re-run with --all.
"""

import argparse
import logging
import time

from moviemood.core.availability import TTL_DAYS, cache, fetch_many, is_fresh, save_entries
from moviemood.core.catalog import get_catalog
from moviemood.config import get_settings

log = logging.getLogger("fetch_providers")

CHUNK = 50  # movies per save, so Ctrl-C keeps progress


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help=f"refetch fresh entries too (< {TTL_DAYS} days old)")
    parser.add_argument("--limit", type=int, help="only the first N catalog movies")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    catalog = get_catalog()
    ids = list(catalog)[: args.limit]
    cached = cache()
    todo = ids if args.all else [i for i in ids if not is_fresh(cached.get(str(i)))]
    log.info("catalog: %d movies, %d to fetch, %d already fresh", len(ids), len(todo), len(ids) - len(todo))
    if not todo:
        return

    started = time.perf_counter()
    done = 0
    for start in range(0, len(todo), CHUNK):
        batch = todo[start : start + CHUNK]
        # save=False: one write per chunk instead of one per movie
        entries = fetch_many(batch, save=False)
        save_entries(entries)
        done += len(entries)
        log.info("%d/%d fetched (%.0fs elapsed)", done, len(todo), time.perf_counter() - started)

    streaming = sum(1 for i in todo if (cached.get(str(i), {}).get("flatrate") or {}).get("IN"))
    log.info(
        "done: %d entries in %s (%.0fs). %d of %d stream in India.",
        len(cache()), get_settings().providers_json.name, time.perf_counter() - started, streaming, len(todo),
    )


if __name__ == "__main__":
    main()
